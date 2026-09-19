# ============================================================
#  application/services/document_publication_service.py
#  Authoritative Crash-Consistent Document Publication Gateway
# ============================================================

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import time
from typing import List, Optional, Union
import uuid

from application.dto.staged_crop import StagedCropHandle
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.services.crop_artifact_staging_service import CropArtifactStagingService
from application.services.legacy_document_backfill import (
    BackfillSummary,
    backfill_legacy_document_versions,
    compute_file_sha256,
)
from core.entities.document_version import DocumentVersionRecord, PublishIntentRecord
from core.exceptions.domain_exceptions import (
    CanonicalDocumentIntegrityError,
    DocumentAlreadyExistsError,
    EntityNotFoundError,
    PublicationInProgressError,
    StaleDocumentVersionError,
)

logger = logging.getLogger("application.services.document_publication_service")


@dataclass(frozen=True)
class RecoveryResult:
    """Outcome of reconciling a crashed publication intent during startup."""
    job_id: int
    intent_id: str
    action: str
    target_version: int


class DocumentPublicationService:
    """
    Sole authoritative gateway for publishing and versioning canonical Markdown documents.
    Enforces durable intent journaling, 4-phase crash consistency, OCC validation against
    the document_versions table, and atomic filesystem activation.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        artifacts_dir: Union[Path, str],
        staging_service: Optional[CropArtifactStagingService] = None,
    ):
        self.uow_factory = uow_factory
        self.artifacts_dir = Path(artifacts_dir).resolve()
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.staging_service = staging_service or CropArtifactStagingService(self.artifacts_dir / ".staging")

    def publish_initial(
        self,
        job_id: int,
        markdown_text: str,
        published_by: str = "SYSTEM",
    ) -> DocumentVersionRecord:
        """
        Publishes the initial canonical document version (version 1) for a job.
        Strictly enforces the 6 preconditions in documented order.
        """
        # Phase 1: Intent Reservation
        intent_id = str(uuid.uuid4())
        target_version = 1
        output_filename = f"output_{job_id}_v1.md"
        data_bytes = markdown_text.encode("utf-8")
        output_sha256 = hashlib.sha256(data_bytes).hexdigest()

        with self.uow_factory.create() as uow:
            uow.begin_immediate()

            # Precondition 1: Job exists
            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

            # Precondition 2: No active publish intent for this job
            active_intent = uow.publish_intents.get_by_job_id(job_id)
            if active_intent:
                raise PublicationInProgressError(job_id)

            latest_ver = uow.document_versions.get_latest(job_id)

            # Precondition 3: Latest version is not QUARANTINED
            if latest_ver and latest_ver.integrity_status == "QUARANTINED":
                raise CanonicalDocumentIntegrityError(
                    f"Cannot publish initial document for job {job_id}: latest version is QUARANTINED."
                )

            # Precondition 4: No prior VALID version exists
            if latest_ver and latest_ver.integrity_status == "VALID":
                raise DocumentAlreadyExistsError(job_id)

            # Precondition 5: No orphan non-null output_path without version record
            if job.output_path and job.output_path.strip() and not latest_ver:
                raise CanonicalDocumentIntegrityError(
                    f"Job {job_id} has active output_path '{job.output_path}' but no document_versions row."
                )

            # Insert intent
            intent = PublishIntentRecord(
                intent_id=intent_id,
                job_id=job_id,
                base_version=0,
                target_version=target_version,
                output_filename=output_filename,
                output_sha256=output_sha256,
                staged_artifacts_manifest="[]",
                status="PENDING",
            )
            uow.publish_intents.insert_intent(intent)
            uow.commit()

        # Phase 2: Staging & Disk Flush
        job_dir = (self.artifacts_dir / f"job_{job_id}").resolve()
        job_dir.mkdir(parents=True, exist_ok=True)
        tmp_file = job_dir / f".{output_filename}.{intent_id}.tmp"

        activated = False
        try:
            with open(tmp_file, "wb") as f:
                f.write(data_bytes)
                f.flush()
                os.fsync(f.fileno())

            with self.uow_factory.create() as uow:
                uow.begin_immediate()
                uow.publish_intents.update_status(intent_id, "FLUSHED")
                uow.commit()

            # Phase 3: Atomic Disk Activation
            final_file = job_dir / output_filename
            os.replace(str(tmp_file), str(final_file))
            activated = True

            # Phase 4: Atomic SQLite Pointer Update
            canonical_uri = final_file.resolve().as_uri()
            with self.uow_factory.create() as uow:
                uow.begin_immediate()
                job = uow.jobs.get_by_id(job_id)
                uow.jobs.update_progress(
                    job_id=job_id,
                    processed_pages=job.processed_pages if job else 0,
                    switch_log=job.api_switch_log if job else [],
                    output_path=canonical_uri,
                )
                record = DocumentVersionRecord(
                    job_id=job_id,
                    version=target_version,
                    output_path=canonical_uri,
                    sha256=output_sha256,
                    integrity_status="VALID",
                    published_by=published_by,
                )
                inserted_record = uow.document_versions.insert_document_version(record)
                uow.publish_intents.delete_intent(intent_id)
                uow.commit()

            return inserted_record

        except Exception:
            if not activated:
                if tmp_file.exists():
                    try:
                        tmp_file.unlink()
                    except OSError:
                        pass
                try:
                    with self.uow_factory.create() as uow:
                        uow.begin_immediate()
                        uow.publish_intents.delete_intent(intent_id)
                        uow.commit()
                except Exception as cleanup_exc:
                    logger.warning("Failed to clean in-process failed intent %s: %s", intent_id, cleanup_exc)
            raise

    def publish_version(
        self,
        job_id: int,
        base_version: int,
        markdown_text: str,
        staged_crops: Optional[List[StagedCropHandle]] = None,
        published_by: str = "SYSTEM",
    ) -> DocumentVersionRecord:
        """
        Publishes an updated canonical document version (base_version + 1) for a job.
        Enforces optimistic concurrency control (OCC) against document_versions and promotes
        any staged visual crop artifacts atomically.
        """
        intent_id = str(uuid.uuid4())
        target_version = base_version + 1
        output_filename = f"output_{job_id}_v{target_version}.md"
        data_bytes = markdown_text.encode("utf-8")
        output_sha256 = hashlib.sha256(data_bytes).hexdigest()

        # Build and verify crop manifest
        manifest_items = []
        if staged_crops:
            for crop in staged_crops:
                crop_path = Path(crop.staging_path)
                if not crop_path.is_file():
                    raise ValueError(f"Staged crop file not found: {crop_path}")
                computed_hash = compute_file_sha256(crop_path)
                if computed_hash != crop.sha256:
                    raise ValueError(f"Staged crop checksum mismatch for {crop.dest_filename}")
                manifest_items.append({
                    "staging_id": crop.staging_id,
                    "region_id": crop.region_id,
                    "artifact_version": crop.artifact_version,
                    "staging_path": str(crop.staging_path),
                    "dest_filename": crop.dest_filename,
                    "sha256": crop.sha256,
                    "size_bytes": crop.size_bytes,
                })

        manifest_json = json.dumps(manifest_items)

        # Phase 1: Intent Reservation
        with self.uow_factory.create() as uow:
            uow.begin_immediate()

            job = uow.jobs.get_by_id(job_id)
            if not job:
                raise EntityNotFoundError("Job", job_id)

            active_intent = uow.publish_intents.get_by_job_id(job_id)
            if active_intent:
                raise PublicationInProgressError(job_id)

            latest_ver = uow.document_versions.get_latest(job_id)
            if not latest_ver:
                raise StaleDocumentVersionError(job_id, base_version, 0)

            if latest_ver.integrity_status == "QUARANTINED":
                raise CanonicalDocumentIntegrityError(
                    f"Cannot publish document version for job {job_id}: active document is QUARANTINED."
                )

            if latest_ver.version != base_version:
                raise StaleDocumentVersionError(job_id, base_version, latest_ver.version)

            intent = PublishIntentRecord(
                intent_id=intent_id,
                job_id=job_id,
                base_version=base_version,
                target_version=target_version,
                output_filename=output_filename,
                output_sha256=output_sha256,
                staged_artifacts_manifest=manifest_json,
                status="PENDING",
            )
            uow.publish_intents.insert_intent(intent)
            uow.commit()

        # Phase 2: Staging & Disk Flush
        job_dir = (self.artifacts_dir / f"job_{job_id}").resolve()
        job_dir.mkdir(parents=True, exist_ok=True)
        tmp_file = job_dir / f".{output_filename}.{intent_id}.tmp"

        activated = False
        try:
            with open(tmp_file, "wb") as f:
                f.write(data_bytes)
                f.flush()
                os.fsync(f.fileno())

            # Promote staged crops to permanent job artifact directory
            for item in manifest_items:
                src_path = Path(item["staging_path"])
                dest_path = job_dir / item["dest_filename"]
                shutil.copy2(src_path, dest_path)
                with open(dest_path, "rb") as df:
                    os.fsync(df.fileno())

            with self.uow_factory.create() as uow:
                uow.begin_immediate()
                uow.publish_intents.update_status(intent_id, "FLUSHED")
                uow.commit()

            # Phase 3: Atomic Disk Activation
            final_file = job_dir / output_filename
            os.replace(str(tmp_file), str(final_file))
            activated = True

            # Phase 4: Atomic SQLite Pointer Update
            canonical_uri = final_file.resolve().as_uri()
            with self.uow_factory.create() as uow:
                uow.begin_immediate()
                job = uow.jobs.get_by_id(job_id)
                uow.jobs.update_progress(
                    job_id=job_id,
                    processed_pages=job.processed_pages if job else 0,
                    switch_log=job.api_switch_log if job else [],
                    output_path=canonical_uri,
                )
                record = DocumentVersionRecord(
                    job_id=job_id,
                    version=target_version,
                    output_path=canonical_uri,
                    sha256=output_sha256,
                    integrity_status="VALID",
                    published_by=published_by,
                )
                inserted_record = uow.document_versions.insert_document_version(record)
                uow.publish_intents.delete_intent(intent_id)
                uow.commit()

            # Discard staging directories for promoted crops
            staging_ids = {item["staging_id"] for item in manifest_items}
            for sid in staging_ids:
                try:
                    self.staging_service.discard_staging(sid)
                except Exception as exc:
                    logger.warning("Failed to clean staging directory %s after publish: %s", sid, exc)

            return inserted_record

        except Exception:
            if not activated:
                if tmp_file.exists():
                    try:
                        tmp_file.unlink()
                    except OSError:
                        pass
                self._revert_promoted_crops(job_dir, manifest_items)
                try:
                    with self.uow_factory.create() as uow:
                        uow.begin_immediate()
                        uow.publish_intents.delete_intent(intent_id)
                        uow.commit()
                except Exception as cleanup_exc:
                    logger.warning("Failed to clean in-process failed intent %s: %s", intent_id, cleanup_exc)
            raise

    def reconcile_startup_intents(self) -> List[RecoveryResult]:
        """
        Executes startup crash recovery across all publication intents.
        Evaluates the 6-case recovery matrix, rolls forward completed activations,
        rolls back uncommitted partial work, and cleans orphaned staging directories.
        """
        results: List[RecoveryResult] = []
        active_staging_ids = set()

        with self.uow_factory.create() as uow:
            uow.begin_immediate()
            intents = uow.publish_intents.list_all()

            for intent in intents:
                manifest = []
                try:
                    manifest = json.loads(intent.staged_artifacts_manifest or "[]")
                except Exception:
                    manifest = []

                for item in manifest:
                    if "staging_id" in item:
                        active_staging_ids.add(item["staging_id"])

            for intent in intents:
                job_id = intent.job_id
                job_dir = (self.artifacts_dir / f"job_{job_id}").resolve()
                final_file = job_dir / intent.output_filename
                tmp_file = job_dir / f".{intent.output_filename}.{intent.intent_id}.tmp"

                manifest = []
                try:
                    manifest = json.loads(intent.staged_artifacts_manifest or "[]")
                except Exception:
                    manifest = []

                latest_record = uow.document_versions.get_latest(job_id)
                current_db_version = latest_record.version if latest_record else 0

                final_exists = final_file.is_file()
                tmp_exists = tmp_file.is_file()
                final_sha = compute_file_sha256(final_file) if final_exists else None

                # Case 5: Final file exists + DB already at target version
                if final_exists and current_db_version == intent.target_version:
                    uow.publish_intents.delete_intent(intent.intent_id)
                    results.append(RecoveryResult(
                        job_id=job_id,
                        intent_id=intent.intent_id,
                        action="CLEANED_LINGERING_INTENT",
                        target_version=intent.target_version,
                    ))
                    self._cleanup_manifest_staging(manifest)
                    continue

                # Case 3: Final file exists + SHA matches + DB at base version -> Forward Roll
                if final_exists and final_sha == intent.output_sha256 and current_db_version == intent.base_version:
                    canonical_uri = final_file.resolve().as_uri()
                    job = uow.jobs.get_by_id(job_id)
                    if job:
                        uow.jobs.update_progress(
                            job_id=job_id,
                            processed_pages=job.processed_pages,
                            switch_log=job.api_switch_log,
                            output_path=canonical_uri,
                        )
                    doc_ver = DocumentVersionRecord(
                        job_id=job_id,
                        version=intent.target_version,
                        output_path=canonical_uri,
                        sha256=intent.output_sha256,
                        integrity_status="VALID",
                        published_by="CRASH_RECOVERY_FORWARD_ROLL",
                    )
                    uow.document_versions.insert_document_version(doc_ver)
                    uow.publish_intents.delete_intent(intent.intent_id)
                    results.append(RecoveryResult(
                        job_id=job_id,
                        intent_id=intent.intent_id,
                        action="FORWARD_ROLLED",
                        target_version=intent.target_version,
                    ))
                    self._cleanup_manifest_staging(manifest)
                    continue

                # Case 4: Final file exists + SHA mismatch -> Quarantine corrupt final on disk
                if final_exists and final_sha != intent.output_sha256:
                    quarantine_file = job_dir / f"{intent.output_filename}.quarantine"
                    try:
                        os.replace(str(final_file), str(quarantine_file))
                    except OSError as exc:
                        logger.warning("Failed to quarantine corrupt final file %s to %s: %s", final_file, quarantine_file, exc)
                    self._revert_promoted_crops(job_dir, manifest)
                    if tmp_exists:
                        try:
                            tmp_file.unlink()
                        except OSError:
                            pass
                    uow.publish_intents.delete_intent(intent.intent_id)
                    job = uow.jobs.get_by_id(job_id)
                    if job:
                        diag = (
                            f"[INTEGRITY_QUARANTINE] Crash recovery: target file '{intent.output_filename}' "
                            f"checksum mismatch (expected {intent.output_sha256}, got {final_sha})."
                        )
                        current_err = job.error_message
                        new_err = f"{current_err} | {diag}" if current_err else diag
                        uow.jobs.update_status(job.id, job.status, error_message=new_err)

                    results.append(RecoveryResult(
                        job_id=job_id,
                        intent_id=intent.intent_id,
                        action="QUARANTINED_CORRUPT_FINAL",
                        target_version=intent.target_version,
                    ))
                    self._cleanup_manifest_staging(manifest)
                    continue

                # Case 2: PENDING/FLUSHED intent, tmp file exists, final file absent -> Roll back tmp
                if tmp_exists and not final_exists:
                    try:
                        tmp_file.unlink()
                    except OSError:
                        pass
                    self._revert_promoted_crops(job_dir, manifest)
                    uow.publish_intents.delete_intent(intent.intent_id)
                    results.append(RecoveryResult(
                        job_id=job_id,
                        intent_id=intent.intent_id,
                        action="ROLLED_BACK_TMP",
                        target_version=intent.target_version,
                    ))
                    self._cleanup_manifest_staging(manifest)
                    continue

                # Case 1: PENDING intent, no tmp file, final absent -> Delete pending intent
                if not tmp_exists and not final_exists:
                    self._revert_promoted_crops(job_dir, manifest)
                    uow.publish_intents.delete_intent(intent.intent_id)
                    results.append(RecoveryResult(
                        job_id=job_id,
                        intent_id=intent.intent_id,
                        action="DELETED_PENDING_INTENT",
                        target_version=intent.target_version,
                    ))
                    self._cleanup_manifest_staging(manifest)
                    continue

                # Fallthrough: unexpected intent state
                logger.warning(
                    "Recovery: intent %s for job %s did not match any standard recovery scenario "
                    "(final_exists=%s, tmp_exists=%s, db_ver=%s, base_ver=%s, target_ver=%s).",
                    intent.intent_id,
                    job_id,
                    final_exists,
                    tmp_exists,
                    current_db_version,
                    intent.base_version,
                    intent.target_version,
                )

            uow.commit()

        # Orphan staging directory scan (> 24 hours old with no active intent)
        self._scan_orphan_staging(active_staging_ids)

        return results

    def backfill_legacy_document_versions(self) -> BackfillSummary:
        """
        Convenience method to execute legacy document version backfill using this service's UnitOfWorkFactory.
        """
        return backfill_legacy_document_versions(self.uow_factory)

    def _revert_promoted_crops(self, job_dir: Path, manifest: List[dict]) -> None:
        """
        Reverts promoted crops. Enforces Artifact Recovery Invariant:
        Only unlinks destination if its SHA-256 matches the manifest. If destination exists
        with a DIFFERENT checksum, it is a pre-existing artifact and must NOT be deleted.
        """
        for item in manifest:
            dest_name = item.get("dest_filename")
            expected_sha = item.get("sha256")
            if not dest_name or not expected_sha:
                continue
            dest_path = job_dir / dest_name
            if dest_path.is_file():
                if compute_file_sha256(dest_path) == expected_sha:
                    try:
                        dest_path.unlink()
                    except OSError:
                        pass
                else:
                    logger.warning(
                        "Recovery invariant: destination %s has checksum mismatch with manifest; "
                        "preserving pre-existing artifact.",
                        dest_path,
                    )

    def _cleanup_manifest_staging(self, manifest: List[dict]) -> None:
        """Safely cleans up staging directories associated with an intent manifest."""
        staging_ids = {item["staging_id"] for item in manifest if "staging_id" in item}
        for sid in staging_ids:
            try:
                self.staging_service.discard_staging(sid)
            except Exception as exc:
                logger.warning("Failed to clean staging directory %s during recovery: %s", sid, exc)

    def _scan_orphan_staging(self, active_staging_ids: set) -> int:
        """
        Removes staging subdirectories older than 24 hours that are not referenced by any active intent.
        """
        staging_base = self.artifacts_dir / ".staging"
        if not staging_base.is_dir():
            return 0
        now = time.time()
        cleaned = 0
        for child in staging_base.iterdir():
            if child.is_dir():
                staging_id = child.name
                if staging_id in active_staging_ids:
                    continue
                try:
                    mtime = child.stat().st_mtime
                    if (now - mtime) > 86400:
                        shutil.rmtree(child, ignore_errors=True)
                        cleaned += 1
                except Exception as exc:
                    logger.warning("Failed to clean orphan staging directory %s: %s", child, exc)
        return cleaned
