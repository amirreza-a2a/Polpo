# ============================================================
#  tests/integration/test_latex_repair_e2e.py
#  End-to-End Integration Tests for AI-Assisted LaTeX Error Repair
# ============================================================

from datetime import datetime, timezone
from pathlib import Path
import time
from unittest.mock import MagicMock
import pytest

from application.dto.latex_repair_dto import LaTeXRepairProposal, LaTeXRepairRequest
from application.ports.ai_executor import IAIExecutionService
from application.ports.diagram_renderer import IDiagramRenderer
from application.ports.math_renderer import IMathRenderer, MathRenderResult
from application.services.crop_artifact_staging_service import CropArtifactStagingService
from application.services.document_publication_service import DocumentPublicationService
from application.services.latex_repair_service import LaTeXRepairService
from application.services.markdown_editor_service import MarkdownEditorService
from core.ai.types import AIResponse
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.exceptions.domain_exceptions import StaleDocumentVersionError
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def e2e_env(tmp_path: Path):
    db_file = tmp_path / "polpo_repair_test.db"
    db_manager = SQLiteDatabaseManager(str(db_file))
    runner = SQLiteMigrationRunner(db_manager)
    runner.run_migrations()
    uow_factory = SQLiteUnitOfWorkFactory(db_manager)

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = artifacts_dir / ".staging"
    staging_service = CropArtifactStagingService(base_dir=staging_dir)

    storage = LocalStorageAdapter(base_dir=str(artifacts_dir))
    publication_service = DocumentPublicationService(
        uow_factory=uow_factory,
        artifacts_dir=artifacts_dir,
        staging_service=staging_service,
    )
    editor_service = MarkdownEditorService(
        uow_factory=uow_factory,
        storage=storage,
        document_publication_service=publication_service,
    )

    # Seed API Slot
    with uow_factory.create() as uow:
        slot = ApiSlot(
            id=None,
            provider="google",
            label="Primary Gemini",
            selected_model="gemini-2.5-flash",
            credential_ref=CredentialRef("cred_1", "google", "byok"),
        )
        uow.apis.save(slot)

        # Seed Prompt
        prompt = uow.prompts.save(
            Prompt(
                id=None,
                name="Default",
                text="Extract text",
                prompt_type=PromptType.PIPELINE_1,
                is_default=True,
            )
        )

        # Seed Job
        job = uow.jobs.save(
            Job(
                id=None,
                file_name="test.pdf",
                file_path=str(tmp_path / "test.pdf"),
                prompt_id=prompt.id,
                status=JobStatus.DONE,
            )
        )
        uow.commit()
        job_id = job.id

    # Publish initial document with failing LaTeX syntax: missing closing brace
    initial_md = "# Equation Document\n\nCalculate $E = \\frac{m c^2{2}$ immediately.\n"
    publication_service.publish_initial(
        job_id=job_id,
        markdown_text=initial_md,
    )

    return {
        "uow_factory": uow_factory,
        "editor_service": editor_service,
        "publication_service": publication_service,
        "artifacts_dir": artifacts_dir,
        "job_id": job_id,
    }


