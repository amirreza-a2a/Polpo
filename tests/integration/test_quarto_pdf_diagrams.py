# ============================================================
#  tests/integration/test_quarto_pdf_diagrams.py
#  Integration Tests for Quarto PDF Export with Native LaTeX Diagrams
# ============================================================

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
from unittest.mock import MagicMock

import pytest

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

from application.dto.pdf_export_dto import ExportPdfCommand, PdfExportResultDTO
from application.services.quarto_pdf_export_service import QuartoPdfExportService
from core.entities.document_version import DocumentVersionRecord
from core.entities.job import Job
from infrastructure.pdf.atomic_publisher import AtomicPdfPublisher
from infrastructure.pdf.quarto_adapter import QuartoPdfAdapter
from infrastructure.pdf.runtime_discovery import QuartoRuntimeDiscovery
from infrastructure.pdf.staging_workspace import StagingWorkspaceManager
from infrastructure.pdf.subprocess_supervisor import SubprocessSupervisor
from infrastructure.pdf.tex_log_sanitizer import TexLogSanitizer


def is_quarto_and_xelatex_installed() -> bool:
    """Helper to check if Quarto CLI and XeLaTeX compiler are present on the host."""
    return shutil.which("quarto") is not None and shutil.which("xelatex") is not None


@pytest.fixture
def shared_preamble_path() -> Path:
    p = Path(__file__).parent.parent.parent / "resources" / "templates" / "polpo_preamble.tex"
    assert p.is_file(), f"Shared preamble missing at: {p}"
    return p


def _build_test_uow_factory(
    job_id: int,
    version: int,
    canonical_markdown: str,
    output_md_path: Path,
):
    raw_bytes = canonical_markdown.encode("utf-8")
    output_md_path.write_bytes(raw_bytes)
    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    job = MagicMock(spec=Job)
    job.id = job_id
    job.file_name = f"document_{job_id}.pdf"

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
class TestQuartoPdfDiagramIntegration:
    """Verifies end-to-end PDF export for canonical Quarto documents with technical diagrams."""

    def test_export_pdf_with_tikz_and_pgfplots(self, tmp_path: Path):
        job_id = 42
        version = 1
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        md_file = artifacts_dir / "canonical.md"

        markdown_content = r"""---
title: "آزمایش مدار و دیاگرام‌های فنی"
author: "سامانه پُلپو"
---

# معرفی سیستم

این یک سند آزمایشی به زبان فارسی است که شامل فرمول‌های ریاضی و نمودارهای برداری TikZ و PGFPlots می‌باشد.

رابطه انرژی اینشتین:
$$E = mc^2$$

## نمودار مدار الکتریکی

در شکل زیر یک مدار ساده مقاومتی رسم شده است:

```{=latex}
\begin{circuitikz}[american]
    \draw (0,0) to[R=10] (2,0);
\end{circuitikz}
```

## نمودار منحنی تابع

منحنی زیر با بسته PGFPlots ترسیم شده است:

```{=latex}
\begin{tikzpicture}
    \begin{axis}[width=7cm, height=5cm, xlabel={$x$}, ylabel={$y$}]
        \addplot[domain=0:3, samples=20, blue, thick] {x^2};
    \end{axis}
\end{tikzpicture}
```

پایان سند.
"""
        uow_factory = _build_test_uow_factory(job_id, version, markdown_content, md_file)
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

        dest_pdf = artifacts_dir / "output.pdf"
        command = ExportPdfCommand(
            job_id=job_id,
            version=version,
            destination_path=dest_pdf,
            overwrite=True,
        )

        result: PdfExportResultDTO = service.export_pdf(command)
        assert result.page_count >= 1
        assert result.file_size_bytes > 0
        assert dest_pdf.is_file()
        assert dest_pdf.stat().st_size > 0

        # Verify PDF contents with PyMuPDF
        doc = fitz.open(str(dest_pdf))
        assert doc.page_count >= 1

        # Check vector drawings exist on pages
        total_drawings = sum(len(page.get_drawings()) for page in doc)
        assert total_drawings > 0, "PDF must contain rendered vector drawings from TikZ/PGFPlots"

        # Check no raw LaTeX delimiter leaking into rendered text layer
        all_text = " ".join(page.get_text() for page in doc)
        assert r"\begin{tikzpicture}" not in all_text
        assert r"\begin{circuitikz}" not in all_text
        assert "```{=latex}" not in all_text
        doc.close()
