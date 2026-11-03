# ============================================================
#  tests/unit/test_diagram_preview.py
#  Unit Tests for In-App Native Diagram Preview Pipeline
# ============================================================

from typing import Mapping, Optional, Sequence, Union
from unittest.mock import MagicMock

import pytest

from application.dto.markdown_dto import MarkdownDocumentDTO, MarkdownNodeDTO
from application.ports.diagram_renderer import (
    DiagramRenderError,
    DiagramRenderRequest,
    DiagramRenderResult,
    DiagramSyntaxError,
    IDiagramRenderer,
)
from application.services.markdown_viewer_service import MarkdownViewerService
from infrastructure.diagram.cache import DiagramSvgCache
from infrastructure.markdown.pandoc_parser import PandocParser
from interfaces.desktop.models.markdown_document_model import MarkdownDocumentModel
from interfaces.desktop.providers.diagram_image_provider import (
    DiagramImageProvider,
    parse_diagram_image_url,
)
from interfaces.desktop.qt_compat import QSize


class FakeDiagramRenderer(IDiagramRenderer):
    """Test double implementing IDiagramRenderer without spawning LaTeX subprocesses."""

    def __init__(self, cache: Optional[DiagramSvgCache] = None) -> None:
        self.cache = cache or DiagramSvgCache()
        self.rendered_sources: list[str] = []

    def probe_readiness(self) -> bool:
        return True

    def render(self, request: DiagramRenderRequest, cancel_fn=None) -> DiagramRenderResult:
        self.rendered_sources.append(request.source)
        res = DiagramRenderResult(
            hash=request.compute_hash(),
            svg_xml='<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"><rect width="120" height="80"/></svg>',
            width=120.0,
            height=80.0,
            status="rendered",
        )
        self.cache.put(res)
        return res

    def render_batch(
        self,
        requests: Sequence[DiagramRenderRequest],
        cancel_fn=None,
    ) -> Mapping[str, Union[DiagramRenderResult, DiagramRenderError]]:
        outcomes: dict[str, Union[DiagramRenderResult, DiagramRenderError]] = {}
        for req in requests:
            outcomes[req.compute_hash()] = self.render(req, cancel_fn=cancel_fn)
        return outcomes


def create_viewer_service(diagram_renderer: Optional[IDiagramRenderer] = None) -> MarkdownViewerService:
    parser = PandocParser()
    uow_factory = MagicMock()
    storage = MagicMock()
    return MarkdownViewerService(
        parser=parser,
        uow_factory=uow_factory,
        storage=storage,
        diagram_renderer=diagram_renderer,
    )


def test_quarto_raw_latex_diagram_projection():
    renderer = FakeDiagramRenderer()
    service = create_viewer_service(diagram_renderer=renderer)

    text = """# Chapter 1

Here is a circuit diagram:

```{=latex}
\\begin{circuitikz}[american]
    \\draw (0,0) to[R=10] (2,0);
\\end{circuitikz}
```

End of section.
"""
    dto = service.render_text(raw_text=text, active_regions=[], job_id=1, version=1)
    nodes = dto.nodes

    assert len(nodes) == 4
    assert nodes[0].node_type == "heading"
    assert nodes[1].node_type == "paragraph"
    assert nodes[2].node_type == "diagram"
    assert nodes[3].node_type == "paragraph"

    diag_node = nodes[2]
    assert "\\begin{circuitikz}" in diag_node.diagram_source
    assert diag_node.diagram_status == "rendered"
    assert diag_node.diagram_width == 120.0
    assert diag_node.diagram_height == 80.0
    assert diag_node.has_error is False
    assert diag_node.diagram_hash is not None


def test_quarto_raw_latex_non_diagram_projection():
    service = create_viewer_service()

    text = """```{=latex}
\\noindent\\textbf{Plain LaTeX snippet}
```"""
    dto = service.render_text(raw_text=text, active_regions=[], job_id=1)
    nodes = dto.nodes

    assert len(nodes) == 1
    assert nodes[0].node_type == "raw_block"
    assert nodes[0].language == "latex"
    assert "\\noindent" in nodes[0].content


def test_incomplete_draft_diagram_flags_syntax_error():
    service = create_viewer_service()

    # User is actively typing an unclosed TikZ environment
    text = """```{=latex}
\\begin{tikzpicture}
    \\draw (0,0) -- (1,1);
```"""
    dto = service.render_text(raw_text=text, active_regions=[], job_id=1)
    nodes = dto.nodes

    assert len(nodes) == 1
    diag_node = nodes[0]
    assert diag_node.node_type == "diagram"
    assert diag_node.has_error is True
    assert diag_node.error_category == "syntax"
    assert diag_node.diagram_status == "error"
    assert "Unclosed" in diag_node.error_message


def test_legacy_tikz_code_block_backward_compatibility():
    renderer = FakeDiagramRenderer()
    service = create_viewer_service(diagram_renderer=renderer)

    text = """```tikz
\\begin{tikzpicture}
    \\draw (0,0) -- (2,2);
\\end{tikzpicture}
```"""
    dto = service.render_text(raw_text=text, active_regions=[], job_id=1)
    nodes = dto.nodes

    assert len(nodes) == 1
    diag_node = nodes[0]
    assert diag_node.node_type == "diagram"
    assert diag_node.diagram_status == "rendered"
    assert diag_node.has_error is False


