# ============================================================
#  infrastructure/diagram/standalone_tex_renderer.py
#  Standalone LaTeX Diagram Compiler & Vector SVG Generator
# ============================================================

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Optional, Sequence, Union

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

from infrastructure.pdf.tex_log_sanitizer import TexLogSanitizer

from application.ports.diagram_renderer import (
    DiagramCancelledError,
    DiagramOutputLimitError,
    DiagramRenderError,
    DiagramRenderRequest,
    DiagramRenderResult,
    DiagramRendererUnavailableError,
    DiagramSyntaxError,
    DiagramTimeoutError,
    IDiagramRenderer,
)
from infrastructure.diagram.cache import DiagramSvgCache

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS: float = 10.0
_MAX_SVG_BYTES: int = 10 * 1024 * 1024  # 10 MB maximum
_MAX_DIMENSION_POINTS: float = 5000.0   # 5000 pt max width/height

_STANDALONE_TEMPLATE = r"""\documentclass[tikz,border=2pt]{standalone}
\usepackage{amsmath,amssymb}
\usepackage{tikz}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usepackage{circuitikz}
\usepackage{tikz-cd}
__PREAMBLE__
\begin{document}
__SOURCE__
\end{document}
"""

_LATEX_ERROR_PATTERN = re.compile(
    r"^!\s*(.*?)(?:\nl\.(?P<line>\d+)\s*(.*?))?$",
    re.MULTILINE,
)

_SENSITIVE_ENV_PATTERN = re.compile(
    r"(API|KEY|SECRET|TOKEN|AUTH|CREDENTIAL|PASS|POLPO|GEMINI|OPENAI|AWS|GITHUB)",
    re.IGNORECASE,
)

_ALLOWED_ENV_PREFIXES = (
    "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
    "TEMP", "TMP", "HOME", "USER", "USERNAME", "LOGNAME",
    "LANG", "LC_", "SHELL", "TERM",
    "TEXMF", "FONTCONFIG", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
)


def _build_sandboxed_environment(workdir: Path) -> dict[str, str]:
    """Builds a sanitized, isolated execution environment for untrusted LaTeX compilation.

    Enforces Kpathsea paranoid file-access restrictions, disables shell escapes,
    pins output paths to the isolated temporary workspace, and strips any sensitive
    credential-bearing environment variables.
    """
    env: dict[str, str] = {}

    for k, v in os.environ.items():
        k_upper = k.upper()
        if _SENSITIVE_ENV_PATTERN.search(k_upper):
            continue
        if any(k_upper == prefix or k_upper.startswith(prefix) for prefix in _ALLOWED_ENV_PREFIXES):
            env[k] = v

    # Security invariants:
    # 1. Paranoid file opening restrictions (prevent \input, \openin from traversing to .. or absolute paths)
    env["openin_any"] = "p"
    # 2. Paranoid file writing restrictions (prevent \openout, \write from writing outside TEXMFOUTPUT)
    env["openout_any"] = "p"
    # 3. Disable shell escape at configuration level
    env["shell_escape"] = "f"
    # 4. Pin TEXMFOUTPUT to the isolated compilation workspace
    env["TEXMFOUTPUT"] = str(workdir)

    return env


def _extract_sanitized_latex_error(log_content: str, stderr: str) -> str:
    """Extracts a concise, actionable error snippet from LaTeX log/stderr without machine path leakage."""
    combined = f"{stderr}\n{log_content}"
    error_lines: list[str] = []

    for line in combined.splitlines():
        line_clean = line.strip()
        if line_clean.startswith("!"):
            # Strip machine-specific file paths
            sanitized = re.sub(r"(/[\w./-]+|\\[\w.\\-]+)", "<file>", line_clean)
            error_lines.append(sanitized)
        elif line_clean.startswith("l.") and error_lines:
            error_lines.append(line_clean)
            break

    if error_lines:
        return "\n".join(error_lines[:3])

    if stderr.strip():
        first_stderr = [l for l in stderr.splitlines() if l.strip()]
        if first_stderr:
            return first_stderr[0][:200]

    return "LaTeX diagram compilation failed with unknown error."


