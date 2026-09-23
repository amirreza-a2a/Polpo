"""Unit tests for MarkdownViewerService math projections and column positions (TICK-006).

Verifies projection of MathBlock to MarkdownNodeDTO, InlineType.MATH to InlineSegmentDTO,
deterministic node IDs for math blocks, and propagation of column positions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

from core.markdown.ast import (
    BlockquoteBlock,
    HeadingBlock,
    InlineSpan,
    InlineType,
    MarkdownDocument,
    MathBlock,
    ParagraphBlock,
)
from application.ports.markdown_parser import IMarkdownParser
from application.ports.math_renderer import MathRenderRequest
from application.services.markdown_viewer_service import MarkdownViewerService
from infrastructure.markdown.exceptions import PandocNotFoundError
from infrastructure.markdown.pandoc_binary import PandocBinaryResolver
from infrastructure.markdown.pandoc_runner import PandocRunner

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "pandoc_ast_fixtures.json"


@pytest.fixture(autouse=True)
def ensure_pandoc_runner_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests run hermetically across platforms even when Pandoc binary is absent."""
    try:
        PandocBinaryResolver().resolve()
        pandoc_present = True
    except PandocNotFoundError:
        pandoc_present = False

    force_fallback = bool(os.environ.get("POLPO_FORCE_PANDOC_FIXTURES"))
    if not pandoc_present or force_fallback:
        fixtures: dict[str, dict] = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

        monkeypatch.setattr(
            PandocBinaryResolver,
            "resolve",
            lambda self: Path("/mock/bin/pandoc"),
        )

        original_run = PandocRunner.run

        def mock_run(self: PandocRunner, text: str, timeout_seconds: float = 5.0) -> dict:
            if text in fixtures:
                return fixtures[text]
            return original_run(self, text, timeout_seconds=timeout_seconds)

        monkeypatch.setattr(PandocRunner, "run", mock_run)


class StubParser(IMarkdownParser):
    def __init__(self, doc: MarkdownDocument) -> None:
        self._doc = doc

    def parse(self, text: str) -> MarkdownDocument:
        return self._doc


def test_project_math_block_to_node_dto():
    tex = r"\int_{-\infty}^\infty e^{-x^2} dx = \sqrt{\pi}"
    math_block = MathBlock(
        content=tex,
        source_start_line=5,
        source_end_line=7,
        source_start_col=1,
        source_end_col=3,
    )
    doc = MarkdownDocument(blocks=(math_block,))
    service = MarkdownViewerService(parser=StubParser(doc), uow_factory=None, storage=None)

    dto = service.render_text("", active_regions=[], job_id=42)

    assert len(dto.nodes) == 1
    node = dto.nodes[0]
    assert node.node_type == "math_block"
    assert node.content == tex
    assert node.raw_markdown == f"$${tex}$$"
    assert node.math_tex == tex
    expected_hash = MathRenderRequest(tex=tex, display=True).compute_hash()
    assert node.math_hash == expected_hash
    assert node.node_id.startswith(f"math_{expected_hash[:12]}_")
    assert node.source_start_line == 5
    assert node.source_end_line == 7
    assert node.source_start_col == 1
    assert node.source_end_col == 3


def test_project_inline_math_segment_to_dto():
    tex = "E = mc^2"
    span_math = InlineSpan(span_type=InlineType.MATH, text=tex)
    span_text_before = InlineSpan(span_type=InlineType.TEXT, text="Formula: ")
    span_text_after = InlineSpan(span_type=InlineType.TEXT, text=" is famous.")
    p_block = ParagraphBlock(
        inlines=(span_text_before, span_math, span_text_after),
        source_start_line=1,
        source_end_line=1,
        source_start_col=1,
        source_end_col=40,
    )
    doc = MarkdownDocument(blocks=(p_block,))
    service = MarkdownViewerService(parser=StubParser(doc), uow_factory=None, storage=None)

    dto = service.render_text("", active_regions=[], job_id=42)

    assert len(dto.nodes) == 1
    node = dto.nodes[0]
    assert node.node_type == "paragraph"
    assert len(node.segments) == 3

    math_seg = node.segments[1]
    assert math_seg.segment_type == "math"
    assert math_seg.math_tex == tex
    expected_hash = MathRenderRequest(tex=tex, display=False).compute_hash()
    assert math_seg.math_hash == expected_hash
    assert "$E = mc^2$" in math_seg.text_html
    # TICK-006 must NOT create live image://math/... rendering dependencies
    assert "image://math/" not in node.content
    assert "image://math/" not in math_seg.text_html
    assert "$E = mc^2$" in node.content


