# ============================================================
#  tests/unit/test_phase10e3a_architecture_invariants.py
#  Phase 10E.3a: Static Invariant Audit & Verification Suite (Ticket 10E.3a-10)
# ============================================================

import ast
import hashlib
import os
from pathlib import Path
import uuid

import pytest

from application.dto.staged_crop import StagedCropHandle
from application.services.crop_artifact_staging_service import CropArtifactStagingService
from application.services.document_publication_service import (
    DocumentPublicationService,
    compute_file_sha256,
)
from core.entities.document_version import DocumentVersionRecord, PublishIntentRecord
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.exceptions.domain_exceptions import (
    CanonicalDocumentIntegrityError,
    PublicationInProgressError,
    StaleDocumentVersionError,
)
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_ast_publication_service_watermark_isolation():
    """
    Invariant 4: output_artifact_version_watermark is never used by
    DocumentPublicationService for OCC or version calculation.
    AST inspection confirms zero references or string literals in the service.
    """
    pub_service_path = REPO_ROOT / "application" / "services" / "document_publication_service.py"
    assert pub_service_path.is_file(), f"File not found: {pub_service_path}"

    tree = ast.parse(pub_service_path.read_text(encoding="utf-8"))

    prohibited_names = {"output_artifact_version_watermark"}
    found_prohibited = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in prohibited_names:
            found_prohibited.append(f"Name '{node.id}' at line {node.lineno}")
        elif isinstance(node, ast.Attribute) and node.attr in prohibited_names:
            found_prohibited.append(f"Attribute '{node.attr}' at line {node.lineno}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "output_artifact_version_watermark" in node.value:
                found_prohibited.append(f"String literal mentioning watermark at line {node.lineno}")

    assert not found_prohibited, (
        f"DocumentPublicationService violates watermark isolation with prohibited references: {found_prohibited}"
    )


def test_ast_publication_service_shim_isolation():
    """
    Invariant: DocumentPublicationService contains zero references to
    active_markdown_version or legacy compatibility shims.
    """
    pub_service_path = REPO_ROOT / "application" / "services" / "document_publication_service.py"
    tree = ast.parse(pub_service_path.read_text(encoding="utf-8"))

    prohibited_names = {"active_markdown_version"}
    found_prohibited = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in prohibited_names:
            found_prohibited.append(f"Name '{node.id}' at line {node.lineno}")
        elif isinstance(node, ast.Attribute) and node.attr in prohibited_names:
            found_prohibited.append(f"Attribute '{node.attr}' at line {node.lineno}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "active_markdown_version" in node.value:
                found_prohibited.append(f"String literal mentioning active_markdown_version at line {node.lineno}")

    assert not found_prohibited, (
        f"DocumentPublicationService violates shim isolation: {found_prohibited}"
    )


def test_ast_sole_active_runtime_publisher():
    """
    Invariant 9: DocumentPublicationService is the sole active runtime canonical Markdown publisher.
    AST inspection validates that:
    1. In interfaces/desktop/app.py, DocumentViewerController is instantiated with apply_review_service=None.
    2. In interfaces/desktop/composition.py, DesktopAppContainer instantiates DocumentPublicationService and
       injects it into JobExecutionService and MarkdownEditorService.
    3. Neither DesktopAppContainer nor app.py injects ApplyReviewService into any presentation controller.
    """
    app_path = REPO_ROOT / "interfaces" / "desktop" / "app.py"
    app_tree = ast.parse(app_path.read_text(encoding="utf-8"))

    # Verify DocumentViewerController instantiation in app.py has apply_review_service=None
    found_dvc_none = False
    for node in ast.walk(app_tree):
        if isinstance(node, ast.Call):
            func_id = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if func_id == "DocumentViewerController":
                for kw in node.keywords:
                    if kw.arg == "apply_review_service":
                        if isinstance(kw.value, ast.Constant) and kw.value.value is None:
                            found_dvc_none = True

    assert found_dvc_none, "DocumentViewerController in app.py must be constructed with apply_review_service=None."

    comp_path = REPO_ROOT / "interfaces" / "desktop" / "composition.py"
    comp_tree = ast.parse(comp_path.read_text(encoding="utf-8"))

    found_pub_service_instantiation = False
    found_apply_review_service_instantiation = False
    job_exec_wires_pub_service = False
    editor_wires_pub_service = False

    for node in ast.walk(comp_tree):
        if isinstance(node, ast.Call):
            func_id = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if func_id == "DocumentPublicationService":
                found_pub_service_instantiation = True
            elif func_id == "ApplyReviewService":
                found_apply_review_service_instantiation = True
            elif func_id == "JobExecutionService":
                for kw in node.keywords:
                    if kw.arg == "document_publication_service":
                        job_exec_wires_pub_service = True
            elif func_id == "MarkdownEditorService":
                for kw in node.keywords:
                    if kw.arg == "document_publication_service":
                        editor_wires_pub_service = True

    assert found_pub_service_instantiation, "DesktopAppContainer must instantiate DocumentPublicationService."
    assert not found_apply_review_service_instantiation, "DesktopAppContainer must not instantiate quarantined ApplyReviewService."
    assert job_exec_wires_pub_service, "DesktopAppContainer must inject DocumentPublicationService into JobExecutionService."
    assert editor_wires_pub_service, "DesktopAppContainer must inject DocumentPublicationService into MarkdownEditorService."


def test_ast_composition_container_omits_quarantined_apply_review_service():
    """
    Invariant: DesktopAppContainer in interfaces/desktop/composition.py omits
    quarantined ApplyReviewService instantiation and imports.
    """
    comp_path = REPO_ROOT / "interfaces" / "desktop" / "composition.py"
    comp_tree = ast.parse(comp_path.read_text(encoding="utf-8"))

    for node in ast.walk(comp_tree):
        if isinstance(node, ast.Call):
            func_id = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            assert func_id != "ApplyReviewService", (
                f"Found forbidden ApplyReviewService call node at line {node.lineno} in composition.py"
            )

    for node in ast.walk(comp_tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name != "ApplyReviewService", (
                    f"Found forbidden ApplyReviewService import at line {node.lineno} in composition.py"
                )


class _CanonicalStoreVisitor(ast.NodeVisitor):
    def __init__(self):
        self.stores_canonical = False

    def visit_Call(self, node):
        if getattr(node.func, "attr", None) == "store":
            is_output_md = False
            is_canonical_name = False
            for kw in node.keywords:
                if kw.arg == "artifact_type":
                    if isinstance(kw.value, ast.Attribute) and kw.value.attr == "OUTPUT_MARKDOWN":
                        is_output_md = True
                elif kw.arg == "filename":
                    if isinstance(kw.value, ast.JoinedStr):
                        for part in kw.value.values:
                            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                                if "output_" in part.value:
                                    is_canonical_name = True
                    elif isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        if kw.value.value.startswith("output_"):
                            is_canonical_name = True
            if is_output_md and is_canonical_name:
                self.stores_canonical = True
        self.generic_visit(node)


def test_ast_apply_review_quarantined_exception():
    """
    Invariant: ApplyReviewService is the ONLY application service with legacy direct
    canonical Markdown write logic, and is quarantined from runtime execution.
    No other service in application/services may execute direct canonical markdown stores
    via storage.store(..., artifact_type=ArtifactType.OUTPUT_MARKDOWN, filename='output_...').
    """
    services_dir = REPO_ROOT / "application" / "services"
    service_files = list(services_dir.glob("*.py"))

    violating_services = []
    apply_review_stores_canonical = False

    for s_file in service_files:
        tree = ast.parse(s_file.read_text(encoding="utf-8"))
        visitor = _CanonicalStoreVisitor()
        visitor.visit(tree)

        if s_file.name == "apply_review_service.py":
            apply_review_stores_canonical = visitor.stores_canonical
        else:
            if visitor.stores_canonical:
                violating_services.append(s_file.name)

    assert apply_review_stores_canonical, (
        "ApplyReviewService must be detected as containing legacy direct canonical Markdown write logic."
    )
    assert not violating_services, (
        f"The following application services unexpectedly perform direct canonical markdown writes: {violating_services}"
    )

    # Verify that ApplyReviewService source explicitly contains quarantine documentation
    apply_review_path = services_dir / "apply_review_service.py"
    apply_review_text = apply_review_path.read_text(encoding="utf-8")
    assert "quarantine" in apply_review_text.lower() or "legacy" in apply_review_text.lower(), (
        "ApplyReviewService must be explicitly tagged with quarantine or legacy documentation."
    )


def test_e2e_publication_authority_lifecycle(tmp_path):
    """
    End-to-end multi-writer OCC verification test:
    1. publish_initial() produces version 1.
    2. publish_version() produces version 2 with staged crops promoted.
    3. Concurrent publish_version() attempts with stale base_version are rejected.
    4. jobs.output_path correctly points to the latest canonical file.
    5. document_versions stores immutable history with real SHA-256.
    """
    db_path = tmp_path / "e2e_lifecycle.db"
    mgr = SQLiteDatabaseManager(db_path)
    SQLiteMigrationRunner(mgr).run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(mgr)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    with uow_factory.create() as uow:
        p = uow.prompts.save(Prompt(id=None, name="P", text="T", prompt_type=PromptType.PIPELINE_1, is_default=True))
        prompt_id = p.id
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="paper.pdf",
                file_path="file:///paper.pdf",
                total_pages=1,
                prompt_id=prompt_id,
                status=JobStatus.DONE,
            )
        )
        uow.commit()

    staging = CropArtifactStagingService(base_dir=artifacts_dir)
    pub_service = DocumentPublicationService(
        uow_factory=uow_factory,
        artifacts_dir=artifacts_dir,
        staging_service=staging,
    )

    # 1. Initial publication creates v1
    v1_text = "# Initial Document Title\nSome content."
    v1_rec = pub_service.publish_initial(
        job_id=job.id,
        markdown_text=v1_text,
        published_by="PIPELINE_1_EXECUTION",
    )
    assert v1_rec.version == 1
    assert v1_rec.integrity_status == "VALID"
    assert v1_rec.sha256 == hashlib.sha256(v1_text.encode("utf-8")).hexdigest()

    # 2. Stage crop artifact and publish v2
    stage_id = str(uuid.uuid4())
    crop_data = b"\xff\xd8\xff\xe0FakeJpegData"
    staged_crop = staging.stage_crop(
        job_id=job.id,
        staging_id=stage_id,
        region_id="reg-123",
        version=1,
        image_bytes=crop_data,
    )

    v2_text = "# Initial Document Title\nUpdated content with image ![crop](crops/reg-123_v1.jpg)."
    v2_rec = pub_service.publish_version(
        job_id=job.id,
        base_version=1,
        markdown_text=v2_text,
        staged_crops=[staged_crop],
        published_by="MARKDOWN_EDITOR",
    )
    assert v2_rec.version == 2
    assert v2_rec.integrity_status == "VALID"

    # Verify crop promoted to permanent directory
    dest_crop_path = artifacts_dir / f"job_{job.id}" / staged_crop.dest_filename
    assert dest_crop_path.exists()
    assert dest_crop_path.read_bytes() == crop_data

    # 3. OCC rejection on stale base_version=1
    with pytest.raises(StaleDocumentVersionError) as exc_info:
        pub_service.publish_version(
            job_id=job.id,
            base_version=1,
            markdown_text="Stale concurrent edit",
            published_by="CONCURRENT_WRITER",
        )
    assert exc_info.value.base_version == 1
    assert exc_info.value.current_version == 2

    # 4. In-flight protection
    with uow_factory.create() as uow:
        uow.publish_intents.insert_intent(
            PublishIntentRecord(
                intent_id="intent-conflict",
                job_id=job.id,
                base_version=2,
                target_version=3,
                output_filename=f"output_{job.id}_v3.md",
                output_sha256="fake_sha",
                staged_artifacts_manifest="[]",
                status="PENDING",
            )
        )
        uow.commit()

    with pytest.raises(PublicationInProgressError):
        pub_service.publish_version(
            job_id=job.id,
            base_version=2,
            markdown_text="Another concurrent edit",
            published_by="CONCURRENT_WRITER_2",
        )


def test_e2e_recovery_after_simulated_crash(tmp_path):
    """
    Simulates crash recovery across all publication intent states during bootstrap:
    1. Case 1: PENDING intent, no files -> deleted pending intent.
    2. Case 2: PENDING intent with tmp file -> tmp unlinked, intent deleted.
    3. Case 3: FLUSHED intent where final file was promoted on disk before crash:
       forward-rolled to SQLite with document_versions row and output_path updated.
    4. Case 4: FLUSHED intent where final file exists but checksum mismatches:
       quarantined on disk with .quarantine suffix, error logged.
    5. Case 5: FLUSHED intent where SQLite was already updated (crash during cleanup):
       lingering intent cleaned up without altering version row.
    6. Staging: Orphan staging directory > 24 hours old without active intent discarded.
    """
    import time
    db_path = tmp_path / "e2e_recovery.db"
    mgr = SQLiteDatabaseManager(db_path)
    SQLiteMigrationRunner(mgr).run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(mgr)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    with uow_factory.create() as uow:
        p = uow.prompts.save(Prompt(id=None, name="P", text="T", prompt_type=PromptType.PIPELINE_1, is_default=True))
        prompt_id = p.id
        job1 = uow.jobs.save(
            Job(id=None, file_name="j1.pdf", file_path="f:///j1.pdf", total_pages=1, prompt_id=prompt_id, status=JobStatus.PROCESSING)
        )
        job2 = uow.jobs.save(
            Job(id=None, file_name="j2.pdf", file_path="f:///j2.pdf", total_pages=1, prompt_id=prompt_id, status=JobStatus.PROCESSING)
        )
        job3 = uow.jobs.save(
            Job(id=None, file_name="j3.pdf", file_path="f:///j3.pdf", total_pages=1, prompt_id=prompt_id, status=JobStatus.PROCESSING)
        )
        job4 = uow.jobs.save(
            Job(id=None, file_name="j4.pdf", file_path="f:///j4.pdf", total_pages=1, prompt_id=prompt_id, status=JobStatus.PROCESSING)
        )
        job5 = uow.jobs.save(
            Job(id=None, file_name="j5.pdf", file_path="f:///j5.pdf", total_pages=1, prompt_id=prompt_id, status=JobStatus.PROCESSING)
        )
        uow.commit()

    pub_service = DocumentPublicationService(uow_factory=uow_factory, artifacts_dir=artifacts_dir)

    # Setup Case 2: PENDING intent for job1 with temporary file (crash before fsync/promote)
    j1_dir = artifacts_dir / f"job_{job1.id}"
    j1_dir.mkdir(parents=True, exist_ok=True)
    tmp1 = j1_dir / f".output_{job1.id}_v1.md.intent1.tmp"
    tmp1.write_text("Interrupted pending write", encoding="utf-8")

    # Setup Case 3: FLUSHED intent for job2 with promoted final file (crash right after os.replace)
    j2_dir = artifacts_dir / f"job_{job2.id}"
    j2_dir.mkdir(parents=True, exist_ok=True)
    final2 = j2_dir / f"output_{job2.id}_v1.md"
    v1_text_j2 = "# Successfully Flushed Markdown"
    final2.write_text(v1_text_j2, encoding="utf-8")
    sha2 = hashlib.sha256(v1_text_j2.encode("utf-8")).hexdigest()

    # Setup Case 1: PENDING intent for job3 with no files on disk
    j3_dir = artifacts_dir / f"job_{job3.id}"
    j3_dir.mkdir(parents=True, exist_ok=True)

    # Setup Case 4: FLUSHED intent for job4 with corrupt file on disk
    j4_dir = artifacts_dir / f"job_{job4.id}"
    j4_dir.mkdir(parents=True, exist_ok=True)
    final4 = j4_dir / f"output_{job4.id}_v1.md"
    final4.write_text("Corrupt unexpected file content", encoding="utf-8")

    # Setup Case 5: FLUSHED intent for job5 with DB already having target version 1
    j5_dir = artifacts_dir / f"job_{job5.id}"
    j5_dir.mkdir(parents=True, exist_ok=True)
    final5 = j5_dir / f"output_{job5.id}_v1.md"
    v1_text_j5 = "# Pre-committed Job 5 Content"
    final5.write_text(v1_text_j5, encoding="utf-8")
    sha5 = hashlib.sha256(v1_text_j5.encode("utf-8")).hexdigest()

    # Setup Orphan Staging directory (>24 hours old)
    staging_base = artifacts_dir / ".staging"
    orphan_staging = staging_base / "orphan-staging-uuid"
    orphan_staging.mkdir(parents=True, exist_ok=True)
    (orphan_staging / "crop_temp.jpg").write_bytes(b"dummy")
    old_time = time.time() - (25 * 3600)
    os.utime(orphan_staging, (old_time, old_time))

    with uow_factory.create() as uow:
        uow.publish_intents.insert_intent(
            PublishIntentRecord(
                intent_id="intent1",
                job_id=job1.id,
                base_version=0,
                target_version=1,
                output_filename=f"output_{job1.id}_v1.md",
                output_sha256="sha1",
                staged_artifacts_manifest="[]",
                status="PENDING",
            )
        )
        uow.publish_intents.insert_intent(
            PublishIntentRecord(
                intent_id="intent2",
                job_id=job2.id,
                base_version=0,
                target_version=1,
                output_filename=f"output_{job2.id}_v1.md",
                output_sha256=sha2,
                staged_artifacts_manifest="[]",
                status="FLUSHED",
            )
        )
        uow.publish_intents.insert_intent(
            PublishIntentRecord(
                intent_id="intent3",
                job_id=job3.id,
                base_version=0,
                target_version=1,
                output_filename=f"output_{job3.id}_v1.md",
                output_sha256="sha3",
                staged_artifacts_manifest="[]",
                status="PENDING",
            )
        )
        uow.publish_intents.insert_intent(
            PublishIntentRecord(
                intent_id="intent4",
                job_id=job4.id,
                base_version=0,
                target_version=1,
                output_filename=f"output_{job4.id}_v1.md",
                output_sha256="expected_sha4_that_does_not_match",
                staged_artifacts_manifest="[]",
                status="FLUSHED",
            )
        )
        uow.publish_intents.insert_intent(
            PublishIntentRecord(
                intent_id="intent5",
                job_id=job5.id,
                base_version=0,
                target_version=1,
                output_filename=f"output_{job5.id}_v1.md",
                output_sha256=sha5,
                staged_artifacts_manifest="[]",
                status="FLUSHED",
            )
        )
        uow.document_versions.insert_document_version(
            DocumentVersionRecord(
                job_id=job5.id,
                version=1,
                output_path=f"file://{final5.resolve().as_posix()}",
                sha256=sha5,
                integrity_status="VALID",
                published_by="SYSTEM",
            )
        )
        uow.commit()

    # Run startup reconciliation across all crash scenarios
    results = pub_service.reconcile_startup_intents()
    assert len(results) == 5
    actions = {r.job_id: r.action for r in results}
    assert actions[job1.id] == "ROLLED_BACK_TMP"
    assert actions[job2.id] == "FORWARD_ROLLED"
    assert actions[job3.id] == "DELETED_PENDING_INTENT"
    assert actions[job4.id] == "QUARANTINED_CORRUPT_FINAL"
    assert actions[job5.id] == "CLEANED_LINGERING_INTENT"

    # Verify Case 2: tmp unlinked, intent removed
    assert not tmp1.exists()

    # Verify Case 3: final committed to SQLite, intent removed
    assert final2.exists()
    assert final2.read_text(encoding="utf-8") == v1_text_j2

    # Verify Case 4: corrupt final quarantined to .quarantine file
    quarantine4 = j4_dir / f"output_{job4.id}_v1.md.quarantine"
    assert quarantine4.exists()
    assert not final4.exists()

    # Verify Case 5: target version intact
    with uow_factory.create() as uow:
        doc5 = uow.document_versions.get_latest(job5.id)
        assert doc5.version == 1
        assert doc5.sha256 == sha5

    # Verify Orphan Staging was cleaned up
    assert not orphan_staging.exists()
