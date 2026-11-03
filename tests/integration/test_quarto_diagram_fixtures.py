# ============================================================
#  tests/integration/test_quarto_diagram_fixtures.py
#  End-to-End & Integration Tests for Standard Quarto Fixtures
# ============================================================

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from typing import Mapping, Optional, Sequence, Union
from unittest.mock import MagicMock

import pytest

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

from application.dto.markdown_dto import MarkdownDocumentDTO, MarkdownNodeDTO
from application.dto.pdf_export_dto import ExportPdfCommand, PdfExportResultDTO
from application.ports.diagram_renderer import (
    DiagramRenderError,
    DiagramRenderRequest,
    DiagramRenderResult,
    DiagramSyntaxError,
    IDiagramRenderer,
)
from application.services.markdown_viewer_service import MarkdownViewerService
from application.services.quarto_pdf_export_service import QuartoPdfExportService
from core.entities.document_version import DocumentVersionRecord
from core.entities.job import Job
from infrastructure.diagram.cache import DiagramSvgCache
from infrastructure.markdown.pandoc_parser import PandocParser
from infrastructure.pdf.atomic_publisher import AtomicPdfPublisher
from infrastructure.pdf.quarto_adapter import QuartoPdfAdapter
from infrastructure.pdf.runtime_discovery import QuartoRuntimeDiscovery
from infrastructure.pdf.staging_workspace import StagingWorkspaceManager
from infrastructure.pdf.subprocess_supervisor import SubprocessSupervisor
from infrastructure.pdf.tex_log_sanitizer import TexLogSanitizer

from tests.fixtures.quarto_diagram_fixtures import (
    FIXTURE_A_SIMPLE_TIKZ,
    FIXTURE_B_PGFPLOTS_FUNCTION,
    FIXTURE_C_DISCRETE_SIGNAL_YCOMB,
    FIXTURE_D_PERSIAN_ENGLISH_DIAGRAM,
    FIXTURE_E_MULTIPLE_DIAGRAMS,
    FIXTURE_F_INVALID_DIAGRAMS,
    FIXTURE_G_MIXED_DOCUMENT,
)


def is_quarto_and_xelatex_installed() -> bool:
    return shutil.which("quarto") is not None and shutil.which("xelatex") is not None


