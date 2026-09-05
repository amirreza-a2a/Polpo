# ============================================================
#  tests/unit/test_phase10e_service.py
#  Unit Tests for Phase 10E.3 Markdown Viewer Service & DTOs
# ============================================================

import ast
import os
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest

from application.dto.markdown_dto import (
    InlineSegmentDTO,
    MarkdownDocumentDTO,
    MarkdownNodeDTO,
    RegionOccurrenceRef,
    VisualRegionRefDTO,
)
from application.services.markdown_viewer_service import (
    MarkdownViewerService,
    _is_safe_url,
    _normalize_whitespace,
    _slugify,
)
from core.entities.artifact import ArtifactHandle, ArtifactType, StorageBackendType
from core.entities.bounding_box import BoundingBox
from core.entities.job import Job, JobStatus
from core.entities.visual_region import RegionOrigin, ReviewStatus, SyncStatus, VisualRegion
from core.exceptions.domain_exceptions import DomainError, EntityNotFoundError
from infrastructure.markdown.markdown_it_parser import MarkdownItParser


class MockJobRepo:
    def __init__(self, jobs=None):
        self._jobs = {j.id: j for j in (jobs or [])}

    def get_by_id(self, job_id: int):
        return self._jobs.get(job_id)


class MockVisualRegionRepo:
    def __init__(self, regions=None):
        self._regions = list(regions or [])

    def get_by_job_id(self, job_id: int):
        return [r for r in self._regions if r.job_id == job_id]


class MockUnitOfWork:
    def __init__(self, job_repo, region_repo):
        self.jobs = job_repo
        self.visual_regions = region_repo

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class MockUowFactory:
    def __init__(self, uow):
        self._uow = uow

    def create(self):
        return self._uow


class MockStorage:
    def __init__(self, store_data=None):
        self._store = dict(store_data or {})

    def retrieve(self, handle: ArtifactHandle) -> bytes:
        if handle.filename in self._store:
            return self._store[handle.filename]
        if handle.uri in self._store:
            return self._store[handle.uri]
        raise FileNotFoundError(f"Artifact not found: {handle.filename}")


def make_test_region(
    job_id: int = 100,
    page_number: int = 1,
    display_order: int = 1,
    region_id: str = "a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d",
    uri: str = "file:///tmp/artifacts/job_100/crop_100_a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d_v1.jpg",
) -> VisualRegion:
    return VisualRegion(
        id=1,
        region_id=region_id,
        job_id=job_id,
        page_number=page_number,
        display_order=display_order,
        origin=RegionOrigin.AI_DETECTED,
        detected_bbox=BoundingBox(ymin=50, xmin=50, ymax=300, xmax=300),
        review_status=ReviewStatus.ACCEPTED,
        sync_status=SyncStatus.SYNCED,
        active_artifact_version=1,
        active_artifact_uri=uri,
    )


# ---------------------------------------------------------------------------
# 1. DTO Immutability Tests
# ---------------------------------------------------------------------------

def test_dto_immutability():
    vref = VisualRegionRefDTO(
        occurrence_id="occ_1",
        source="crop.jpg",
        image_path="/path/crop.jpg",
        region_id="018f2d5a12347a8b9cde567812345678",
        is_associated=True,
    )
    with pytest.raises(FrozenInstanceError):
        vref.is_associated = False  # type: ignore

    seg = InlineSegmentDTO(segment_type="text", text_html="Hello")
    with pytest.raises(FrozenInstanceError):
        seg.text_html = "Modified"  # type: ignore

    occ = RegionOccurrenceRef(node_index=0, occurrence_id="occ_1")
    with pytest.raises(FrozenInstanceError):
        occ.node_index = 5  # type: ignore

    node = MarkdownNodeDTO(node_id="n1", node_type="paragraph")
    with pytest.raises(FrozenInstanceError):
        node.node_type = "heading"  # type: ignore

    doc = MarkdownDocumentDTO(
        job_id=1,
        version=1,
        nodes=(node,),
        region_to_occurrences={"rid": (occ,)},
    )
    with pytest.raises(FrozenInstanceError):
        doc.job_id = 2  # type: ignore


