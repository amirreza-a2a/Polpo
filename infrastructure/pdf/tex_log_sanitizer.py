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
        line_number: Optional[int] = None

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

        # 3. Detect line number:
        # e.g.: l.12 \usepackage
        # e.g.: on input line 45
        # e.g.: document.tex:45: !
        line_match = re.search(r"(?:l\.(\d+)|on input line (\d+)|(?:\.tex|\.qmd):(\d+):)", raw_log)
        if line_match:
            for g in line_match.groups():
                if g is not None:
                    try:
                        line_number = int(g)
                        break
                    except ValueError:
                        pass

        # 4. Extract primary error lines for snippet
        error_lines: List[str] = []
        for line in raw_log.splitlines():
            stripped = line.strip()
            if stripped.startswith("!") or "cannot be found" in stripped or "not found" in stripped:
                error_lines.append(stripped)

        raw_snippet = "\n".join(error_lines[:5]) if error_lines else raw_log[:500]

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
