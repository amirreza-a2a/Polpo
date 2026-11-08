# ============================================================
#  tests/unit/test_diagram_security.py
#  Adversarial Security Tests for StandaloneTeXDiagramRenderer
# ============================================================

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import pytest

from application.ports.diagram_renderer import (
    DiagramRenderError,
    DiagramRenderRequest,
    DiagramSyntaxError,
)
from infrastructure.diagram.cache import DiagramSvgCache
from infrastructure.diagram.standalone_tex_renderer import (
    StandaloneTeXDiagramRenderer,
    _build_sandboxed_environment,
)


def is_tex_installed() -> bool:
    return shutil.which("xelatex") is not None or shutil.which("pdflatex") is not None


def test_sandboxed_environment_sets_paranoid_policies_and_strips_secrets():
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        os.environ["POLPO_TEST_SECRET_API_KEY_123"] = "super_secret_token_abc"
        os.environ["GEMINI_API_KEY"] = "gemini_secret_xyz"
        try:
            env = _build_sandboxed_environment(workdir)

            # Paranoid policies
            assert env["openin_any"] == "p"
            assert env["openout_any"] == "p"
            assert env["shell_escape"] == "f"
            assert env["TEXMFOUTPUT"] == str(workdir)

            # Secrets stripped
            assert "POLPO_TEST_SECRET_API_KEY_123" not in env
            assert "GEMINI_API_KEY" not in env
            for k in env:
                assert "API_KEY" not in k.upper()
                assert "SECRET" not in k.upper()
                assert "TOKEN" not in k.upper()

            # System variables preserved
            assert "PATH" in env
        finally:
            os.environ.pop("POLPO_TEST_SECRET_API_KEY_123", None)
            os.environ.pop("GEMINI_API_KEY", None)


@pytest.mark.skipif(not is_tex_installed(), reason="Requires TeX compiler on host")
def test_renderer_blocks_external_canary_absolute_path_read():
    renderer = StandaloneTeXDiagramRenderer()

    with tempfile.TemporaryDirectory(prefix="polpo_canary_") as external_dir:
        canary_file = Path(external_dir) / "canary.txt"
        canary_content = "CANARY_SECRET_DATA_778899"
        canary_file.write_text(canary_content, encoding="utf-8")

        # Diagram attempting to read external canary via absolute path
        attack_source = rf"""\begin{{tikzpicture}}
    \node {{Secret: \input{{{canary_file.as_posix()}}}}};
\end{{tikzpicture}}"""

        req = DiagramRenderRequest(source=attack_source)
        with pytest.raises(DiagramRenderError) as exc_info:
            renderer.render(req)

        # Verify canary content was never captured in error excerpt
        err_msg = str(exc_info.value)
        if hasattr(exc_info.value, "log_excerpt") and exc_info.value.log_excerpt:
            assert canary_content not in exc_info.value.log_excerpt
        assert canary_content not in err_msg


@pytest.mark.skipif(not is_tex_installed(), reason="Requires TeX compiler on host")
def test_renderer_blocks_external_canary_relative_traversal_read():
    renderer = StandaloneTeXDiagramRenderer()

    # Diagram attempting to read parent directory canary via relative traversal
    attack_source = r"""\begin{tikzpicture}
    \node {Secret: \input{../canary.txt}};
\end{tikzpicture}"""

    req = DiagramRenderRequest(source=attack_source)
    with pytest.raises(DiagramRenderError):
        renderer.render(req)


@pytest.mark.skipif(not is_tex_installed(), reason="Requires TeX compiler on host")
def test_renderer_blocks_external_canary_include_read():
    renderer = StandaloneTeXDiagramRenderer()

    with tempfile.TemporaryDirectory(prefix="polpo_canary_inc_") as external_dir:
        canary_file = Path(external_dir) / "canary_inc.tex"
        canary_content = "CANARY_INCLUDE_SECRET_445566"
        canary_file.write_text(rf"\node {{{canary_content}}};", encoding="utf-8")

        attack_source = rf"""\begin{{tikzpicture}}
    \include{{{canary_file.as_posix()}}}
\end{{tikzpicture}}"""

        req = DiagramRenderRequest(source=attack_source)
        with pytest.raises(DiagramRenderError) as exc_info:
            renderer.render(req)

        err_msg = str(exc_info.value)
        if hasattr(exc_info.value, "log_excerpt") and exc_info.value.log_excerpt:
            assert canary_content not in exc_info.value.log_excerpt
        assert canary_content not in err_msg


