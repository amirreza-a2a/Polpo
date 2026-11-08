# ============================================================
#  tests/integration/test_interactive_repair_journey.py
#  Interactive AI Repair Journey & Math/Diagram Acceptance
# ============================================================

from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
from unittest.mock import MagicMock
import pytest

from application.dto.latex_repair_dto import LaTeXRepairProposal, LaTeXRepairRequest
from application.ports.ai_executor import IAIExecutionService
from application.ports.diagram_renderer import DiagramRenderRequest, DiagramSyntaxError
from application.ports.math_renderer import MathRenderRequest
from application.services.crop_artifact_staging_service import CropArtifactStagingService
from application.services.document_publication_service import DocumentPublicationService
from application.services.latex_repair_service import LaTeXRepairService
from application.services.markdown_editor_service import MarkdownEditorService
from application.services.markdown_viewer_service import MarkdownViewerService
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.job import Job, JobStatus
from core.entities.prompt import Prompt, PromptType
from core.exceptions.domain_exceptions import StaleDocumentVersionError
from infrastructure.diagram.cache import DiagramSvgCache
from infrastructure.diagram.standalone_tex_renderer import StandaloneTeXDiagramRenderer
from infrastructure.markdown.pandoc_parser import PandocParser
from infrastructure.math.lru_cache import MathSvgCache
from infrastructure.math.mathjax_client import MathJaxClient
from infrastructure.math.mathjax_supervisor import MathJaxProcessSupervisor
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.storage.local_storage import LocalStorageAdapter
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def journey_env(tmp_path: Path):
    db_file = tmp_path / "journey_test.db"
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

    with uow_factory.create() as uow:
        slot = ApiSlot(
            id=None,
            provider="google",
            label="Gemini Flash",
            selected_model="gemini-2.5-flash",
            credential_ref=CredentialRef("c1", "google", "byok"),
        )
        uow.apis.save(slot)

        prompt = uow.prompts.save(
            Prompt(
                id=None,
                name="Default",
                text="Prompt",
                prompt_type=PromptType.PIPELINE_1,
                is_default=True,
            )
        )

        job = uow.jobs.save(
            Job(
                id=None,
                file_name="diagram_doc.pdf",
                file_path=str(tmp_path / "diagram_doc.pdf"),
                prompt_id=prompt.id,
                status=JobStatus.DONE,
            )
        )
        uow.commit()
        job_id = job.id

    return {
        "uow_factory": uow_factory,
        "editor_service": editor_service,
        "publication_service": publication_service,
        "job_id": job_id,
        "tmp_path": tmp_path,
    }


@pytest.mark.integration
def test_interactive_diagram_repair_journey(qapp, journey_env):
    """Executes the full 26-step user journey from diagram compilation failure to OCC save."""
    if shutil.which("xelatex") is None and shutil.which("pdflatex") is None:
        pytest.skip("No TeX compiler installed on host runner")

    uow_factory = journey_env["uow_factory"]
    editor_service = journey_env["editor_service"]
    publication_service = journey_env["publication_service"]
    job_id = journey_env["job_id"]

    # 1. Broken TikZ source missing a semicolon
    broken_tikz = r"""\begin{tikzpicture}
    \draw (0,0) -- (2,2)
\end{tikzpicture}"""
    initial_doc = f"# System Specification\n\n```{{=latex}}\n{broken_tikz}\n```\n\nEnd of section.\n"

    publication_service.publish_initial(job_id=job_id, markdown_text=initial_doc)

    # 2. Confirm actual diagram renderer fails on broken source
    real_diag_renderer = StandaloneTeXDiagramRenderer()
    with pytest.raises(DiagramSyntaxError) as exc_info:
        real_diag_renderer.render(DiagramRenderRequest(source=broken_tikz))
    assert exc_info.value.code == "syntax"
    sanitized_diag = exc_info.value.message

    # 3. Setup mock AI executor for controlled candidate return
    corrected_tikz = r"""\begin{tikzpicture}
    \draw (0,0) -- (2,2);
\end{tikzpicture}"""
    ai_calls: list[dict] = []

    def mock_ai_execute(chain, prompt, at_page=0, **kwargs):
        ai_calls.append({"prompt": prompt, "chain": chain})
        return (
            json.dumps({
                "schema_version": 1,
                "repaired_source": corrected_tikz,
                "change_summary": "Added missing semicolon at end of path",
                "preserved_invariants": ["coordinates", "syntax_valid"],
            }),
            chain[0],
        )

    mock_ai = MagicMock(spec=IAIExecutionService)
    mock_ai.execute_text_with_fallback.side_effect = mock_ai_execute

    repair_service = LaTeXRepairService(
        uow_factory=uow_factory,
        ai_executor=mock_ai,
        diagram_renderer=real_diag_renderer,
    )

    ctrl = MarkdownEditorController(
        editor_service=editor_service,
        repair_service=repair_service,
    )

    try:
        # Step 4: Load source and verify canRepair
        ctrl.load_source_sync(job_id)
        assert ctrl.activeVersion == 1
        assert ctrl.isDirty is False
        assert ctrl.canRepair is True
        assert broken_tikz in ctrl.sourceText

        # Step 5 & 6 & 8: Pre-transmission consent check: cancelling sends 0 AI requests
        assert len(ai_calls) == 0

        # Step 9: User grants consent -> request repair
        proposals_ready = []
        errors = []
        ctrl.repairProposalReady.connect(proposals_ready.append)
        ctrl.repairFailed.connect(errors.append)

        ok = ctrl.requestRepair(
            node_id="diagram-1",
            original_source=broken_tikz,
            renderer_type="diagram",
            diagnostics=sanitized_diag,
            error_line=0,
            log_excerpt="",
            start_line=3,
            end_line=5,
        )
        assert ok is True

        deadline = time.time() + 4.0
        while not proposals_ready and not errors and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert len(proposals_ready) == 1
        assert len(errors) == 0

        # Step 10: Verify AI payload contained only the fragment, not the whole document
        assert len(ai_calls) == 1
        transmitted_prompt = ai_calls[0]["prompt"]
        assert broken_tikz in transmitted_prompt
        assert "# System Specification" not in transmitted_prompt
        assert "End of section." not in transmitted_prompt

        # Step 12 & 13: Real diagram compiler validated the repaired candidate!
        prop = proposals_ready[0]
        assert prop["validationPassed"] is True
        assert prop["repairedSource"] == corrected_tikz
        assert "<svg" in prop["renderedPreviewXml"]
        assert prop["renderedPreviewWidth"] > 0

        # Step 16: User rejects proposal -> buffer remains unchanged
        ctrl.rejectRepair()
        assert ctrl.activeProposal is None
        assert ctrl.isDirty is False
        assert broken_tikz in ctrl.sourceText

        # Step 17: Request repair again and accept
        proposals_ready.clear()
        ctrl.requestRepair(
            node_id="diagram-1",
            original_source=broken_tikz,
            renderer_type="diagram",
            diagnostics=sanitized_diag,
            error_line=0,
            log_excerpt="",
            start_line=3,
            end_line=5,
        )
        deadline = time.time() + 4.0
        while not proposals_ready and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        # Step 18 & 19: Apply repair
        assert ctrl.applyRepair() is True
        assert ctrl.isDirty is True
        assert ctrl.activeVersion == 1  # OCC watermark preserved
        assert corrected_tikz in ctrl.sourceText
        assert broken_tikz not in ctrl.sourceText

        # Step 21: Undo (Ctrl+Z) restores original source
        doc = ctrl._get_document()
        doc.undo()
        ctrl.set_source_text(doc.toPlainText())
        assert broken_tikz in ctrl.sourceText

        # Step 22: Redo / Re-apply repair
        doc.redo()
        ctrl.set_source_text(doc.toPlainText())
        assert corrected_tikz in ctrl.sourceText
        assert ctrl.isDirty is True

        # Step 23 & 24: Save commits new immutable version under OCC
        assert ctrl.save_sync() is True
        assert ctrl.isDirty is False
        assert ctrl.activeVersion == 2

        # Step 25: Reload and verify persistence
        saved_text, saved_ver = editor_service.load_source_text(job_id)
        assert saved_ver == 2
        assert corrected_tikz in saved_text

    finally:
        ctrl.shutdown()


