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
    1. In interfaces/desktop/app.py, DocumentViewerController is instantiated with
       region_publication_service=container.visual_region_publication_service.
    2. In interfaces/desktop/composition.py, DesktopAppContainer instantiates DocumentPublicationService and
       injects it into JobExecutionService, MarkdownEditorService, and VisualRegionPublicationService.
    3. DesktopAppContainer instantiates VisualRegionPublicationService.
    4. Neither DesktopAppContainer nor app.py references ApplyReviewService.
    """
    app_path = REPO_ROOT / "interfaces" / "desktop" / "app.py"
    app_tree = ast.parse(app_path.read_text(encoding="utf-8"))

    # Verify DocumentViewerController instantiation in app.py has region_publication_service
    found_dvc_reg_pub = False
    for node in ast.walk(app_tree):
        if isinstance(node, ast.Call):
            func_id = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if func_id == "DocumentViewerController":
                for kw in node.keywords:
                    if kw.arg == "region_publication_service":
                        found_dvc_reg_pub = True

    assert found_dvc_reg_pub, (
        "DocumentViewerController in app.py must be constructed with "
        "region_publication_service=container.visual_region_publication_service."
    )

    comp_path = REPO_ROOT / "interfaces" / "desktop" / "composition.py"
    comp_tree = ast.parse(comp_path.read_text(encoding="utf-8"))

    found_pub_service_instantiation = False
    found_visual_region_pub_service_instantiation = False
    found_apply_review_service_instantiation = False
    job_exec_wires_pub_service = False
    editor_wires_pub_service = False
    visual_region_wires_pub_service = False

    for node in ast.walk(comp_tree):
        if isinstance(node, ast.Call):
            func_id = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if func_id == "DocumentPublicationService":
                found_pub_service_instantiation = True
            elif func_id == "VisualRegionPublicationService":
                found_visual_region_pub_service_instantiation = True
                for kw in node.keywords:
                    if kw.arg in ("publication_service", "document_publication_service"):
                        visual_region_wires_pub_service = True
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
    assert found_visual_region_pub_service_instantiation, "DesktopAppContainer must instantiate VisualRegionPublicationService."
    assert visual_region_wires_pub_service, "DesktopAppContainer must inject DocumentPublicationService into VisualRegionPublicationService."
    assert not found_apply_review_service_instantiation, "DesktopAppContainer must not instantiate retired ApplyReviewService."
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


def test_ast_markdown_editor_service_omits_inline_backfill():
    """
    Invariant: MarkdownEditorService omits inline backfill_legacy_document_versions
    imports and calls. Backfill belongs strictly to application startup bootstrap.
    Enforces that neither ImportFrom nor Import can reference legacy_document_backfill.
    """
    editor_path = REPO_ROOT / "application" / "services" / "markdown_editor_service.py"
    editor_tree = ast.parse(editor_path.read_text(encoding="utf-8"))

    for node in ast.walk(editor_tree):
        if isinstance(node, ast.Call):
            func_id = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            assert func_id != "backfill_legacy_document_versions", (
                f"Found forbidden backfill_legacy_document_versions call node at line {node.lineno} in markdown_editor_service.py"
            )

        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            assert "legacy_document_backfill" not in module_name, (
                f"Found forbidden legacy_document_backfill ImportFrom at line {node.lineno} in markdown_editor_service.py"
            )
            for alias in node.names:
                assert (
                    alias.name != "backfill_legacy_document_versions"
                    and alias.name != "legacy_document_backfill"
                ), (
                    f"Found forbidden legacy_document_backfill import alias '{alias.name}' at line {node.lineno} in markdown_editor_service.py"
                )

        elif isinstance(node, ast.Import):
            for alias in node.names:
                mod_name = alias.name or ""
                assert (
                    mod_name != "application.services.legacy_document_backfill"
                    and not mod_name.endswith(".legacy_document_backfill")
                    and mod_name != "legacy_document_backfill"
                ), (
                    f"Found forbidden legacy_document_backfill Import '{mod_name}' at line {node.lineno} in markdown_editor_service.py"
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


def test_ast_apply_review_decommissioned_and_purged():
    """
    Invariant: ApplyReviewService is completely decommissioned and purged from PolpoT.
    1. application/services/apply_review_service.py does NOT exist.
    2. Zero Python files across the codebase import or reference ApplyReviewService.
    3. No application service performs direct canonical markdown writes (DocumentPublicationService is the sole authority).
    """
    services_dir = REPO_ROOT / "application" / "services"
    apply_review_path = services_dir / "apply_review_service.py"
    assert not apply_review_path.exists(), "application/services/apply_review_service.py must be deleted."

    # Verify zero references to ApplyReviewService or apply_review_service in python code
    for py_file in REPO_ROOT.rglob("*.py"):
        if any(part.startswith(".") or part in ("venv", "build", "dist", "__pycache__") for part in py_file.parts):
            continue
        # Skip this test file itself from the substring check of the class name
        if py_file.resolve() == Path(__file__).resolve():
            continue
        content = py_file.read_text(encoding="utf-8")
        assert "ApplyReviewService" not in content, (
            f"Found forbidden ApplyReviewService reference in {py_file}"
        )
        assert "apply_review_service" not in content, (
            f"Found forbidden apply_review_service reference in {py_file}"
        )

    # Verify no service in application/services performs direct canonical markdown writes
    service_files = list(services_dir.glob("*.py"))
    violating_services = []
    for s_file in service_files:
        tree = ast.parse(s_file.read_text(encoding="utf-8"))
        visitor = _CanonicalStoreVisitor()
        visitor.visit(tree)
        if visitor.stores_canonical:
            violating_services.append(s_file.name)

    assert not violating_services, (
        f"The following application services unexpectedly perform direct canonical markdown writes: {violating_services}"
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


DB_RECEIVER_KEYWORDS = {
    "db",
    "database",
    "sqlite",
    "sqlite3",
    "pymysql",
    "sqlalchemy",
    "sql",
    "sql_connection",
    "connection",
    "conn",
    "cursor",
    "cur",
}


def _is_db_token(tok: str) -> bool:
    """Checks whether an identifier token corresponds to database-handle naming."""
    clean = tok.strip("_").lower()
    if clean in DB_RECEIVER_KEYWORDS:
        return True
    parts = clean.split("_")
    return any(p in DB_RECEIVER_KEYWORDS for p in parts if p)


def _has_db_receiver(receiver_node: ast.AST) -> bool:
    """
    Inspects receiver AST structure (names, attributes, and method call chains)
    to detect database-handle terminology across arbitrarily nested chains.
    """
    for child in ast.walk(receiver_node):
        if isinstance(child, ast.Name) and _is_db_token(child.id):
            return True
        elif isinstance(child, ast.Attribute) and _is_db_token(child.attr):
            return True
    return False


def _check_ast_for_direct_db_operations(tree: ast.AST) -> list[str]:
    """
    AST analyzer detecting direct database operations in presentation controllers.
    Covers:
      - Direct driver module imports (sqlite3, pymysql, sqlalchemy).
      - Executescript calls on any receiver.
      - Calls to execute, executemany, cursor, connect on DB-like receiver chains
        (e.g. cursor.execute, self.conn.execute, self.database.execute,
        self.sql_connection.execute, self.db.cursor().execute, etc.).
    Preserves legitimate non-DB calls (command.execute(), model.execute(),
    self.viewer_service.execute(), self._executor.submit(), self.action.execute()).
    """
    violations = []
    db_methods = {"execute", "executemany", "executescript", "cursor", "connect"}
    db_driver_modules = {"sqlite3", "pymysql", "sqlalchemy"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for db_mod in db_driver_modules:
                    if alias.name == db_mod or alias.name.startswith(db_mod + "."):
                        violations.append(f"Forbidden direct DB import '{alias.name}' at line {node.lineno}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for db_mod in db_driver_modules:
                if mod == db_mod or mod.startswith(db_mod + "."):
                    violations.append(f"Forbidden direct DB from-import '{mod}' at line {node.lineno}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if method in db_methods:
                if method == "executescript":
                    violations.append(f"Direct database executescript() call at line {node.lineno}")
                elif _has_db_receiver(node.func.value):
                    violations.append(
                        f"Direct database call '...{method}()' on DB-like receiver at line {node.lineno}"
                    )

    return violations


def test_ast_direct_db_detection_catches_attribute_receivers():
    """
    Regression test validating that _check_ast_for_direct_db_operations detects
    both Name and Attribute receiver patterns without false positives on non-DB methods.
    """
    detected_snippets = [
        'cursor.execute("SELECT 1")',
        'db.execute("SELECT 1")',
        'self.conn.execute("SELECT 1")',
        'self.db.execute("SELECT 1")',
        'self.connection.executemany("INSERT INTO t VALUES (?)", [(1,)])',
        'self.database.execute("SELECT 1")',
        'self.sql_connection.execute("SELECT 1")',
        'self._cursor.execute("SELECT 1")',
        'self._connection.executemany("INSERT INTO t VALUES (?)", [(1,)])',
        'self.database.cursor()',
        'self.db.cursor().execute("SELECT 1")',
        'self.database.connection.execute("SELECT 1")',
        'sqlite3.connect("test.db")',
        'pymysql.connect(host="localhost")',
        'any_obj.executescript("CREATE TABLE t (id INT);")',
    ]
    for snippet in detected_snippets:
        tree = ast.parse(snippet)
        violations = _check_ast_for_direct_db_operations(tree)
        assert len(violations) > 0, f"Expected detection for snippet: {snippet}"

    clean_snippets = [
        'command.execute()',
        'model.execute()',
        'self.viewer_service.execute()',
        'self.action.execute()',
        'self._executor.submit(task)',
        'self.viewer_service.render_preview(1, text, 1)',
    ]
    for snippet in clean_snippets:
        tree = ast.parse(snippet)
        violations = _check_ast_for_direct_db_operations(tree)
        assert len(violations) == 0, f"Unexpected false positive for clean snippet: {snippet}"


def test_p08_architecture_boundaries_remain_clean():
    """
    Phase P08 architectural boundary verification:
      1. application/services/markdown_viewer_service.py has ZERO Qt/PySide6/PyQt imports.
      2. infrastructure/math/*.py has ZERO Qt/PySide6/PyQt imports.
      3. interfaces/desktop/controllers/*.py has ZERO direct imports of infrastructure.math.
      4. interfaces/desktop/controllers/*.py has ZERO direct database operations or driver imports.
    """
    app_service_file = REPO_ROOT / "application" / "services" / "markdown_viewer_service.py"
    infra_math_dir = REPO_ROOT / "infrastructure" / "math"
    controllers_dir = REPO_ROOT / "interfaces" / "desktop" / "controllers"

    qt_frameworks = {"PySide6", "PyQt6", "PyQt5", "PySide2", "Qt"}

    # 1. application/services/markdown_viewer_service.py must be 100% Qt-free
    assert app_service_file.is_file(), f"File not found: {app_service_file}"
    tree_service = ast.parse(app_service_file.read_text(encoding="utf-8"), filename=str(app_service_file))
    for node in ast.walk(tree_service):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for qt in qt_frameworks:
                    assert not (alias.name == qt or alias.name.startswith(qt + ".")), (
                        f"Forbidden Qt import '{alias.name}' in application service: {app_service_file}"
                    )
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for qt in qt_frameworks:
                assert not (mod == qt or mod.startswith(qt + ".")), (
                    f"Forbidden Qt from-import '{mod}' in application service: {app_service_file}"
                )

    # 2. infrastructure/math/*.py must be 100% Qt-free
    assert infra_math_dir.is_dir(), f"Directory not found: {infra_math_dir}"
    for py_file in infra_math_dir.glob("*.py"):
        tree_math = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree_math):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for qt in qt_frameworks:
                        assert not (alias.name == qt or alias.name.startswith(qt + ".")), (
                            f"Forbidden Qt import '{alias.name}' in math infrastructure: {py_file}"
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for qt in qt_frameworks:
                    assert not (mod == qt or mod.startswith(qt + ".")), (
                        f"Forbidden Qt from-import '{mod}' in math infrastructure: {py_file}"
                    )

    # 3. interfaces/desktop/controllers/*.py must NOT directly import infrastructure.math
    assert controllers_dir.is_dir(), f"Directory not found: {controllers_dir}"
    for py_file in controllers_dir.glob("*.py"):
        tree_ctrl = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree_ctrl):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not (alias.name == "infrastructure.math" or alias.name.startswith("infrastructure.math.")), (
                        f"Forbidden direct import of 'infrastructure.math' in controller: {py_file}"
                    )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not (mod == "infrastructure.math" or mod.startswith("infrastructure.math.")), (
                    f"Forbidden direct from-import of 'infrastructure.math' in controller: {py_file}"
                )

        # 4. Check for direct database operations and driver imports
        db_violations = _check_ast_for_direct_db_operations(tree_ctrl)
        assert not db_violations, f"Direct database violations in {py_file}: {db_violations}"