@pytest.mark.skipif(not is_tex_installed(), reason="Requires TeX compiler on host")
def test_renderer_blocks_external_canary_openin_and_read():
    renderer = StandaloneTeXDiagramRenderer()

    with tempfile.TemporaryDirectory(prefix="polpo_canary_openin_") as external_dir:
        canary_file = Path(external_dir) / "canary_openin.txt"
        canary_content = "CANARY_OPENIN_SECRET_332211"
        canary_file.write_text(canary_content, encoding="utf-8")

        attack_source = rf"""\begin{{tikzpicture}}
    \newread\myfile
    \openin\myfile={canary_file.as_posix()}
    \read\myfile to \line
    \node {{Data: \line}};
    \closein\myfile
\end{{tikzpicture}}"""

        req = DiagramRenderRequest(source=attack_source)
        with pytest.raises(DiagramRenderError) as exc_info:
            renderer.render(req)

        err_msg = str(exc_info.value)
        if hasattr(exc_info.value, "log_excerpt") and exc_info.value.log_excerpt:
            assert canary_content not in exc_info.value.log_excerpt
        assert canary_content not in err_msg


@pytest.mark.skipif(not is_tex_installed(), reason="Requires TeX compiler on host")
def test_renderer_blocks_external_canary_openin_relative_traversal():
    renderer = StandaloneTeXDiagramRenderer()

    attack_source = r"""\begin{tikzpicture}
    \newread\myfile
    \openin\myfile=../canary_rel_openin.txt
    \read\myfile to \line
    \node {Data: \line};
    \closein\myfile
\end{tikzpicture}"""

    req = DiagramRenderRequest(source=attack_source)
    with pytest.raises(DiagramRenderError) as exc_info:
        renderer.render(req)
    assert exc_info.value is not None


@pytest.mark.skipif(not is_tex_installed(), reason="Requires TeX compiler on host")
def test_renderer_blocks_external_canary_file_write():
    renderer = StandaloneTeXDiagramRenderer()

    with tempfile.TemporaryDirectory(prefix="polpo_write_canary_") as external_dir:
        target_file = Path(external_dir) / "should_not_exist.txt"

        attack_source = rf"""\begin{{tikzpicture}}
    \newwrite\outfile
    \openout\outfile={target_file.as_posix()}
    \write\outfile{{MALICIOUS_FILE_WRITE}}
    \closeout\outfile
    \node {{done}};
\end{{tikzpicture}}"""

        req = DiagramRenderRequest(source=attack_source)
        try:
            renderer.render(req)
        except Exception:
            pass

        assert not target_file.exists(), "External file write succeeded; security isolation broken!"


@pytest.mark.skipif(not is_tex_installed(), reason="Requires TeX compiler on host")
def test_renderer_blocks_write18_shell_execution():
    renderer = StandaloneTeXDiagramRenderer()

    with tempfile.TemporaryDirectory(prefix="polpo_shell_canary_") as external_dir:
        canary_marker = Path(external_dir) / "shell_executed.txt"

        attack_source = rf"""\begin{{tikzpicture}}
    \immediate\write18{{touch {canary_marker.as_posix()}}}
    \node {{done}};
\end{{tikzpicture}}"""

        req = DiagramRenderRequest(source=attack_source)
        try:
            renderer.render(req)
        except Exception:
            pass

        assert not canary_marker.exists(), "\\write18 shell execution succeeded; command execution isolation broken!"


@pytest.mark.skipif(not is_tex_installed(), reason="Requires TeX compiler on host")
def test_renderer_preserves_standard_package_and_diagram_features():
    cache = DiagramSvgCache(capacity=10)
    renderer = StandaloneTeXDiagramRenderer(cache=cache)

    # Standard TikZ block
    res_tikz = renderer.render(DiagramRenderRequest(
        source=r"""\begin{tikzpicture}
    \draw[thick, fill=blue!20] (0,0) rectangle (2,1);
\end{tikzpicture}"""
    ))
    assert res_tikz.status == "rendered"
    assert "<svg" in res_tikz.svg_xml
    assert res_tikz.width > 0

    # PGFPlots
    res_pgf = renderer.render(DiagramRenderRequest(
        source=r"""\begin{tikzpicture}
    \begin{axis}[width=4cm, height=3cm]
        \addplot[blue] coordinates {(0,0) (1,1) (2,4)};
    \end{axis}
\end{tikzpicture}"""
    ))
    assert res_pgf.status == "rendered"
    assert "<svg" in res_pgf.svg_xml

    # CircuitTikZ
    res_circ = renderer.render(DiagramRenderRequest(
        source=r"""\begin{circuitikz}[american]
    \draw (0,0) to[R=10] (2,0);
\end{circuitikz}"""
    ))
    assert res_circ.status == "rendered"
    assert "<svg" in res_circ.svg_xml

    # TikZ-CD
    res_cd = renderer.render(DiagramRenderRequest(
        source=r"""\begin{tikzcd}
    X \arrow[r, "f"] & Y
\end{tikzcd}"""
    ))
    assert res_cd.status == "rendered"
    assert "<svg" in res_cd.svg_xml