def _parse_latex_diagnostics(
    log_content: str,
    stderr: str,
    source: str,
    preamble: str,
    returncode: Optional[int],
) -> DiagramSyntaxError:
    """Parses raw LaTeX compiler log/stderr and computes fragment-relative line numbers."""
    combined = f"{stderr}\n{log_content}"
    sanitizer = TexLogSanitizer()
    diag = sanitizer.sanitize(combined)

    # Calculate wrapper line offset
    header_prefix = _STANDALONE_TEMPLATE.split("__SOURCE__")[0]
    header_tex = header_prefix.replace("__PREAMBLE__", preamble or "")
    header_lines = header_tex.count("\n")

    tex_line = diag.line_number
    source_line: Optional[int] = None
    if tex_line is not None:
        candidate = tex_line - header_lines
        num_source_lines = source.count("\n") + 1
        if 1 <= candidate <= num_source_lines:
            source_line = candidate

    # Bound and sanitize log excerpt to at most 2000 chars
    raw_excerpt = diag.raw_error_snippet or diag.sanitized_message
    excerpt = sanitizer._redact_sensitive_info(raw_excerpt)
    if len(excerpt) > 2000:
        excerpt = excerpt[:2000]

    source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()

    # Determine error category
    err_cat = "syntax"
    if diag.missing_packages:
        err_cat = "missing_package"
    elif diag.missing_fonts:
        err_cat = "missing_font"
    elif "undefined control sequence" in diag.sanitized_message.lower():
        err_cat = "undefined_control_sequence"
    elif "environment" in diag.sanitized_message.lower():
        err_cat = "environment"

    return DiagramSyntaxError(
        code="syntax",
        message=diag.sanitized_message,
        returncode=returncode,
        tex_line=tex_line,
        source_line=source_line,
        log_excerpt=excerpt,
        source_sha256=source_sha256,
        error_category=err_cat,
    )