class StubDiagramRenderer(IDiagramRenderer):
    """Predictable diagram renderer double for desktop preview pipeline verification."""

    def __init__(self, cache: Optional[DiagramSvgCache] = None) -> None:
        self.cache = cache or DiagramSvgCache()
        self.render_calls = 0

    def probe_readiness(self) -> bool:
        return True

    def render(self, request: DiagramRenderRequest, cancel_fn=None) -> DiagramRenderResult:
        self.render_calls += 1
        h = request.compute_hash()
        cached = self.cache.get(h)
        if cached is not None:
            return cached

        res = DiagramRenderResult(
            hash=h,
            svg_xml=f'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200"><desc>{h}</desc></svg>',
            width=300.0,
            height=200.0,
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
        for r in requests:
            outcomes[r.compute_hash()] = self.render(r, cancel_fn=cancel_fn)
        return outcomes


def build_viewer_service(renderer: Optional[IDiagramRenderer] = None) -> MarkdownViewerService:
    parser = PandocParser()
    uow_factory = MagicMock()
    storage = MagicMock()
    return MarkdownViewerService(
        parser=parser,
        uow_factory=uow_factory,
        storage=storage,
        diagram_renderer=renderer,
    )


class TestQuartoDiagramPreviewFixtures:
    """Verifies that all standardized Quarto fixtures A through G correctly project into the preview pipeline."""

    def test_fixture_a_simple_tikz_preview(self):
        renderer = StubDiagramRenderer()
        service = build_viewer_service(renderer)

        doc = service.render_text(raw_text=FIXTURE_A_SIMPLE_TIKZ, active_regions=[], job_id=1)
        assert len(doc.nodes) == 1
        node = doc.nodes[0]
        assert node.node_type == "diagram"
        assert "\\node" in node.diagram_source
        assert node.diagram_status == "rendered"
        assert node.diagram_width == 300.0
        assert node.has_error is False

    def test_fixture_b_pgfplots_function_preview(self):
        renderer = StubDiagramRenderer()
        service = build_viewer_service(renderer)

        doc = service.render_text(raw_text=FIXTURE_B_PGFPLOTS_FUNCTION, active_regions=[], job_id=2)
        assert len(doc.nodes) == 1
        node = doc.nodes[0]
        assert node.node_type == "diagram"
        assert "\\addplot" in node.diagram_source
        assert node.diagram_status == "rendered"

    def test_fixture_c_discrete_signal_preview(self):
        renderer = StubDiagramRenderer()
        service = build_viewer_service(renderer)

        doc = service.render_text(raw_text=FIXTURE_C_DISCRETE_SIGNAL_YCOMB, active_regions=[], job_id=3)
        assert len(doc.nodes) == 1
        node = doc.nodes[0]
        assert node.node_type == "diagram"
        assert "ycomb" in node.diagram_source
        assert node.diagram_status == "rendered"

    def test_fixture_d_persian_english_diagram_preview(self):
        renderer = StubDiagramRenderer()
        service = build_viewer_service(renderer)

        doc = service.render_text(raw_text=FIXTURE_D_PERSIAN_ENGLISH_DIAGRAM, active_regions=[], job_id=4)
        node_types = [n.node_type for n in doc.nodes]
        assert "heading" in node_types
        assert "paragraph" in node_types
        assert "diagram" in node_types

        # Mathematical formulas are projected as discrete math segments within paragraphs
        math_segments = [
            seg
            for n in doc.nodes
            if n.segments
            for seg in n.segments
            if getattr(seg, "segment_type", "") == "math"
        ]
        assert len(math_segments) >= 1

        diag_nodes = [n for n in doc.nodes if n.node_type == "diagram"]
        assert len(diag_nodes) == 1
        assert diag_nodes[0].diagram_status == "rendered"

    def test_fixture_e_multiple_diagrams_preview(self):
        renderer = StubDiagramRenderer()
        service = build_viewer_service(renderer)

        doc = service.render_text(raw_text=FIXTURE_E_MULTIPLE_DIAGRAMS, active_regions=[], job_id=5)
        diag_nodes = [n for n in doc.nodes if n.node_type == "diagram"]
        assert len(diag_nodes) == 2

        # Each diagram has independent source and content-addressed hash
        assert diag_nodes[0].diagram_hash != diag_nodes[1].diagram_hash
        assert "\\begin{circuitikz}" in diag_nodes[0].diagram_source
        assert "\\begin{axis}" in diag_nodes[1].diagram_source

    def test_fixture_f_invalid_diagrams_graceful_handling(self):
        renderer = StubDiagramRenderer()
        service = build_viewer_service(renderer)

        doc = service.render_text(raw_text=FIXTURE_F_INVALID_DIAGRAMS, active_regions=[], job_id=6)
        diag_nodes = [n for n in doc.nodes if n.node_type == "diagram"]
        assert len(diag_nodes) >= 1

        # Incomplete environment during typing must be flagged as syntax error without crashing
        unclosed_node = diag_nodes[0]
        assert unclosed_node.has_error is True
        assert unclosed_node.diagram_status == "error"
        assert "Unclosed" in unclosed_node.error_message

        # Document prose must remain visible despite diagram error
        heading_nodes = [n for n in doc.nodes if n.node_type == "heading"]
        assert len(heading_nodes) >= 1

    def test_fixture_g_mixed_document_full_pipeline(self):
        renderer = StubDiagramRenderer()
        service = build_viewer_service(renderer)

        doc = service.render_text(raw_text=FIXTURE_G_MIXED_DOCUMENT, active_regions=[], job_id=7)
        node_types = set(n.node_type for n in doc.nodes)

        # Asserts that all rich document structures are preserved simultaneously
        assert "heading" in node_types
        assert "paragraph" in node_types
        assert "blockquote" in node_types
        assert "diagram" in node_types
        assert "table_fallback" in node_types
        assert "code_block" in node_types
        assert "list" in node_types

        # Verify math formulas are preserved inside inline segments
        math_segments = [
            seg
            for n in doc.nodes
            if n.segments
            for seg in n.segments
            if getattr(seg, "segment_type", "") == "math"
        ]
        assert len(math_segments) >= 2

        diag_nodes = [n for n in doc.nodes if n.node_type == "diagram"]
        assert len(diag_nodes) == 2  # CircuitTikZ filter and PGFPlots frequency response
        for d in diag_nodes:
            assert d.diagram_status == "rendered"
            assert d.has_error is False

        # Verify ordinary Python code block is preserved and not mistaken for diagram
        code_nodes = [n for n in doc.nodes if n.node_type == "code_block"]
        assert len(code_nodes) == 1
        assert code_nodes[0].language == "python"
        assert "np.logspace" in code_nodes[0].content


def _build_test_uow_factory(job_id: int, version: int, markdown_content: str, output_md_path: Path):
    raw_bytes = markdown_content.encode("utf-8")
    output_md_path.write_bytes(raw_bytes)
    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    job = MagicMock(spec=Job)
    job.id = job_id
    job.file_name = f"doc_{job_id}.pdf"

    version_record = DocumentVersionRecord(
        job_id=job_id,
        version=version,
        output_path=str(output_md_path),
        sha256=content_hash,
        integrity_status="VALID",
    )

    mock_uow = MagicMock()
    mock_uow.publish_intents.get_by_job_id.return_value = None
    mock_uow.jobs.get_by_id.return_value = job
    mock_uow.document_versions.get_by_version.return_value = version_record

    uow_factory = MagicMock()
    uow_factory.create.return_value.__enter__.return_value = mock_uow
    return uow_factory


@pytest.mark.integration
@pytest.mark.skipif(
    not is_quarto_and_xelatex_installed() or fitz is None,
    reason="Quarto, XeLaTeX, or PyMuPDF not available on host",
)
class TestQuartoPdfFixturesIntegration:
    """Verifies real XeLaTeX/Quarto PDF compilation of the standardized diagram fixtures."""

    def test_export_fixture_g_mixed_document_to_pdf(self, tmp_path: Path):
        job_id = 101
        version = 1
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        md_file = artifacts_dir / "canonical.md"

        uow_factory = _build_test_uow_factory(job_id, version, FIXTURE_G_MIXED_DOCUMENT, md_file)
        discovery = QuartoRuntimeDiscovery()
        supervisor = SubprocessSupervisor()
        staging = StagingWorkspaceManager()
        sanitizer = TexLogSanitizer()
        adapter = QuartoPdfAdapter(
            discovery=discovery,
            supervisor=supervisor,
            staging=staging,
            sanitizer=sanitizer,
        )
        publisher = AtomicPdfPublisher()
        service = QuartoPdfExportService(
            uow_factory=uow_factory,
            export_engine=adapter,
            publisher=publisher,
            artifacts_dir=artifacts_dir,
            discovery=discovery,
        )

        dest_pdf = artifacts_dir / "mixed_document.pdf"
        result: PdfExportResultDTO = service.export_pdf(
            ExportPdfCommand(job_id=job_id, version=version, destination_path=dest_pdf, overwrite=True)
        )

        assert result.page_count >= 1
        assert dest_pdf.is_file()

        doc = fitz.open(str(dest_pdf))
        assert doc.page_count >= 1

        # Check that vector drawings from both CircuitTikZ and PGFPlots exist
        drawings_count = sum(len(page.get_drawings()) for page in doc)
        assert drawings_count > 0, "PDF must contain rendered vector drawings from TikZ/PGFPlots"

        # Check text layer does NOT leak raw fenced LaTeX delimiters
        all_text = " ".join(page.get_text() for page in doc)
        assert "```{=latex}" not in all_text
        assert r"\begin{circuitikz}" not in all_text
        assert r"\begin{tikzpicture}" not in all_text
        doc.close()

    def test_export_fixture_d_bilingual_to_pdf(self, tmp_path: Path):
        job_id = 102
        version = 1
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        md_file = artifacts_dir / "canonical.md"

        uow_factory = _build_test_uow_factory(job_id, version, FIXTURE_D_PERSIAN_ENGLISH_DIAGRAM, md_file)
        discovery = QuartoRuntimeDiscovery()
        supervisor = SubprocessSupervisor()
        staging = StagingWorkspaceManager()
        sanitizer = TexLogSanitizer()
        adapter = QuartoPdfAdapter(
            discovery=discovery,
            supervisor=supervisor,
            staging=staging,
            sanitizer=sanitizer,
        )
        publisher = AtomicPdfPublisher()
        service = QuartoPdfExportService(
            uow_factory=uow_factory,
            export_engine=adapter,
            publisher=publisher,
            artifacts_dir=artifacts_dir,
            discovery=discovery,
        )

        dest_pdf = artifacts_dir / "bilingual.pdf"
        result: PdfExportResultDTO = service.export_pdf(
            ExportPdfCommand(job_id=job_id, version=version, destination_path=dest_pdf, overwrite=True)
        )
        assert result.page_count >= 1
        assert dest_pdf.is_file()

        doc = fitz.open(str(dest_pdf))
        assert doc.page_count >= 1
        drawings = sum(len(p.get_drawings()) for p in doc)
        assert drawings > 0, "PDF must contain vector drawings for control loop block diagram"

        import unicodedata
        all_text = " ".join(p.get_text() for p in doc)
        normalized_text = unicodedata.normalize("NFKD", all_text)
        assert "کنترل" in normalized_text
        assert "```{=latex}" not in all_text
        assert r"\begin{tikzpicture}" not in all_text
        doc.close()