# ---------------------------------------------------------------------------
# 2. Pure Helper Tests
# ---------------------------------------------------------------------------

def test_slugify_and_helpers():
    assert _slugify("Hello, World! 123") == "hello-world-123"
    assert _slugify("   ") == "heading"
    assert _normalize_whitespace("  line 1 \n\n  line 2\t  ") == "line 1 line 2"

    assert _is_safe_url("https://example.com") is True
    assert _is_safe_url("http://example.com/foo") is True
    assert _is_safe_url("file:///local/path.jpg") is True
    assert _is_safe_url("#section-1") is True
    assert _is_safe_url("javascript:alert(1)") is False
    assert _is_safe_url("region://018f2d5a12347a8b9cde567812345678") is False
    assert _is_safe_url("data:image/png;base64,123") is False
    assert _is_safe_url("") is False
    assert _is_safe_url(None) is False


# ---------------------------------------------------------------------------
# 3. Deterministic Node ID and Requirement 3 Invariant Tests
# ---------------------------------------------------------------------------

def test_deterministic_node_id_generation():
    parser = MarkdownItParser()
    service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)

    raw_md = """# Introduction
This is the first paragraph.

This is the second paragraph.

This is the first paragraph.
"""
    doc_dto = service.render_text(raw_md, active_regions=[], job_id=100)
    nodes = doc_dto.nodes

    assert len(nodes) == 4
    # Heading
    assert nodes[0].node_id == "h1_introduction_1"
    # Paragraph 1
    assert nodes[1].node_id.startswith("p_")
    assert nodes[1].node_id.endswith("_1")
    # Paragraph 2
    assert nodes[2].node_id.startswith("p_")
    assert nodes[2].node_id.endswith("_1")
    # Duplicate Paragraph 1 gets occ=2
    assert nodes[3].node_id.startswith("p_")
    assert nodes[3].node_id.endswith("_2")
    # Both identical paragraphs share the same content hash prefix
    assert nodes[1].node_id[:-2] == nodes[3].node_id[:-2]


def test_requirement_3_inserting_unrelated_block_preserves_existing_id():
    """
    Requirement 3 Proof:
    Inserting an unrelated block before an existing block must NOT invalidate
    the existing block's identity.
    """
    parser = MarkdownItParser()
    service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)

    doc_v1 = """# Section A
Existing paragraph to track.
"""
    dto_v1 = service.render_text(doc_v1, active_regions=[], job_id=100)
    tracked_id_v1 = dto_v1.nodes[1].node_id

    # Insert an unrelated block at position 0
    doc_v2 = """# Unrelated Heading
Newly inserted text here.

# Section A
Existing paragraph to track.
"""
    dto_v2 = service.render_text(doc_v2, active_regions=[], job_id=100)

    # In v2, the tracked paragraph is at index 3 instead of index 1
    tracked_node_v2 = dto_v2.nodes[3]
    assert "Existing paragraph to track." in tracked_node_v2.raw_markdown
    assert tracked_node_v2.node_id == tracked_id_v1


# ---------------------------------------------------------------------------
# 4. RichText Sanitization and Tag Whitelist Tests
# ---------------------------------------------------------------------------