class StandaloneTeXDiagramRenderer(IDiagramRenderer):
    """Compiles LaTeX diagram environments into vector SVG using host TeX engine and PyMuPDF.

    Security & Isolation Guarantees:
      - Uses isolated, ephemeral temporary directory per compilation.
      - Enforces -no-shell-escape to prevent arbitrary OS command execution.
      - Uses subprocess argument arrays (never shell interpolation).
      - Enforces strict process timeouts and terminates whole process trees.
      - Employs PyMuPDF to extract crisp, self-contained vector SVG XML without external converters.
    """

    def __init__(
        self,
        cache: Optional[DiagramSvgCache] = None,
        compiler_path: Optional[str] = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.cache = cache if cache is not None else DiagramSvgCache()
        self._explicit_compiler = compiler_path
        self._timeout_seconds = max(1.0, timeout_seconds)

    def resolve_compiler_path(self) -> Optional[str]:
        """Resolves the best available TeX compiler executable (preferring xelatex, fallback pdflatex)."""
        if self._explicit_compiler is not None:
            p = Path(self._explicit_compiler).expanduser().resolve()
            if p.is_file():
                return str(p)
            return None

        # 1. Prefer xelatex
        xe = shutil.which("xelatex")
        if xe:
            return xe

        # 2. Fallback pdflatex
        pdf_tex = shutil.which("pdflatex")
        if pdf_tex:
            return pdf_tex

        return None

    def probe_readiness(self) -> bool:
        """Verifies whether a supported LaTeX compiler and PyMuPDF are available."""
        if fitz is None:
            return False
        return self.resolve_compiler_path() is not None

    def render(
        self,
        request: DiagramRenderRequest,
        cancel_fn: Optional[Callable[[], bool]] = None,
    ) -> DiagramRenderResult:
        """Renders a single diagram request into vector SVG, checking cache first."""
        req_hash = request.compute_hash()

        # 1. Cache hit check
        cached = self.cache.get(req_hash)
        if cached is not None:
            return cached

        # 2. Cooperative cancellation check
        if cancel_fn and cancel_fn():
            raise DiagramCancelledError("cancelled", "Diagram rendering was cancelled.")

        # 3. Toolchain availability check
        compiler = self.resolve_compiler_path()
        if not compiler:
            raise DiagramRendererUnavailableError(
                "unavailable",
                "No LaTeX compiler (xelatex/pdflatex) found on host system.",
            )

        if fitz is None:
            raise DiagramRendererUnavailableError(
                "unavailable",
                "PyMuPDF library is not installed in the active Python environment.",
            )

        # 4. Synthesize compilation source
        source_tex = _STANDALONE_TEMPLATE.replace(
            "__PREAMBLE__", request.preamble or ""
        ).replace(
            "__SOURCE__", request.source
        )

        # 5. Execute in isolated temporary directory
        with tempfile.TemporaryDirectory(prefix="polpo_diag_") as td:
            tex_file = Path(td) / "diagram.tex"
            tex_file.write_text(source_tex, encoding="utf-8")

            cmd = [
                compiler,
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-no-shell-escape",
                "diagram.tex",
            ]

            kwargs: dict[str, Any] = {
                "cwd": td,
                "env": _build_sandboxed_environment(Path(td)),
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            }

            if sys.platform == "win32":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            else:
                kwargs["start_new_session"] = True

            start_t = time.monotonic()
            try:
                proc = subprocess.Popen(cmd, **kwargs)
            except Exception as e:
                raise DiagramRenderError("spawn_failed", f"Failed to spawn TeX compiler: {e}")

            stdout, stderr = "", ""
            while True:
                if cancel_fn and cancel_fn():
                    self._kill_process_tree(proc)
                    raise DiagramCancelledError("cancelled", "Diagram rendering was cancelled.")

                rem = self._timeout_seconds - (time.monotonic() - start_t)
                if rem <= 0:
                    self._kill_process_tree(proc)
                    raise DiagramTimeoutError(
                        "timeout",
                        f"Diagram compilation timed out after {self._timeout_seconds:.1f}s.",
                    )

                step = min(0.1, rem)
                try:
                    out, err = proc.communicate(timeout=step)
                    stdout, stderr = out or "", err or ""
                    break
                except subprocess.TimeoutExpired:
                    continue

            # Check compiler exit code
            if proc.returncode != 0:
                log_file = Path(td) / "diagram.log"
                log_text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.is_file() else ""
                raise _parse_latex_diagnostics(
                    log_content=log_text,
                    stderr=stderr,
                    source=request.source,
                    preamble=request.preamble,
                    returncode=proc.returncode,
                )

            pdf_file = Path(td) / "diagram.pdf"
            if not pdf_file.is_file() or pdf_file.stat().st_size == 0:
                raise DiagramRenderError("empty_pdf", "Compiled diagram PDF was not generated or is empty.")

            # 6. Extract vector SVG using PyMuPDF
            try:
                doc = fitz.open(str(pdf_file))
                if len(doc) == 0:
                    raise DiagramRenderError("invalid_pdf", "Compiled PDF contains 0 pages.")
                page = doc[0]
                svg_xml = page.get_svg_image()
                width = float(page.rect.width)
                height = float(page.rect.height)
                doc.close()
            except Exception as e:
                raise DiagramRenderError("svg_conversion_failed", f"Failed to convert PDF to SVG: {e}")

            # 7. Output safety boundaries
            if len(svg_xml.encode("utf-8")) > _MAX_SVG_BYTES:
                raise DiagramOutputLimitError("output_limit", "Rendered diagram SVG exceeds size limit.")

            if width > _MAX_DIMENSION_POINTS or height > _MAX_DIMENSION_POINTS:
                raise DiagramOutputLimitError("output_limit", "Rendered diagram dimensions exceed bounds.")

            result = DiagramRenderResult(
                hash=req_hash,
                svg_xml=svg_xml,
                width=width,
                height=height,
                status="rendered",
            )
            self.cache.put(result)
            return result

    def render_batch(
        self,
        requests: Sequence[DiagramRenderRequest],
        cancel_fn: Optional[Callable[[], bool]] = None,
    ) -> Mapping[str, Union[DiagramRenderResult, DiagramRenderError]]:
        """Renders multiple diagram requests, continuing past individual failures."""
        outcomes: dict[str, Union[DiagramRenderResult, DiagramRenderError]] = {}

        for req in requests:
            h = req.compute_hash()
            if cancel_fn and cancel_fn():
                outcomes[h] = DiagramCancelledError("cancelled", "Batch rendering was cancelled.")
                continue
            try:
                outcomes[h] = self.render(req, cancel_fn=cancel_fn)
            except DiagramRenderError as exc:
                outcomes[h] = exc
            except Exception as e:
                outcomes[h] = DiagramRenderError("unknown", str(e))

        return outcomes

    def _kill_process_tree(self, proc: subprocess.Popen) -> None:
        """Kills a child process and its entire process tree safely across platforms."""
        try:
            if sys.platform == "win32":
                proc.kill()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.communicate(timeout=0.5)
        except Exception:
            pass
