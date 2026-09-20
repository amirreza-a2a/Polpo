# ============================================================
#  application/services/legacy_document_backfill.py
#  Idempotent Legacy Canonical Document Version Backfill Routine
# ============================================================

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
from typing import Optional, Union

from application.ports.unit_of_work import IUnitOfWork, IUnitOfWorkFactory
from core.entities.artifact import resolve_canonical_file_path
from core.entities.document_version import DocumentVersionRecord
from core.markdown.version import parse_canonical_markdown_version

logger = logging.getLogger("application.services.legacy_document_backfill")


@dataclass(frozen=True)
class BackfillSummary:
    """Summary of operations performed during legacy document version backfill."""
    scanned_jobs: int
    valid_documents: int
    quarantined_documents: int
    skipped_jobs: int
    newly_inserted_versions: int


def _format_iso_dt(dt: Optional[datetime]) -> Optional[str]:
    """Formats datetime as UTC ISO 8601 string."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def compute_file_sha256(path: Path) -> str:
    """
    Computes the SHA-256 digest of a local file in 64 KiB chunks.
    A 0-byte file deterministically produces the standard empty string digest
    ('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855').
    """
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def backfill_legacy_document_versions(
    uow_or_factory: Union[IUnitOfWork, IUnitOfWorkFactory],
) -> BackfillSummary:
    """
    Inspects all pre-existing jobs with non-null canonical output pointers and
    deterministically populates immutable document_versions rows.

    Invariants:
    1. A job with an active output pointer cannot simultaneously appear to be at document version 0.
    2. Version is derived from the canonical filename pattern via parse_canonical_markdown_version(),
       never from output_artifact_version_watermark.
    3. Missing or unreadable files are quarantined with sha256 = None and integrity_status = 'QUARANTINED'.
       The job's error_message is updated with an explanatory diagnostic.
    4. Empty (0-byte) readable canonical Markdown files receive their real SHA-256 digest and 'VALID' status.
    5. The operation is fully idempotent: repeated executions insert zero duplicate rows (INSERT OR IGNORE).
    6. Record created_at uses job.updated_at if available, otherwise migration execution time.
       This is explicitly recorded as approximate migration-time metadata, not a historical timestamp.
    """
    if isinstance(uow_or_factory, IUnitOfWorkFactory):
        with uow_or_factory.create() as uow:
            return _execute_backfill(uow)
    else:
        return _execute_backfill(uow_or_factory)


def _execute_backfill(uow: IUnitOfWork) -> BackfillSummary:
    jobs = uow.jobs.get_jobs_with_output()
    now_iso = _format_iso_dt(datetime.now(timezone.utc))

    scanned_count = len(jobs)
    valid_count = 0
    quarantined_count = 0
    skipped_count = 0
    inserted_count = 0

    for job in jobs:
        if not job.output_path or not job.output_path.strip():
            skipped_count += 1
            continue

        raw_output_path = job.output_path.strip()
        version = parse_canonical_markdown_version(raw_output_path)
        if version <= 0:
            version = 1

        # Determine approximate created_at metadata from job.updated_at
        created_at_iso = _format_iso_dt(job.updated_at) or now_iso

        sha256: Optional[str] = None
        integrity_status: str = "VALID"
        diagnostic: Optional[str] = None

        try:
            resolved_path = resolve_canonical_file_path(raw_output_path)
            if not resolved_path.is_file():
                integrity_status = "QUARANTINED"
                sha256 = None
                diagnostic = f"[INTEGRITY_QUARANTINE] Canonical document file missing on disk: {resolved_path}"
            else:
                sha256 = compute_file_sha256(resolved_path)
                integrity_status = "VALID"
        except (OSError, PermissionError) as exc:
            integrity_status = "QUARANTINED"
            sha256 = None
            diagnostic = f"[INTEGRITY_QUARANTINE] Canonical document file unreadable: {exc}"
        except Exception as exc:
            integrity_status = "QUARANTINED"
            sha256 = None
            diagnostic = f"[INTEGRITY_QUARANTINE] Failed to resolve or access canonical document file: {exc}"

        if integrity_status == "VALID":
            valid_count += 1
        else:
            quarantined_count += 1
            logger.warning(
                "Quarantining legacy document version for job %s (path: %s): %s",
                job.id,
                raw_output_path,
                diagnostic,
            )
            current_err = job.error_message
            if current_err:
                if "[INTEGRITY_QUARANTINE]" not in current_err:
                    new_err = f"{current_err} | {diagnostic}"
                else:
                    new_err = current_err
            else:
                new_err = diagnostic
            uow.jobs.update_status(job.id, job.status, error_message=new_err)

        record = DocumentVersionRecord(
            job_id=job.id,
            version=version,
            output_path=raw_output_path,
            sha256=sha256,
            integrity_status=integrity_status,
            published_by="LEGACY_BACKFILL",
            created_at=created_at_iso,
        )

        was_inserted = uow.document_versions.insert_document_version_if_absent(record)
        if was_inserted:
            inserted_count += 1

    uow.commit()

    return BackfillSummary(
        scanned_jobs=scanned_count,
        valid_documents=valid_count,
        quarantined_documents=quarantined_count,
        skipped_jobs=skipped_count,
        newly_inserted_versions=inserted_count,
    )