def test_ai_latex_repair_end_to_end(qapp, e2e_env):
    uow_factory = e2e_env["uow_factory"]
    editor_service = e2e_env["editor_service"]
    job_id = e2e_env["job_id"]

    # 1. Setup mock AI executor that returns corrected JSON
    ai_response_json = """```json
{
  "schema_version": 1,
  "repaired_source": "\\\\frac{m c^2}{2}",
  "change_summary": "Fixed closing brace on numerator",
  "preserved_invariants": ["syntax_valid"]
}
```"""
    dummy_slot = ApiSlot(
        id=1,
        provider="google",
        label="Primary Gemini",
        selected_model="gemini-2.5-flash",
        credential_ref=CredentialRef("cred_1", "google", "byok"),
    )
    mock_ai_executor = MagicMock(spec=IAIExecutionService)
    mock_ai_executor.execute_text_with_fallback.return_value = (
        ai_response_json,
        dummy_slot,
    )

    # 2. Setup mock Math Renderer that verifies candidate compilation
    mock_math_renderer = MagicMock(spec=IMathRenderer)
    mock_math_renderer.render.return_value = MathRenderResult(
        hash="valid-hash",
        svg_xml="<svg width='120' height='30'></svg>",
        width="12ex",
        height="3ex",
        vertical_align="0ex",
    )

    mock_diag_renderer = MagicMock(spec=IDiagramRenderer)

    repair_service = LaTeXRepairService(
        uow_factory=uow_factory,
        ai_executor=mock_ai_executor,
        diagram_renderer=mock_diag_renderer,
        math_renderer=mock_math_renderer,
    )

    # 3. Create editor controller and load source from SQLite
    ctrl = MarkdownEditorController(
        editor_service=editor_service,
        repair_service=repair_service,
    )
    try:
        ctrl.load_source_sync(job_id)
        assert ctrl.activeVersion == 1
        assert ctrl.isDirty is False
        assert "\\frac{m c^2{2}" in ctrl.sourceText

        # 4. Request repair
        proposals_ready = []
        errors = []
        ctrl.repairProposalReady.connect(proposals_ready.append)
        ctrl.repairFailed.connect(errors.append)

        assert ctrl.requestRepair(
            node_id="math-node-1",
            original_source="\\frac{m c^2{2}",
            renderer_type="math_inline",
            diagnostics="Missing } inserted",
            error_line=1,
            log_excerpt="! Missing } inserted.",
        ) is True

        deadline = time.time() + 2.0
        while not proposals_ready and not errors and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert len(proposals_ready) == 1
        prop = proposals_ready[0]
        assert prop["repairedSource"] == "\\frac{m c^2}{2}"
        assert prop["validationPassed"] is True

        # 5. Apply repair to editor buffer
        assert ctrl.applyRepair() is True

        # 6. Verify buffer state: repaired source is present, dirty is True, version is still 1 (OCC watermark)
        assert "\\frac{m c^2}{2}" in ctrl.sourceText
        assert "\\frac{m c^2{2}" not in ctrl.sourceText
        assert ctrl.isDirty is True
        assert ctrl.activeVersion == 1

        # 7. Save source back to SQLite via OCC
        assert ctrl.save_sync() is True
        assert ctrl.isDirty is False
        assert ctrl.activeVersion == 2

        # 8. Verify SQLite persistence of repaired document
        saved_text, saved_ver = editor_service.load_source_text(job_id)
        assert saved_ver == 2
        assert "\\frac{m c^2}{2}" in saved_text

        with uow_factory.create() as uow:
            doc_rec = uow.document_versions.get_latest(job_id)
            assert doc_rec is not None
            assert doc_rec.version == 2
    finally:
        ctrl.shutdown()


def test_ai_latex_repair_occ_conflict_detection(qapp, e2e_env):
    uow_factory = e2e_env["uow_factory"]
    editor_service = e2e_env["editor_service"]
    publication_service = e2e_env["publication_service"]
    job_id = e2e_env["job_id"]

    dummy_slot = ApiSlot(
        id=1,
        provider="google",
        label="Primary Gemini",
        selected_model="gemini-2.5-flash",
        credential_ref=CredentialRef("cred_1", "google", "byok"),
    )
    mock_ai_executor = MagicMock(spec=IAIExecutionService)
    mock_ai_executor.execute_text_with_fallback.return_value = (
        '{"schema_version": 1, "repaired_source": "\\\\frac{m c^2}{2}", "change_summary": "Fixed", "preserved_invariants": []}',
        dummy_slot,
    )
    mock_math_renderer = MagicMock(spec=IMathRenderer)
    mock_math_renderer.render.return_value = MathRenderResult(
        hash="valid-hash",
        svg_xml="<svg></svg>",
        width="5ex",
        height="2ex",
        vertical_align="0ex",
    )

    repair_service = LaTeXRepairService(
        uow_factory=uow_factory,
        ai_executor=mock_ai_executor,
        diagram_renderer=MagicMock(),
        math_renderer=mock_math_renderer,
    )

    ctrl = MarkdownEditorController(
        editor_service=editor_service,
        repair_service=repair_service,
    )
    try:
        ctrl.load_source_sync(job_id)
        assert ctrl.activeVersion == 1

        proposals_ready = []
        ctrl.repairProposalReady.connect(proposals_ready.append)
        ctrl.requestRepair(
            node_id="math-node-1",
            original_source="\\frac{m c^2{2}",
            renderer_type="math_inline",
            diagnostics="Syntax error",
        )

        deadline = time.time() + 2.0
        while not proposals_ready and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        # Apply repair
        assert ctrl.applyRepair() is True
        assert ctrl.isDirty is True

        # Simulate concurrent external edit incrementing canonical version in SQLite to version 2
        publication_service.publish_version(
            job_id=job_id,
            base_version=1,
            markdown_text="# Concurrent External Update\nSome text.",
        )

        # Attempting to save dirty repaired buffer with base_version=1 should trigger OCC conflict!
        conflicts = []
        ctrl.conflictDetected.connect(conflicts.append)

        assert ctrl.save_sync() is False
        assert ctrl.hasConflict is True
        assert len(conflicts) == 1
        assert "stale" in conflicts[0].lower() or "conflict" in conflicts[0].lower() or "version" in conflicts[0].lower()

        # Direct domain-level call with stale base_version=1 raises StaleDocumentVersionError
        with pytest.raises(StaleDocumentVersionError):
            editor_service.commit_source_text(
                job_id=job_id,
                raw_text=ctrl.sourceText,
                base_version=1,
            )

        # The controller detects stale version and preserves user's dirty buffer
        assert ctrl.isDirty is True
    finally:
        ctrl.shutdown()