def test_markdown_document_model_diagram_roles():
    model = MarkdownDocumentModel()
    roles = model.roleNames()

    assert model.DiagramSourceRole in roles
    assert model.DiagramHashRole in roles
    assert model.DiagramStatusRole in roles
    assert model.DiagramWidthRole in roles
    assert model.DiagramHeightRole in roles
    assert model.DiagramHasErrorRole in roles
    assert model.DiagramErrorMessageRole in roles

    node = MarkdownNodeDTO(
        node_id="diagram_1",
        node_type="diagram",
        diagram_source=r"\begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}",
        diagram_hash="mock_hash_123",
        diagram_status="rendered",
        diagram_width=150.0,
        diagram_height=100.0,
        has_error=False,
    )
    doc_dto = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
    model.set_document(doc_dto)

    assert model.rowCount() == 1
    idx = model.index(0, 0)
    assert model.data(idx, model.NodeTypeRole) == "diagram"
    assert model.data(idx, model.DiagramHashRole) == "mock_hash_123"
    assert model.data(idx, model.DiagramStatusRole) == "rendered"
    assert model.data(idx, model.DiagramWidthRole) == 150.0
    assert model.data(idx, model.DiagramHeightRole) == 100.0
    assert model.data(idx, model.DiagramHasErrorRole) is False


def test_diagram_image_provider_url_parsing():
    theme, h = parse_diagram_image_url("dark/abc123hash")
    assert theme == "dark"
    assert h == "abc123hash"

    theme, h = parse_diagram_image_url("abc123hash")
    assert theme == "dark"
    assert h == "abc123hash"

    theme, h = parse_diagram_image_url("")
    assert theme == "dark"
    assert h == ""


def test_diagram_image_provider_miss_and_hit():
    cache = DiagramSvgCache(capacity=10)
    provider = DiagramImageProvider(cache=cache, device_pixel_ratio=1.0)

    # 1. Miss returns 1x1 transparent image
    out_size = QSize()
    img_miss = provider.requestImage("dark/unknown_hash", out_size, QSize(-1, -1))
    assert img_miss.width() == 1
    assert img_miss.height() == 1

    # 2. Put valid SVG into cache
    res = DiagramRenderResult(
        hash="test_hash_456",
        svg_xml='<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><circle cx="50" cy="50" r="40"/></svg>',
        width=200.0,
        height=100.0,
    )
    cache.put(res)

    # 3. Hit returns rendered QImage
    img_hit = provider.requestImage("dark/test_hash_456", out_size, QSize(-1, -1))
    assert not img_hit.isNull()
    assert img_hit.width() == 200
    assert img_hit.height() == 100


def test_regular_code_blocks_not_hijacked_as_diagrams():
    """Confirms ordinary code blocks (e.g. latex, python) containing TikZ syntax are not hijacked."""
    service = create_viewer_service()

    # Case 1: Standard markdown code fence with language "latex"
    latex_code = """```latex
\\begin{tikzpicture}
    \\draw (0,0) -- (1,1);
\\end{tikzpicture}
```"""
    dto1 = service.render_text(raw_text=latex_code, active_regions=[], job_id=1)
    assert len(dto1.nodes) == 1
    assert dto1.nodes[0].node_type == "code_block"
    assert dto1.nodes[0].language == "latex"
    assert "\\begin{tikzpicture}" in dto1.nodes[0].content

    # Case 2: Standard markdown code fence with language "python"
    py_code = """```python
tikz_template = \"\"\"
\\begin{tikzpicture}
    \\draw (0,0) -- (1,1);
\\end{tikzpicture}
\"\"\"
```"""
    dto2 = service.render_text(raw_text=py_code, active_regions=[], job_id=2)
    assert len(dto2.nodes) == 1
    assert dto2.nodes[0].node_type == "code_block"
    assert dto2.nodes[0].language == "python"


def test_multiple_diagrams_in_single_raw_block_projected():
    """Confirms multiple diagrams within a single raw LaTeX block are all projected into nodes."""
    renderer = FakeDiagramRenderer()
    service = create_viewer_service(diagram_renderer=renderer)

    text = """```{=latex}
\\begin{tikzpicture}
    \\draw (0,0) -- (1,0);
\\end{tikzpicture}

\\begin{tikzpicture}
    \\draw (0,0) -- (0,1);
\\end{tikzpicture}
```"""
    dto = service.render_text(raw_text=text, active_regions=[], job_id=1)
    assert len(dto.nodes) == 2
    assert all(n.node_type == "diagram" for n in dto.nodes)
    assert "\\draw (0,0) -- (1,0);" in dto.nodes[0].diagram_source
    assert "\\draw (0,0) -- (0,1);" in dto.nodes[1].diagram_source
    assert dto.nodes[0].diagram_hash != dto.nodes[1].diagram_hash
    assert dto.nodes[0].diagram_status == "rendered"
    assert dto.nodes[1].diagram_status == "rendered"


def test_quarto_raw_latex_tikzcd_projection():
    """Confirms tikz-cd diagram environment is recognized and projected as a diagram node."""
    renderer = FakeDiagramRenderer()
    service = create_viewer_service(diagram_renderer=renderer)

    text = """```{=latex}
\\begin{tikzcd}
    A \\arrow[r] & B
\\end{tikzcd}
```"""
    dto = service.render_text(raw_text=text, active_regions=[], job_id=1)
    assert len(dto.nodes) == 1
    diag_node = dto.nodes[0]
    assert diag_node.node_type == "diagram"
    assert "\\begin{tikzcd}" in diag_node.diagram_source
    assert diag_node.diagram_status == "rendered"