def test_blocks_propagate_column_positions_to_node_dto():
    h_block = HeadingBlock(
        level=2,
        inlines=(InlineSpan(span_type=InlineType.TEXT, text="Section Title"),),
        source_start_line=1,
        source_end_line=1,
        source_start_col=1,
        source_end_col=15,
    )
    doc = MarkdownDocument(blocks=(h_block,))
    service = MarkdownViewerService(parser=StubParser(doc), uow_factory=None, storage=None)

    dto = service.render_text("", active_regions=[], job_id=42)

    assert len(dto.nodes) == 1
    node = dto.nodes[0]
    assert node.source_start_col == 1
    assert node.source_end_col == 15


def test_duplicate_math_blocks_produce_unique_node_ids():
    tex = r"\alpha + \beta = \gamma"
    b1 = MathBlock(content=tex)
    b2 = MathBlock(content=tex)
    doc = MarkdownDocument(blocks=(b1, b2))
    service = MarkdownViewerService(parser=StubParser(doc), uow_factory=None, storage=None)

    dto = service.render_text("", active_regions=[], job_id=42)

    assert len(dto.nodes) == 2
    assert dto.nodes[0].node_id.endswith("_1")
    assert dto.nodes[1].node_id.endswith("_2")
    assert dto.nodes[0].node_id != dto.nodes[1].node_id


def test_blockquote_preserves_child_math_block():
    tex = r"\sum_{i=1}^n i = \frac{n(n+1)}{2}"
    math_b = MathBlock(content=tex)
    p_b = ParagraphBlock(inlines=(InlineSpan(span_type=InlineType.TEXT, text="Intro to sum"),))
    quote_b = BlockquoteBlock(blocks=(p_b, math_b))
    doc = MarkdownDocument(blocks=(quote_b,))
    service = MarkdownViewerService(parser=StubParser(doc), uow_factory=None, storage=None)

    dto = service.render_text("", active_regions=[], job_id=42)

    assert len(dto.nodes) == 1
    node = dto.nodes[0]
    assert node.node_type == "blockquote"
    assert len(node.quote_children) == 2
    assert node.quote_children[0].child_type == "paragraph"
    assert node.quote_children[1].child_type == "math_block"
    assert node.quote_children[1].content == tex
    assert len(node.quote_children[1].segments) == 1
    assert node.quote_children[1].segments[0].segment_type == "math"
    assert node.quote_children[1].segments[0].math_tex == tex
    assert "image://math/" not in node.content
    assert f"$${tex}$$" in node.content


def test_canonical_visual_occurrence_identity_preserved_standalone():
    """Verify canonical occurrence_id survives from standalone visual token to DTO."""
    import uuid
    from core.entities.bounding_box import BoundingBox
    from core.entities.visual_region import VisualRegion, RegionOrigin, ReviewStatus, SyncStatus
    from infrastructure.markdown.pandoc_parser import PandocParser

    reg_uuid = uuid.UUID("00000000-0000-4000-8000-000000000001")
    reg_id_hex = reg_uuid.hex
    reg_id_token = str(reg_uuid)
    occ_id = "00000000-0000-4000-8000-000000000002"

    active_region = VisualRegion(
        id=1,
        job_id=42,
        region_id=reg_id_hex,
        page_number=1,
        display_order=1,
        detected_bbox=BoundingBox(0, 0, 100, 100),
        origin=RegionOrigin.AI_DETECTED,
        review_status=ReviewStatus.ACCEPTED,
        sync_status=SyncStatus.SYNCED,
    )

    md = f'![Figure 1](crop_1.jpg "polpo:region={reg_id_token};occ={occ_id}")'
    parser = PandocParser()
    service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)
    dto = service.render_text(md, active_regions=[active_region], job_id=42)

    assert len(dto.nodes) == 1
    node = dto.nodes[0]
    assert node.node_type == "image"
    assert len(node.regions) == 1
    assert node.regions[0].occurrence_id == occ_id
    assert node.regions[0].region_id == reg_id_hex
    assert dto.region_to_occurrences[reg_id_hex][0].occurrence_id == occ_id