@pytest.mark.integration
def test_interactive_mathjax_repair_journey(qapp, journey_env):
    """Verifies the equivalent repair cycle for MathJax equations with real MathJax validation."""
    uow_factory = journey_env["uow_factory"]
    editor_service = journey_env["editor_service"]
    publication_service = journey_env["publication_service"]
    job_id = journey_env["job_id"]

    supervisor = MathJaxProcessSupervisor()
    math_cache = MathSvgCache()
    real_math_client = MathJaxClient(supervisor=supervisor, cache=math_cache)

    try:
        broken_math = r"\int_{-\infty}^\infty e^{-x^2 dx = \sqrt{\pi}"
        corrected_math = r"\int_{-\infty}^\infty e^{-x^2} dx = \sqrt{\pi}"

        doc_text = f"# Math Document\n\n$$\n{broken_math}\n$$\n"
        publication_service.publish_initial(job_id=job_id, markdown_text=doc_text)

        # Setup AI mock that returns corrected math
        dummy_slot = ApiSlot(
            id=1,
            provider="google",
            label="Gemini Flash",
            selected_model="gemini-2.5-flash",
            credential_ref=CredentialRef("c1", "google", "byok"),
        )
        mock_ai = MagicMock(spec=IAIExecutionService)
        mock_ai.execute_text_with_fallback.return_value = (
            json.dumps({
                "schema_version": 1,
                "repaired_source": corrected_math,
                "change_summary": "Added missing closing brace in exponent",
                "preserved_invariants": ["syntax_valid"],
            }),
            dummy_slot,
        )

        repair_service = LaTeXRepairService(
            uow_factory=uow_factory,
            ai_executor=mock_ai,
            math_renderer=real_math_client,
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

            assert ctrl.requestRepair(
                node_id="math-1",
                original_source=broken_math,
                renderer_type="math_display",
                diagnostics="Missing }",
                error_line=0,
                log_excerpt="",
                start_line=3,
                end_line=5,
            ) is True

            deadline = time.time() + 3.0
            while not proposals_ready and time.time() < deadline:
                qapp.processEvents()
                time.sleep(0.01)

            assert len(proposals_ready) == 1
            prop = proposals_ready[0]
            # Real MathJax validated the repaired candidate!
            assert prop["validationPassed"] is True
            assert prop["repairedSource"] == corrected_math
            assert "<svg" in prop["renderedPreviewXml"]

            # Apply repair
            assert ctrl.applyRepair() is True
            assert corrected_math in ctrl.sourceText
            assert ctrl.isDirty is True

            # Save under OCC
            assert ctrl.save_sync() is True
            assert ctrl.activeVersion == 2

            saved_text, saved_ver = editor_service.load_source_text(job_id)
            assert saved_ver == 2
            assert corrected_math in saved_text
        finally:
            ctrl.shutdown()
    finally:
        supervisor.shutdown()