def test_richtext_sanitization_and_injection_defense():
    parser = MarkdownItParser()
    service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)

    raw_md = """Here is **bold**, *italic*, `inline_code()`, and a <script>alert('xss')</script> tag.

Also a [Safe Link](https://polpot.dev) and a [Malicious Link](javascript:exploit()) and an injected [Region Link](region://fake-id).
"""
    dto = service.render_text(raw_md, active_regions=[], job_id=100)

    p1_content = dto.nodes[0].content
    assert "<b>bold</b>" in p1_content
    assert "<i>italic</i>" in p1_content
    assert "<code>inline_code()</code>" in p1_content
    # Raw HTML <script> is safely escaped by parser or projection
    assert "<script>" not in p1_content
    assert "&lt;script&gt;" in p1_content or "alert" in p1_content

    p2_content = dto.nodes[1].content
    assert '<a href="https://polpot.dev">Safe Link</a>' in p2_content
    # javascript: target is never emitted as an active href
    assert '<a href="javascript:' not in p2_content
    # raw region:// target is sanitized to # (not allowed from markdown source)
    assert '<a href="#">Region Link</a>' in p2_content


# ---------------------------------------------------------------------------
# 5. Mixed Inline Segments Rendering & Inverted Index (P1 Requirement)
# ---------------------------------------------------------------------------

def test_mixed_inline_segments_and_inverted_index():
    parser = MarkdownItParser()
    service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)

    region1 = make_test_region(
        job_id=100,
        page_number=1,
        display_order=1,
        region_id="a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d",
        uri="file:///data/job_100/crop_1.jpg",
    )
    region2 = make_test_region(
        job_id=100,
        page_number=2,
        display_order=2,
        region_id="f1e2d3c4b5a64987ba654321fedcba98",
        uri="file:///data/job_100/crop_2.jpg",
    )

    # Mixed inline paragraph: text + image 1 + text + image 2 + text
    raw_md = """We observe figure ![[crop_1.jpg|region_id=a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d]] as well as diagram ![[crop_2.jpg|region_id=f1e2d3c4-b5a6-4987-ba65-4321fedcba98]] in the document."""

    dto = service.render_text(raw_md, active_regions=[region1, region2], job_id=100)

    assert len(dto.nodes) == 1
    node = dto.nodes[0]
    assert node.node_type == "paragraph"
    assert len(node.regions) == 2
    assert len(node.segments) == 5

    # Segment 0: text
    assert node.segments[0].segment_type == "text"
    assert "We observe figure " in node.segments[0].text_html

    # Segment 1: image
    assert node.segments[1].segment_type == "image"
    assert node.segments[1].image_ref.region_id == "a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d"
    assert node.segments[1].image_ref.is_associated is True
    assert node.segments[1].image_ref.image_path == "/data/job_100/crop_1.jpg"
    assert node.segments[1].image_ref.display_order == 1
    assert node.segments[1].image_ref.page_number == 1

    # Segment 2: text
    assert node.segments[2].segment_type == "text"
    assert " as well as diagram " in node.segments[2].text_html

    # Segment 3: image
    assert node.segments[3].segment_type == "image"
    assert node.segments[3].image_ref.region_id == "f1e2d3c4b5a64987ba654321fedcba98"
    assert node.segments[3].image_ref.is_associated is True
    assert node.segments[3].image_ref.display_order == 2
    assert node.segments[3].image_ref.page_number == 2

    # Segment 4: text
    assert node.segments[4].segment_type == "text"
    assert " in the document." in node.segments[4].text_html

    # Full content has synthesized region:// hyperlinks
    assert '<a href="region://a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d">' in node.content
    assert '<a href="region://f1e2d3c4b5a64987ba654321fedcba98">' in node.content

    # Inverted Index
    assert "a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d" in dto.region_to_occurrences
    assert "f1e2d3c4b5a64987ba654321fedcba98" in dto.region_to_occurrences

    occ_ref_1 = dto.region_to_occurrences["a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d"][0]
    assert occ_ref_1.node_index == 0
    assert occ_ref_1.occurrence_id == node.segments[1].image_ref.occurrence_id