def test_canonical_visual_occurrence_identity_preserved_inline():
    """Verify canonical occurrence_id survives from inline visual token to DTO."""
    import uuid
    from core.entities.bounding_box import BoundingBox
    from core.entities.visual_region import VisualRegion, RegionOrigin, ReviewStatus, SyncStatus
    from infrastructure.markdown.pandoc_parser import PandocParser

    reg_uuid = uuid.UUID("00000000-0000-4000-8000-000000000001")
    reg_id_hex = reg_uuid.hex
    reg_id_token = str(reg_uuid)
    occ_id = "00000000-0000-4000-8000-000000000002"

    active_region = VisualRegion(
        id=1,
        job_id=42,
        region_id=reg_id_hex,
        page_number=1,
        display_order=1,
        detected_bbox=BoundingBox(0, 0, 100, 100),
        origin=RegionOrigin.AI_DETECTED,
        review_status=ReviewStatus.ACCEPTED,
        sync_status=SyncStatus.SYNCED,
    )

    md = f'Reference text ![Figure 1](crop_1.jpg "polpo:region={reg_id_token};occ={occ_id}") continues.'
    parser = PandocParser()
    service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)
    dto = service.render_text(md, active_regions=[active_region], job_id=42)

    assert len(dto.nodes) == 1
    node = dto.nodes[0]
    assert node.node_type == "paragraph"
    assert len(node.regions) == 1
    assert node.regions[0].occurrence_id == occ_id
    assert node.regions[0].region_id == reg_id_hex
    assert dto.region_to_occurrences[reg_id_hex][0].occurrence_id == occ_id


def test_canonical_visual_occurrence_identity_preserved_combined():
    """Verify standalone and inline visual tokens preserve distinct occurrence_ids in same document."""
    import uuid
    from core.entities.bounding_box import BoundingBox
    from core.entities.visual_region import VisualRegion, RegionOrigin, ReviewStatus, SyncStatus
    from infrastructure.markdown.pandoc_parser import PandocParser

    reg_uuid = uuid.UUID("00000000-0000-4000-8000-000000000001")
    reg_id_hex = reg_uuid.hex
    reg_id_token = str(reg_uuid)
    occ_id_standalone = "00000000-0000-4000-8000-000000000002"
    occ_id_inline = "00000000-0000-4000-8000-000000000003"

    active_region = VisualRegion(
        id=1,
        job_id=42,
        region_id=reg_id_hex,
        page_number=1,
        display_order=1,
        detected_bbox=BoundingBox(0, 0, 100, 100),
        origin=RegionOrigin.AI_DETECTED,
        review_status=ReviewStatus.ACCEPTED,
        sync_status=SyncStatus.SYNCED,
    )

    md = (
        f'![Figure 1](crop_1.jpg "polpo:region={reg_id_token};occ={occ_id_standalone}")\n\n'
        f'Paragraph with ![Inline Fig](crop_1.jpg "polpo:region={reg_id_token};occ={occ_id_inline}") inside.'
    )

    parser = PandocParser()
    service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)
    dto = service.render_text(md, active_regions=[active_region], job_id=42)

    assert len(dto.nodes) == 2

    # Standalone image block
    img_node = dto.nodes[0]
    assert img_node.node_type == "image"
    assert len(img_node.regions) == 1
    assert img_node.regions[0].occurrence_id == occ_id_standalone
    assert img_node.regions[0].region_id == reg_id_hex

    # Inline image in paragraph
    para_node = dto.nodes[1]
    assert para_node.node_type == "paragraph"
    assert len(para_node.regions) == 1
    assert para_node.regions[0].occurrence_id == occ_id_inline
    assert para_node.regions[0].region_id == reg_id_hex

    # Inverted index contains both distinct occurrences
    occurrences = dto.region_to_occurrences[reg_id_hex]
    assert len(occurrences) == 2
    assert occurrences[0].occurrence_id == occ_id_standalone
    assert occurrences[1].occurrence_id == occ_id_inline
