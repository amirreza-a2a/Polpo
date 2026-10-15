# ============================================================
#  infrastructure/pdf/tex_log_sanitizer.py
#  TeX Log Sanitizer & Diagnostic Parser
#  Phase 13 — Ticket #101
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
import getpass
import os
from pathlib import Path
import re
from typing import Dict, List, Optional, Sequence, Tuple

# Mapping from LaTeX style/package stems to canonical CTAN package identifiers.
KNOWN_PACKAGE_MAP: Dict[str, str] = {
    "tikz": "pgf",
    "pgf": "pgf",
    "pgfplots": "pgfplots",
    "amsmath": "amsmath",
    "amssymb": "amsfonts",
    "amsfonts": "amsfonts",
    "fontspec": "fontspec",
    "xcolor": "xcolor",
    "geometry": "geometry",
    "bidi": "bidi",
    "xepersian": "xepersian",
    "babel": "babel",
    "etoolbox": "etoolbox",
    "hyperref": "hyperref",
    "microtype": "microtype",
    "booktabs": "booktabs",
    "caption": "caption",
    "float": "float",
    "fancyhdr": "fancyhdr",
    "listings": "listings",
    "tcolorbox": "tcolorbox",
    "environ": "environ",
    "unicode-math": "unicode-math",
    "luacolor": "luacolor",
    "scrartcl": "koma-script",
    "scrreprt": "koma-script",
    "scrbook": "koma-script",
    "koma-script": "koma-script",
    "standalone": "standalone",
    "beamer": "beamer",
}


@dataclass(frozen=True)
class SanitizedDiagnostic:
    """
    Sanitized compilation diagnostics extracted from TeX / Quarto compiler output.

    Attributes:
        sanitized_message: Clean error summary with absolute paths and usernames redacted.
        missing_packages: Tuple of mapped CTAN package identifiers required for compilation.
        missing_fonts: Tuple of font family names detected as missing.
        line_number: Source line number where compilation error occurred, if identified.
        raw_error_snippet: Raw error snippet before sanitization.
    """
    sanitized_message: str
    missing_packages: Tuple[str, ...] = ()
    missing_fonts: Tuple[str, ...] = ()
    line_number: Optional[int] = None
    raw_error_snippet: str = ""


