# ============================================================
#  tests/unit/test_phase6_decommissioning.py
#  Phase 6 — Legacy Decommissioning & Architecture Tests
# ============================================================

import ast
import os
import unittest
from pathlib import Path

import tests.characterization.conftest_base
from infrastructure.composition import get_app_container, AppContainer
from core.policies.fallback_policy import FallbackChainPolicy
from core.entities.artifact import ArtifactType
from application.ports.document_processor import IDocumentProcessor
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor


class TestPhase6DecommissioningAndCleanArchitecture(unittest.TestCase):
    """
    Verifies that all legacy modules have been decommissioned and that
    the active application strictly obeys the canonical architectural boundaries.
    """

    def setUp(self):
        self.root_dir = Path(__file__).resolve().parent.parent.parent

    def test_legacy_modules_are_physically_deleted(self):
        """
        Verify that deprecated legacy files no longer exist on disk.
        """
        deprecated_files = [
            self.root_dir / "services" / "backup.py",
            self.root_dir / "services" / "pdf_processor.py",
            self.root_dir / "services" / "pipeline2_processor.py",
            self.root_dir / "services" / "ai_executor.py",
            self.root_dir / "services" / "api_manager.py",
            self.root_dir / "database" / "models.py",
        ]

        for file_path in deprecated_files:
            self.assertFalse(
                file_path.exists(),
                f"Deprecated legacy file '{file_path}' must be deleted.",
            )

    def test_zero_references_to_decommissioned_modules_in_codebase(self):
        """
        Verify that no active python module imports any decommissioned module name.
        """
        forbidden_imports = {
            "services.pdf_processor",
            "services.pipeline2_processor",
            "services.ai_executor",
            "services.api_manager",
            "services.backup",
            "database.models",
        }

        violations = []
        for root, _, files in os.walk(self.root_dir):
            if ".git" in root or "__pycache__" in root or ".system_generated" in root:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                file_path = os.path.join(root, f)
                with open(file_path, "r", encoding="utf-8") as fh:
                    try:
                        tree = ast.parse(fh.read(), filename=file_path)
                    except Exception as e:
                        violations.append(f"Syntax error in {file_path}: {e}")
                        continue

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            for fb in forbidden_imports:
                                if alias.name == fb or alias.name.startswith(fb + "."):
                                    violations.append(f"{file_path}: import {alias.name}")
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            for fb in forbidden_imports:
                                if node.module == fb or node.module.startswith(fb + "."):
                                    violations.append(f"{file_path}: from {node.module} import ...")

        self.assertEqual(
            violations,
            [],
            f"Found forbidden imports of decommissioned modules: {violations}",
        )

    def test_core_and_application_have_zero_legacy_service_imports(self):
        """
        Verify core/ and application/ have zero imports from services/.
        """
        target_dirs = [self.root_dir / "core", self.root_dir / "application"]
        violations = []

        for target_dir in target_dirs:
            for root, _, files in os.walk(target_dir):
                for f in files:
                    if not f.endswith(".py"):
                        continue
                    file_path = os.path.join(root, f)
                    with open(file_path, "r", encoding="utf-8") as fh:
                        tree = ast.parse(fh.read(), filename=file_path)

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                if alias.name.startswith("services."):
                                    violations.append(f"{file_path}: import {alias.name}")
                        elif isinstance(node, ast.ImportFrom):
                            if node.module and node.module.startswith("services"):
                                violations.append(f"{file_path}: from {node.module} import ...")

        self.assertEqual(
            violations,
            [],
            f"Core/Application layers must not import from services/: {violations}",
        )

    def test_quick_convert_handler_has_no_call_vision_api_shim(self):
        """
        Verify that handlers/quick_convert.py no longer contains the transitional _call_vision_api shim.
        """
        from handlers import quick_convert
        self.assertFalse(
            hasattr(quick_convert, "_call_vision_api"),
            "Transitional shim '_call_vision_api' must be removed from handlers/quick_convert.py",
        )

    def test_worker_imports_only_canonical_execution_service(self):
        """
        Verify services/worker.py imports only AppContainer/JobExecutionService and no legacy processors.
        """
        worker_path = self.root_dir / "services" / "worker.py"
        with open(worker_path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=str(worker_path))

        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.add(node.module)

        forbidden = {"services.pdf_processor", "services.pipeline2_processor", "services.ai_executor", "database.models"}
        intersect = imported_modules.intersection(forbidden)
        self.assertEqual(intersect, set(), f"services/worker.py must not import legacy modules: {intersect}")

    def test_document_processor_port_and_implementation_have_unify_markdown(self):
        """
        Verify unify_markdown is part of IDocumentProcessor port and PyMuPDFDocumentProcessor.
        """
        self.assertTrue(hasattr(IDocumentProcessor, "unify_markdown"))
        processor = PyMuPDFDocumentProcessor()
        self.assertTrue(callable(processor.unify_markdown))

        sample_raw = "## صفحه 1\nLine 1\n---\n## صفحه 2\nLine 2"
        unified = processor.unify_markdown(sample_raw)
        self.assertNotIn("## صفحه", unified)
        self.assertNotIn("---", unified)
        self.assertIn("Line 1\n\nLine 2", unified)

    def test_app_container_composition_root_has_all_services(self):
        """
        Verify AppContainer provides all required application services in the canonical composition root.
        """
        container = AppContainer(token_secret="test_secret_for_tests")
        self.assertIsInstance(container, AppContainer)
        self.assertIsNotNone(container.user_service)
        self.assertIsNotNone(container.prompt_service)
        self.assertIsNotNone(container.api_service)
        self.assertIsNotNone(container.job_submission_service)
        self.assertIsNotNone(container.job_query_service)
        self.assertIsNotNone(container.job_execution_service)
        self.assertIsNotNone(container.job_recovery_service)
        self.assertIsNotNone(container.quick_convert_service)
        self.assertIsNotNone(container.artifact_service)
        self.assertIsNotNone(container.auth_service)

    def test_canonical_provider_detector(self):

        """
        Verify infrastructure/ai/provider_detector.py provides get_default_base_url, get_default_model, and detect_provider_and_models.
        """
        from infrastructure.ai.provider_detector import (
            get_default_base_url, get_default_model, detect_provider_and_models,
        )
        self.assertIsNone(get_default_base_url("google"))
        self.assertEqual(get_default_base_url("openai"), "https://api.openai.com/v1")
        self.assertEqual(get_default_base_url("openrouter"), "https://openrouter.ai/api/v1")
        self.assertEqual(get_default_model("google"), "gemini-3.5-flash")
        self.assertEqual(get_default_model("openai"), "gpt-4o")

        # Empty key returns None, []
        provider, models = detect_provider_and_models("")
        self.assertIsNone(provider)
        self.assertEqual(models, [])

    def test_artifact_retrieval_and_storage_canonical_boundary(self):
        """
        Verify ArtifactService and LocalStorageAdapter cooperate without legacy file_manager dependencies.
        """
        import tempfile
        from infrastructure.storage.local_storage import LocalStorageAdapter
        from application.services.artifact_service import ArtifactService

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorageAdapter(base_dir=Path(tmpdir))
            mock_uow_factory = unittest.mock.MagicMock()
            mock_uow = unittest.mock.MagicMock()
            mock_uow_factory.create.return_value.__enter__.return_value = mock_uow

            handle = storage.store(
                job_id=42,
                artifact_type=ArtifactType.OUTPUT_MARKDOWN,
                filename="output.md",
                data=b"# Sample Clean Markdown",
                mime_type="text/markdown",
            )
            mock_job = unittest.mock.MagicMock()
            mock_job.user_id = 100
            mock_job.output_path = handle.uri
            mock_uow.jobs.get_by_id.return_value = mock_job

            service = ArtifactService(storage=storage, uow_factory=mock_uow_factory)
            stream, filename, mime = service.get_job_artifact_stream(job_id=42, user_id=100)


            content = stream.read()
            stream.close()

            self.assertEqual(content, b"# Sample Clean Markdown")
            self.assertEqual(mime, "text/markdown")


if __name__ == "__main__":
    unittest.main()
