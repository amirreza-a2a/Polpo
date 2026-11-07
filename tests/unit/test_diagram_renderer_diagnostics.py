# ============================================================
#  tests/unit/test_diagram_renderer_diagnostics.py
#  Unit Tests for Structured LaTeX Compiler Diagnostics (LFR-01)
# ============================================================

from __future__ import annotations

import hashlib
import pytest

from application.dto.markdown_dto import InlineSegmentDTO, MarkdownNodeDTO
from application.ports.diagram_renderer import (
    DiagramRenderError,
    DiagramRenderRequest,
    DiagramSyntaxError,
)
from application.ports.math_renderer import MathRenderError, MathSyntaxError
from infrastructure.diagram.standalone_tex_renderer import (
    _STANDALONE_TEMPLATE,
    _parse_latex_diagnostics,
)


def test_diagram_syntax_error_attributes():
    """Verifies that DiagramSyntaxError carries structured diagnostic context."""
    err = DiagramSyntaxError(
        code="syntax",
        message="! Package tikz Error: Giving up on this path.",
        returncode=1,
        tex_line=14,
        source_line=4,
        log_excerpt="! Package tikz Error: Giving up on this path.\nl.14 \\node [invalid",
        source_sha256="abc123sha",
        error_category="syntax",
    )

    assert err.code == "syntax"
    assert "Giving up" in err.message
    assert err.returncode == 1
    assert err.tex_line == 14
    assert err.source_line == 4
    assert "l.14" in err.log_excerpt
    assert err.source_sha256 == "abc123sha"
    assert err.error_category == "syntax"
    assert "line 4" in str(err)


def test_parse_latex_diagnostics_line_offset_correction():
    """Verifies that tex_line is mapped to source_line by subtracting wrapper preamble lines."""
    # Compute actual template header lines before __SOURCE__
    header = _STANDALONE_TEMPLATE.split("__SOURCE__")[0]
    preamble = "\\usepackage{braket}\n\\usepackage{xcolor}\n"
    header_tex = header.replace("__PREAMBLE__", preamble)
    header_lines = header_tex.count("\n")

    diagram_source = (
        "\\begin{tikzpicture}\n"
        "  \\draw (0,0) -- (1,1);\n"
        "  \\invalidcommand[foo]\n"  # line 3 of diagram
        "\\end{tikzpicture}\n"
    )

    error_tex_line = header_lines + 3
    mock_log = (
        f"This is XeTeX, Version 3.141592653\n"
        f"! Undefined control sequence.\n"
        f"l.{error_tex_line}   \\invalidcommand\n"
        f"                   [foo]\n"
        f"Here is how much of TeX memory you used:\n"
    )

    diag_err = _parse_latex_diagnostics(
        log_content=mock_log,
        stderr="",
        source=diagram_source,
        preamble=preamble,
        returncode=1,
    )

    assert diag_err.tex_line == error_tex_line
    assert diag_err.source_line == 3
    assert diag_err.error_category == "undefined_control_sequence"
    assert diag_err.source_sha256 == hashlib.sha256(diagram_source.encode("utf-8")).hexdigest()
    assert len(diag_err.log_excerpt) <= 2000
    assert "invalidcommand" in diag_err.log_excerpt or "Undefined control sequence" in diag_err.log_excerpt


def test_parse_latex_diagnostics_sanitizes_absolute_paths():
    """Verifies that private host filesystem paths and user names are redacted from diagnostics."""
    mock_log = (
        "! LaTeX Error: File '/home/secretuser/private_folder/missing.sty' not found.\n"
        "l.5 \\usepackage{missing}\n"
    )

    diag_err = _parse_latex_diagnostics(
        log_content=mock_log,
        stderr="",
        source="\\begin{tikzpicture}\\end{tikzpicture}",
        preamble="",
        returncode=1,
    )

    assert "/home/secretuser/private_folder" not in diag_err.message
    assert "/home/secretuser/private_folder" not in diag_err.log_excerpt
    assert diag_err.error_category == "missing_package"


def test_parse_latex_diagnostics_bounds_excessive_log_excerpt():
    """Verifies that massive TeX logs are bounded to at most 2000 characters."""
    giant_log = "! Error line\n" + ("x" * 5000) + "\nl.10 \\end{tikzpicture}\n"
    diag_err = _parse_latex_diagnostics(
        log_content=giant_log,
        stderr="",
        source="\\begin{tikzpicture}\n\\end{tikzpicture}\n",
        preamble="",
        returncode=1,
    )

    assert len(diag_err.log_excerpt) <= 2000


def test_math_render_error_attributes():
    """Verifies that MathRenderError supports structured error diagnostics."""
    err = MathRenderError(
        code=-32603,
        message="Syntax error: Undefined control sequence \\xyz",
        tex_line=1,
        source_line=1,
        log_excerpt="Undefined control sequence \\xyz",
        source_sha256="mathsha123",
        error_category="syntax",
    )

    assert err.code == -32603
    assert err.tex_line == 1
    assert err.source_line == 1
    assert err.log_excerpt == "Undefined control sequence \\xyz"
    assert err.source_sha256 == "mathsha123"
    assert err.error_category == "syntax"


def test_markdown_dto_carries_error_line_and_log_excerpt():
    """Verifies that MarkdownNodeDTO and InlineSegmentDTO expose error_line and log_excerpt."""
    node = MarkdownNodeDTO(
        node_id="diagram_1",
        node_type="diagram",
        has_error=True,
        error_category="syntax",
        error_message="Invalid TikZ syntax",
        error_line=5,
        log_excerpt="l.15 \\badcmd",
    )
    assert node.error_line == 5
    assert node.log_excerpt == "l.15 \\badcmd"

    seg = InlineSegmentDTO(
        segment_type="math",
        has_error=True,
        error_category="syntax",
        error_message="Undefined control sequence",
        error_line=1,
        log_excerpt="Undefined control sequence",
    )
    assert seg.error_line == 1
    assert seg.log_excerpt == "Undefined control sequence"
