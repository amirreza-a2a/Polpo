# ============================================================
#  application/services/quarto_pdf_export_service.py
#  Quarto PDF Export Application Orchestration Service
#  Phase 13 — Ticket #103
# ============================================================

"""
Quarto PDF Export Application Orchestration Service.

Security Trust Boundary Disclosure (Mandatory F-03):
---------------------------------------------------
Child processes execute under ordinary desktop user OS privileges.
The compilation flag -no-shell-escape strictly blocks arbitrary shell command
execution and subprocess invocation via \\write18. However, -no-shell-escape does NOT
provide operating-system filesystem read sandboxing for native TeX primitives
(such as \\input and \\openin).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Union

from application.dto.pdf_export_dto import ExportPdfCommand, PdfExportResultDTO
from application.ports.pdf_export_port import (
    IPdfArtifactPublisher,
    IPdfExportEngine,
    IPdfRuntimeDiscovery,
    PdfExportRequest,
)
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.services.export_package_service import (
    DestinationAlreadyExistsError,
    DestinationDirectoryNotFoundError,
    ExportSourceNotFoundError,
    derive_document_slug,
)
from application.services.metadata_normalizer import normalize_quarto_pdf_metadata
from core.exceptions.domain_exceptions import (
    CanonicalDocumentIntegrityError,
    EntityNotFoundError,
    PdfExportCancelledError,
    PublicationInProgressError,
)

logger = logging.getLogger(__name__)


def _parse_yaml_scalar(val_str: str) -> Any:
    """Parses scalar YAML string into typed Python primitives (bool, int, float, str)."""
    s = val_str.strip()
    if not s:
        return ""
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.lower() in ("null", "~"):
        return None
    # Quoted string
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        try:
            return json.loads(s) if s.startswith('"') else s[1:-1]
        except Exception:
            return s[1:-1]
    # Numbers
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _extract_raw_front_matter(markdown: str) -> Dict[str, Any]:
    """
    Extracts raw YAML front matter mapping from canonical Markdown source text.
    Handles nested sections and list elements without third-party dependencies.
    """
    if not (markdown.startswith("---\n") or markdown.startswith("---\r\n")):
        return {}

    lines = markdown.splitlines()
    if len(lines) < 3 or not lines[1].strip():
        return {}

    closing_index = -1
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            closing_index = i
            break

    if closing_index == -1:
        return {}

    yaml_lines = lines[1:closing_index]
    result: Dict[str, Any] = {}

    # Simple hierarchical line parser
    current_key: Optional[str] = None
    sub_key: Optional[str] = None
    current_list: Optional[List[Any]] = None

    for raw_line in yaml_lines:
        line = raw_line.rstrip()
        if not line or line.strip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        # List item
        if stripped.startswith("- "):
            item_val = stripped[2:].strip()
            if current_list is not None:
                current_list.append(_parse_yaml_scalar(item_val))
            continue

        # Key-value pair: key: value
        colon_pos = stripped.find(":")
        if colon_pos != -1:
            k = stripped[:colon_pos].strip()
            v = stripped[colon_pos + 1 :].strip()

            if indent == 0:
                current_key = k
                sub_key = None
                current_list = None
                if not v:
                    result[k] = {}
                else:
                    result[k] = _parse_yaml_scalar(v)
            elif indent == 2 and current_key:
                if isinstance(result.get(current_key), dict):
                    sub_key = k
                    if not v:
                        result[current_key][k] = {}
                    else:
                        result[current_key][k] = _parse_yaml_scalar(v)
            elif indent >= 4 and current_key and sub_key:
                parent_map = result.get(current_key, {})
                if isinstance(parent_map, dict) and isinstance(parent_map.get(sub_key), dict):
                    parent_map[sub_key][k] = _parse_yaml_scalar(v)

    return result


class QuartoPdfExportService:
    """
    Application service orchestrating canonical document publication-grade PDF export.

    Security Trust Boundary Disclosure (Mandatory F-03):
    Child processes execute under ordinary desktop user privileges.
    -no-shell-escape blocks arbitrary command execution via \\write18 but does
    not provide OS-level filesystem read sandboxing for TeX primitives.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        export_engine: IPdfExportEngine,
        publisher: IPdfArtifactPublisher,
        artifacts_dir: Optional[Union[Path, str]] = None,
        discovery: Optional[IPdfRuntimeDiscovery] = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._export_engine = export_engine
        self._publisher = publisher
        self._artifacts_dir = Path(artifacts_dir or "artifacts").resolve()
        self._discovery = discovery

    def export_pdf(self, cmd: ExportPdfCommand) -> PdfExportResultDTO:
        """
        Orchestrates validation, OCC verification, normalization, compilation,
        and atomic publication of a canonical document version into a PDF artifact.

        Args:
            cmd: ExportPdfCommand containing job_id, version, destination, and policies.

        Returns:
            PdfExportResultDTO with target path, page count, duration, and warnings.

        Raises:
            PdfExportCancelledError: If cancellation callback signals abort.
            DestinationDirectoryNotFoundError: If destination directory is missing.
            DestinationAlreadyExistsError: If destination exists and overwrite=False.
            EntityNotFoundError: If requested job does not exist.
            ExportSourceNotFoundError: If canonical document version is missing.
            CanonicalDocumentIntegrityError: If document is quarantined or hash mismatches.
            MissingLatexPackageError: If LaTeX packages are missing.
            PdfCompilationError: If compilation fails.
            PdfCompilationTimeoutError: If compilation times out.
        """
        # 1. Early cooperative cancellation check
        if cmd.is_cancelled and cmd.is_cancelled():
            raise PdfExportCancelledError("PDF export was cancelled before starting.")

        # 2. Destination pre-flight validation
        dest = Path(cmd.destination_path).resolve()
        if not dest.parent.exists() or not dest.parent.is_dir():
            raise DestinationDirectoryNotFoundError(
                f"Destination directory does not exist: '{dest.parent}'"
            )

        if dest.is_dir():
            raise DestinationAlreadyExistsError(
                f"Destination path is an existing directory: '{dest}'"
            )

        if dest.exists() and not cmd.overwrite:
            raise DestinationAlreadyExistsError(
                f"Destination file already exists: '{dest}'"
            )

        # 3. Canonical Document Version Resolution under OCC
        with self._uow_factory.create() as uow:
            # Precondition: No publication intent in progress for this job
            active_intent = uow.publish_intents.get_by_job_id(cmd.job_id)
            if active_intent:
                raise PublicationInProgressError(cmd.job_id)

            job = uow.jobs.get_by_id(cmd.job_id)
            if not job:
                raise EntityNotFoundError("Job", cmd.job_id)

            version_record = uow.document_versions.get_by_version(cmd.job_id, cmd.version)
            if not version_record:
                raise ExportSourceNotFoundError(
                    f"No canonical document version found for job {cmd.job_id} (version {cmd.version})."
                )

            if version_record.integrity_status == "QUARANTINED":
                raise CanonicalDocumentIntegrityError(
                    f"Cannot export job {cmd.job_id} version {version_record.version}: canonical document is QUARANTINED."
                )

        # 4. Canonical File Verification & SHA-256 Check
        doc_file = Path(version_record.output_path).resolve()
        if not doc_file.is_file():
            raise ExportSourceNotFoundError(
                f"Canonical document file does not exist on disk: {version_record.output_path}"
            )

        try:
            raw_bytes = doc_file.read_bytes()
        except (OSError, PermissionError) as e:
            raise ExportSourceNotFoundError(
                f"Cannot read canonical document file at '{doc_file}': {e}"
            ) from e

        actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        if version_record.sha256 and version_record.sha256 != actual_sha256:
            raise CanonicalDocumentIntegrityError(
                f"Canonical document SHA-256 mismatch for job {cmd.job_id} version {version_record.version}: "
                f"expected '{version_record.sha256}', calculated '{actual_sha256}'."
            )

        try:
            canonical_markdown = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise CanonicalDocumentIntegrityError(
                f"Canonical document at '{doc_file}' is not valid UTF-8: {e}"
            ) from e

        # 5. Metadata Extraction & Fallback Title
        raw_metadata = _extract_raw_front_matter(canonical_markdown)
        fallback_title = derive_document_slug(job, cmd.job_id)

        # 6. TeX Distribution Readiness Check
        is_tinytex = False
        if self._discovery is not None:
            try:
                readiness = self._discovery.probe_readiness()
                is_tinytex = readiness.is_tinytex
            except Exception:
                pass

        # 7. Metadata Normalization (enforcing F-01 & F-02)
        normalized_metadata = normalize_quarto_pdf_metadata(
            raw_meta=raw_metadata,
            is_tinytex=is_tinytex,
            default_title=fallback_title,
        )

        # 8. Compilation Delegation to IPdfExportEngine
        job_artifacts_dir = (self._artifacts_dir / f"job_{cmd.job_id}").resolve()
        job_artifacts_dir.mkdir(parents=True, exist_ok=True)

        render_request = PdfExportRequest(
            job_id=cmd.job_id,
            canonical_markdown=canonical_markdown,
            raw_metadata=normalized_metadata,
            job_artifacts_dir=job_artifacts_dir,
            is_tinytex=is_tinytex,
            is_cancelled=cmd.is_cancelled,
        )

        render_result = self._export_engine.render_pdf(render_request)

        # 9. Atomic Artifact Publication via IPdfArtifactPublisher
        publish_result = self._publisher.publish(
            source_compiled_pdf=render_result.compiled_pdf_path,
            destination_path=dest,
            overwrite=cmd.overwrite,
        )

        # 10. Packaging Result DTO
        cleanup_warning: Optional[str] = publish_result.cleanup_warning
        if not cleanup_warning and render_result.warnings:
            cleanup_warning = "; ".join(render_result.warnings)

        return PdfExportResultDTO(
            destination_path=publish_result.destination_path,
            canonical_version=cmd.version,
            page_count=render_result.page_count,
            file_size_bytes=publish_result.published_bytes,
            duration_seconds=render_result.duration_seconds,
            cleanup_warning=cleanup_warning,
        )