def test_stale_explicit_region_is_not_associated_and_not_in_index():
    parser = MarkdownItParser()
    service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)

    raw_md = "Here is stale image: ![[crop.jpg|region_id=99999999-9999-4999-9999-999999999999]]"
    dto = service.render_text(raw_md, active_regions=[], job_id=100)

    node = dto.nodes[0]
    assert len(node.regions) == 1
    vref = node.regions[0]
    assert vref.region_id == "99999999-9999-4999-9999-999999999999"
    assert vref.is_associated is False
    assert vref.display_order is None

    # Stale region does NOT generate region:// link in RichText
    assert "region://" not in node.content

    # Stale region is NOT in region_to_occurrences
    assert "99999999-9999-4999-9999-999999999999" not in dto.region_to_occurrences
    assert len(dto.region_to_occurrences) == 0


# ---------------------------------------------------------------------------
# 6. Full Document Loading via UnitOfWork & Storage Tests
# ---------------------------------------------------------------------------

def test_load_document_success():
    region = make_test_region(job_id=42, region_id="a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d")
    job = Job(
        id=42,
        file_path="/data/test.pdf",
        file_name="test.pdf",
        status=JobStatus.DONE,
        output_path="/data/artifacts/job_42/output_42_v3.md",
    )

    md_content = b"# Document Title\n\nSome text content with ![[crop.jpg|region_id=a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d]]\n"
    storage = MockStorage({"output_42_v3.md": md_content})
    job_repo = MockJobRepo([job])
    region_repo = MockVisualRegionRepo([region])
    uow = MockUnitOfWork(job_repo, region_repo)
    factory = MockUowFactory(uow)
    parser = MarkdownItParser()

    service = MarkdownViewerService(parser=parser, uow_factory=factory, storage=storage)
    doc_dto = service.load_document(42)

    assert doc_dto.job_id == 42
    assert doc_dto.version == 3
    assert len(doc_dto.nodes) == 2
    assert doc_dto.nodes[0].node_type == "heading"
    assert doc_dto.nodes[1].node_type == "paragraph"
    assert "a1b2c3d4e5f64a7b8c9d0e1f2a3b4c5d" in doc_dto.region_to_occurrences


def test_load_document_failures():
    job_repo = MockJobRepo([])
    region_repo = MockVisualRegionRepo([])
    uow = MockUnitOfWork(job_repo, region_repo)
    factory = MockUowFactory(uow)
    parser = MarkdownItParser()
    storage = MockStorage()

    service = MarkdownViewerService(parser=parser, uow_factory=factory, storage=storage)

    # Non-existent job raises EntityNotFoundError
    with pytest.raises(EntityNotFoundError):
        service.load_document(999)

    # Job without output_path raises DomainError
    job_no_output = Job(id=50, file_path="/p", file_name="f", status=JobStatus.PROCESSING, output_path=None)
    job_repo._jobs[50] = job_no_output
    with pytest.raises(DomainError, match="no committed output Markdown"):
        service.load_document(50)

    # Job with missing artifact file raises DomainError
    job_missing_art = Job(id=51, file_path="/p", file_name="f", status=JobStatus.DONE, output_path="/data/output_51_v1.md")
    job_repo._jobs[51] = job_missing_art
    with pytest.raises(DomainError, match="missing"):
        service.load_document(51)


# ---------------------------------------------------------------------------
# 7. Clean Architecture Boundary Tests (Zero Qt/PySide6/sqlite3/markdown_it)
# ---------------------------------------------------------------------------

def test_clean_architecture_imports():
    disallowed_modules = {"PySide6", "QtCore", "QtGui", "QtWidgets", "QtQuick", "QtQml", "sqlite3", "markdown_it"}

    files_to_check = [
        "application/dto/markdown_dto.py",
        "application/services/markdown_viewer_service.py",
    ]

    for rel_path in files_to_check:
        full_path = os.path.join(os.path.dirname(__file__), "..", "..", rel_path)
        with open(full_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=rel_path)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    assert root_pkg not in disallowed_modules, f"Disallowed import '{alias.name}' in {rel_path}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    assert root_pkg not in disallowed_modules, f"Disallowed import from '{node.module}' in {rel_path}"
