# ============================================================
#  tests/unit/test_tex_log_sanitizer.py
#  Unit tests for TeX Log Sanitizer
#  Phase 13 — Ticket #101
# ============================================================

from pathlib import Path
import pytest

from infrastructure.pdf.tex_log_sanitizer import (
    KNOWN_PACKAGE_MAP,
    SanitizedDiagnostic,
    TexLogSanitizer,
)


def test_sanitizer_extracts_missing_package_and_redacts_paths():
    """
    TexLogSanitizer extracts missing .sty packages, maps them to CTAN names,
    and strips machine-specific absolute paths and usernames from diagnostics.
    """
    raw_log = r"""
This is XeTeX, Version 3.141592653-2.6-0.999995 (TeX Live 2024)
(/home/amirreza/DevelopPOlpo/PolpoT/artifacts/job_1/.pdf_staging/1_a1b2c3d4/document.tex
! LaTeX Error: File 'tikz.sty' not found.

Type X to quit or <RETURN> to proceed,
or enter new name. (Default extension: sty)

Enter file name:
! Emergency stop.
<read *>

l.12 \usepackage
                {tikz}
End of file on /home/amirreza/DevelopPOlpo/PolpoT/artifacts/job_1/.pdf_staging/1_a1b2c3d4/document.tex
"""
    sanitizer = TexLogSanitizer()
    diag = sanitizer.sanitize(raw_log)

    assert isinstance(diag, SanitizedDiagnostic)
    # tikz.sty maps to CTAN package 'pgf'
    assert "pgf" in diag.missing_packages
    assert diag.line_number == 12

    # Verify path and username sanitization
    assert "/home/amirreza" not in diag.sanitized_message
    assert "amirreza" not in diag.sanitized_message
    assert "1_a1b2c3d4" not in diag.sanitized_message
    assert "tikz.sty" in diag.sanitized_message or "pgf" in diag.sanitized_message


def test_sanitizer_identifies_font_error():
    """
    TexLogSanitizer detects fontspec / XeTeX font-not-found errors and extracts font name.
    """
    raw_log = r"""
(/home/user123/workspace/.pdf_staging/5_cafe1234/document.tex
! Package fontspec Error: The font "Vazirmatn" cannot be found.

See the fontspec package documentation for explanation.
Type  H <return>  for immediate help.
 ...

l.8 \setmainfont{Vazirmatn}
"""
    sanitizer = TexLogSanitizer()
    diag = sanitizer.sanitize(raw_log)

    assert "Vazirmatn" in diag.missing_fonts
    assert diag.line_number == 8
    assert "Vazirmatn" in diag.sanitized_message
    assert "/home/user123" not in diag.sanitized_message
    assert "user123" not in diag.sanitized_message


def test_sanitizer_extracts_backtick_style_missing_package():
    """
    LaTeX sometimes outputs: ! LaTeX Error: File `amssymb.sty' not found.
    """
    raw_log = r"""
! LaTeX Error: File `amssymb.sty' not found.
l.15 \\usepackage{amssymb}
"""
    sanitizer = TexLogSanitizer()
    diag = sanitizer.sanitize(raw_log)

    # amssymb maps to amsfonts
    assert "amsfonts" in diag.missing_packages
    assert diag.line_number == 15


def test_sanitizer_package_name_mapping():
    """
    Known package dictionary correctly maps .sty stems to CTAN identifiers,
    and falls back to stem for unmapped packages.
    """
    sanitizer = TexLogSanitizer()

    assert sanitizer.resolve_ctan_package("tikz") == "pgf"
    assert sanitizer.resolve_ctan_package("amssymb") == "amsfonts"
    assert sanitizer.resolve_ctan_package("amsmath") == "amsmath"
    assert sanitizer.resolve_ctan_package("pgfplots") == "pgfplots"
    assert sanitizer.resolve_ctan_package("customunmapped") == "customunmapped"


def test_sanitizer_windows_paths_and_usernames_redacted():
    """
    Strips Windows-style absolute paths and temporary directory hashes.
    """
    raw_log = r"""
C:\\Users\\JohnDoe\\AppData\\Local\\Temp\\.pdf_staging\\9_beef9876\\document.tex:45: ! Undefined control sequence.
l.45 \\nonexistentmacro
"""
    sanitizer = TexLogSanitizer()
    diag = sanitizer.sanitize(raw_log)

    assert "C:\\Users\\JohnDoe" not in diag.sanitized_message
    assert "JohnDoe" not in diag.sanitized_message
    assert "beef9876" not in diag.sanitized_message
    assert diag.line_number == 45
    assert "Undefined control sequence" in diag.sanitized_message


def test_sanitizer_clean_or_empty_log():
    """
    Sanitizing an empty or non-error log produces safe empty diagnostics.
    """
    sanitizer = TexLogSanitizer()
    diag = sanitizer.sanitize("")

    assert diag.missing_packages == ()
    assert diag.missing_fonts == ()
    assert diag.line_number is None
    assert diag.sanitized_message == "LaTeX compilation failed."


def test_sanitizer_multiple_missing_packages_deduplicated():
    """
    Extracts multiple distinct missing packages and deduplicates them.
    """
    raw_log = r"""
! LaTeX Error: File 'tikz.sty' not found.
! LaTeX Error: File 'pgf.sty' not found.
! LaTeX Error: File 'custompkg.sty' not found.
"""
    sanitizer = TexLogSanitizer()
    diag = sanitizer.sanitize(raw_log)

    # tikz and pgf both map to 'pgf'
    assert diag.missing_packages == ("pgf", "custompkg")