class TexLogSanitizer:
    """
    Parses TeX compiler logs to extract structured diagnostics while redacting
    host-specific absolute paths, system usernames, and ephemeral staging hashes.
    """

    def __init__(self, package_map: Optional[Dict[str, str]] = None) -> None:
        self._package_map = dict(KNOWN_PACKAGE_MAP)
        if package_map:
            self._package_map.update(package_map)

    def resolve_ctan_package(self, stem: str) -> str:
        """Resolves a .sty or .cls stem name to its installable CTAN package identifier."""
        clean_stem = stem.strip().lower()
        if clean_stem.endswith(".sty"):
            clean_stem = clean_stem[:-4]
        elif clean_stem.endswith(".cls"):
            clean_stem = clean_stem[:-4]
        return self._package_map.get(clean_stem, clean_stem)

    @staticmethod
    def _is_info_line(line: str) -> bool:
        """Determines if a log line is an informational message rather than a compiler error."""
        stripped = line.strip()
        if not stripped:
            return False
        if re.search(
            r"^(?:Package\s+\S+\s+Info|LaTeX\s+(?:Font\s+)?Info|\([a-zA-Z0-9_.-]+\)\s+Info):\s*",
            stripped,
            re.IGNORECASE,
        ):
            return True
        if stripped.startswith("(") and (
            "Info:" in stripped or "Reported on input line" in stripped
        ):
            return True
        return False

    @staticmethod
    def _is_boilerplate(line: str) -> bool:
        """Determines if a log line is standard LaTeX help/documentation boilerplate."""
        stripped = line.strip()
        return bool(
            re.search(
                r"^(?:See the\s+.*documentation|Type\s+H\s+<return>|\.\.\.$)",
                stripped,
                re.IGNORECASE,
            )
        )

    def sanitize(self, raw_log: str) -> SanitizedDiagnostic:
        """
        Parses LaTeX log output, extracts missing packages/fonts, line numbers,
        and redacts host paths and system usernames.
        """
        if not raw_log or not raw_log.strip():
            return SanitizedDiagnostic(
                sanitized_message="LaTeX compilation failed.",
                missing_packages=(),
                missing_fonts=(),
                line_number=None,
                raw_error_snippet="",
            )

        missing_packages: List[str] = []
        missing_fonts: List[str] = []

        # 1. Detect missing package patterns:
        # e.g.: ! LaTeX Error: File 'tikz.sty' not found.
        # e.g.: ! LaTeX Error: File `amssymb.sty' not found.
        # e.g.: ! LaTeX Error: File "pgf.sty" not found.
        # e.g.: ! LaTeX Error: File 'scrartcl.cls' not found.
        pkg_pattern = re.compile(
            r"! LaTeX Error: File [`'\"]([^'\"`\s]+\.(?:sty|cls))[`'\"] not found",
            re.IGNORECASE,
        )
        for match in pkg_pattern.finditer(raw_log):
            raw_file = match.group(1)
            stem = Path(raw_file).stem
            ctan_name = self.resolve_ctan_package(stem)
            if ctan_name and ctan_name not in missing_packages:
                missing_packages.append(ctan_name)

        # 2. Detect missing font patterns:
        # e.g.: ! Package fontspec Error: The font "Vazirmatn" cannot be found.
        # e.g.: ! The font "Vazirmatn" cannot be found.
        font_pattern = re.compile(
            r'(?:! (?:Package fontspec Error: )?The font ["\']([^"\']+)["\'] cannot be found)|'
            r'(?:fontspec error: "font-not-found".*?font ["\']([^"\']+)["\'])',
            re.IGNORECASE | re.DOTALL,
        )
        for match in font_pattern.finditer(raw_log):
            font_name = match.group(1) or match.group(2)
            if font_name and font_name not in missing_fonts:
                missing_fonts.append(font_name.strip())

        # 3. Extract primary error blocks and line number
        lines = raw_log.splitlines()
        error_blocks: List[List[str]] = []
        current_block: List[str] = []
        in_error = False

        for line in lines:
            stripped = line.strip()
            if self._is_info_line(line):
                continue

            is_error_start = (
                stripped.startswith("!")
                or bool(re.search(r"(?:\.tex|\.qmd):\d+:\s*(?:!|.*error)", stripped, re.IGNORECASE))
                or ("cannot be found" in stripped and "font" in stripped.lower())
                or stripped.startswith("Emergency stop")
                or stripped.startswith("Fatal error occurred")
            )

            if is_error_start:
                if current_block:
                    error_blocks.append(current_block)
                    current_block = []
                in_error = True
                current_block.append(line)
            elif in_error:
                if self._is_boilerplate(line):
                    continue
                if not stripped:
                    # End context block on empty line if we have already captured a source line number
                    if any(re.search(r"(?:l\.\d+|on input line \d+)", l) for l in current_block):
                        in_error = False
                        error_blocks.append(current_block)
                        current_block = []
                    continue
                if (
                    stripped.startswith("Here is how much")
                    or stripped.startswith("Transcript written on")
                    or stripped.startswith("Output written on")
                    or stripped.startswith("End of file on")
                ):
                    in_error = False
                    if current_block:
                        error_blocks.append(current_block)
                        current_block = []
                elif len(current_block) >= 6:
                    in_error = False
                    error_blocks.append(current_block)
                    current_block = []
                else:
                    current_block.append(line)

        if current_block:
            error_blocks.append(current_block)

        # Detect line number:
        # Prefer line number from error blocks
        line_number: Optional[int] = None
        for blk in error_blocks:
            blk_text = "\n".join(blk)
            m = re.search(
                r"(?:(?:\.tex|\.qmd):(\d+):|l\.(\d+)(?:\s|$)|(?:on input line|input line)\s+(\d+))",
                blk_text,
            )
            if m:
                for g in m.groups():
                    if g is not None:
                        try:
                            line_number = int(g)
                            break
                        except ValueError:
                            pass
            if line_number is not None:
                break

        # Fallback line number outside info lines
        if line_number is None:
            for line in lines:
                if self._is_info_line(line):
                    continue
                m = re.search(
                    r"(?:(?:\.tex|\.qmd):(\d+):|l\.(\d+)(?:\s|$)|(?:on input line|input line)\s+(\d+))",
                    line,
                )
                if m:
                    for g in m.groups():
                        if g is not None:
                            try:
                                line_number = int(g)
                                break
                            except ValueError:
                                pass
                if line_number is not None:
                    break

        # 4. Extract primary error lines for snippet from error blocks
        flattened_lines: List[str] = []
        for blk in error_blocks:
            for l in blk:
                l_stripped = l.strip()
                if l_stripped and l_stripped not in [x.strip() for x in flattened_lines]:
                    flattened_lines.append(l.rstrip())

        raw_snippet = "\n".join(flattened_lines[:8]) if flattened_lines else raw_log[:500]

        # 5. Redact paths, usernames, and staging hashes
        sanitized = self._redact_sensitive_info(raw_snippet)


        # 6. Formulate clean user-facing message
        message_parts: List[str] = []
        if missing_packages:
            message_parts.append(f"Missing LaTeX package(s): {', '.join(missing_packages)}")
        if missing_fonts:
            message_parts.append(f"Missing font(s): {', '.join(missing_fonts)}")

        if sanitized:
            message_parts.append(sanitized)
        elif not message_parts:
            message_parts.append("LaTeX compilation failed.")

        final_message = "\n".join(message_parts)

        return SanitizedDiagnostic(
            sanitized_message=final_message,
            missing_packages=tuple(missing_packages),
            missing_fonts=tuple(missing_fonts),
            line_number=line_number,
            raw_error_snippet=raw_snippet,
        )

    def _redact_sensitive_info(self, text: str) -> str:
        """
        Redacts host absolute paths, user home directory names, and staging hashes.
        """
        redacted = text

        # Redact ephemeral staging directory hashes (e.g. .pdf_staging/1_a1b2c3d4/ -> [STAGING_DIR]/)
        redacted = re.sub(
            r"\.pdf_staging[/\\]\d+_[a-fA-F0-9]+[/\\]?",
            "[STAGING_DIR]/",
            redacted,
        )

        # Redact Windows paths (e.g. C:\Users\Username\...\ -> [REDACTED_PATH]\)
        redacted = re.sub(
            r"[a-zA-Z]:\\(?:Users|Documents and Settings)\\[^\\\s:()\"']+\\[^:()\"'\n]*",
            "[REDACTED_PATH]",
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(
            r"[a-zA-Z]:\\[^:()\"'\n]+",
            "[REDACTED_PATH]",
            redacted,
        )

        # Redact Unix home paths (e.g. /home/username/... -> [REDACTED_PATH])
        redacted = re.sub(
            r"/(?:home|Users)/[^/\s:()\"']+/[^:()\"'\n]*",
            "[REDACTED_PATH]",
            redacted,
        )

        # Redact remaining /home/username or /Users/username references
        redacted = re.sub(
            r"/(?:home|Users)/[^/\s:()\"']+",
            "[REDACTED_PATH]",
            redacted,
        )

        # Redact current system username if present in text
        try:
            current_user = getpass.getuser()
            if current_user and len(current_user) >= 2:
                redacted = re.sub(re.escape(current_user), "[REDACTED_USER]", redacted)
        except Exception:
            pass

        # Also check USER, USERNAME environment variables
        for env_var in ("USER", "USERNAME", "LOGNAME"):
            user_env = os.environ.get(env_var)
            if user_env and len(user_env) >= 2:
                redacted = re.sub(re.escape(user_env), "[REDACTED_USER]", redacted)

        return redacted
