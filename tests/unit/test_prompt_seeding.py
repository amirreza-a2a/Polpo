# ============================================================
#  tests/unit/test_prompt_seeding.py
# ============================================================

import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

from core.entities.prompt import Prompt, PromptType
from application.services.prompt_seeding_service import (
    PromptSeedingService,
    CuratedPromptDefinition,
    CURATED_PROMPTS,
)
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.paths import get_runtime_resource_path
from interfaces.desktop.composition import DesktopAppContainer


class TestPromptSeeding(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_prompts.db"
        self.db_manager = SQLiteDatabaseManager(str(self.db_path))
        self.migration_runner = SQLiteMigrationRunner(self.db_manager)
        self.migration_runner.run_migrations()
        self.uow_factory = SQLiteUnitOfWorkFactory(self.db_manager)
        self.seeder = PromptSeedingService(
            uow_factory=self.uow_factory,
            resource_resolver=get_runtime_resource_path,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_curated_prompts_manifest_count_and_paths(self):
        """Verifies the curated manifest contains exactly the 9 curated source files."""
        self.assertEqual(len(CURATED_PROMPTS), 9)
        # All files under prompts/templates/ (8 files) + prompts/pipeline2/unify_markdown.txt (1 file)
        templates_prompts = [p for p in CURATED_PROMPTS if p.relative_path.startswith("prompts/templates/")]
        pipeline2_prompts = [p for p in CURATED_PROMPTS if p.relative_path.startswith("prompts/pipeline2/")]
        self.assertEqual(len(templates_prompts), 8)
        self.assertEqual(len(pipeline2_prompts), 1)
        self.assertEqual(pipeline2_prompts[0].relative_path, "prompts/pipeline2/unify_markdown.txt")

        # Verify every file in the manifest actually exists on disk
        for p in CURATED_PROMPTS:
            resolved = get_runtime_resource_path(p.relative_path)
            self.assertTrue(resolved.is_file(), f"Curated prompt file missing: {resolved}")
            content = resolved.read_text(encoding="utf-8")
            self.assertTrue(len(content.strip()) > 0, f"Curated prompt file is empty: {resolved}")

    def test_clean_first_run_seeds_all_curated_prompts(self):
        """Clean first-run initialization seeds all 9 prompts and updates settings defaults."""
        with self.uow_factory.create() as uow:
            self.assertEqual(len(uow.prompts.list_all()), 0)

        seeded = self.seeder.seed_default_prompts()
        self.assertEqual(len(seeded), 9)

        with self.uow_factory.create() as uow:
            all_prompts = uow.prompts.list_all()
            self.assertEqual(len(all_prompts), 9)

            p1_prompts = uow.prompts.list_all(PromptType.PIPELINE_1)
            p2_prompts = uow.prompts.list_all(PromptType.PIPELINE_2)
            qc_prompts = uow.prompts.list_all(PromptType.QUICK_CONVERT)

            self.assertEqual(len(p1_prompts), 7)
            self.assertEqual(len(p2_prompts), 1)
            self.assertEqual(len(qc_prompts), 1)

            # Check defaults
            p1_default = uow.prompts.get_default(PromptType.PIPELINE_1)
            self.assertIsNotNone(p1_default)
            self.assertEqual(p1_default.name, "Persian STEM Transcription")

            p2_default = uow.prompts.get_default(PromptType.PIPELINE_2)
            self.assertIsNotNone(p2_default)
            self.assertEqual(p2_default.name, "Pipeline 2 Unify Markdown")

            qc_default = uow.prompts.get_default(PromptType.QUICK_CONVERT)
            self.assertIsNotNone(qc_default)
            self.assertEqual(qc_default.name, "Quick Convert")

            # Check AppSettings linked defaults
            settings = uow.settings.get()
            self.assertEqual(settings.default_prompt_id, p1_default.id)
            self.assertEqual(settings.default_pipeline2_prompt_id, p2_default.id)

    def test_exact_source_text_preserved(self):
        """Verifies source text is preserved byte-for-byte including Markdown and newlines."""
        self.seeder.seed_default_prompts()

        with self.uow_factory.create() as uow:
            all_prompts = {p.name: p for p in uow.prompts.list_all()}
            for defn in CURATED_PROMPTS:
                self.assertIn(defn.name, all_prompts)
                expected_text = get_runtime_resource_path(defn.relative_path).read_text(encoding="utf-8")
                self.assertEqual(all_prompts[defn.name].text, expected_text)

    def test_idempotent_repeated_startup_preserves_records(self):
        """Calling seed_default_prompts multiple times does not duplicate records or modify IDs."""
        first_seeded = self.seeder.seed_default_prompts()
        self.assertEqual(len(first_seeded), 9)

        second_seeded = self.seeder.seed_default_prompts()
        self.assertEqual(second_seeded, [])

        with self.uow_factory.create() as uow:
            all_prompts = uow.prompts.list_all()
            self.assertEqual(len(all_prompts), 9)
            self.assertEqual([p.id for p in all_prompts], [p.id for p in first_seeded])

    def test_preserves_user_modified_and_deleted_prompts(self):
        """If user edits a default prompt or deletes one, seeding does not overwrite or re-add."""
        self.seeder.seed_default_prompts()

        with self.uow_factory.create() as uow:
            p1_default = uow.prompts.get_default(PromptType.PIPELINE_1)
            p1_default.text = "User customized transcription instructions"
            uow.prompts.save(p1_default)

            p2_default = uow.prompts.get_default(PromptType.PIPELINE_2)
            uow.prompts.delete(p2_default.id)
            uow.commit()

        # Re-run seeding
        result = self.seeder.seed_default_prompts()
        self.assertEqual(result, [])

        with self.uow_factory.create() as uow:
            all_prompts = uow.prompts.list_all()
            # Still 8 prompts (the deleted one was not resurrected)
            self.assertEqual(len(all_prompts), 8)
            # The edited prompt keeps its user customization
            p1 = uow.prompts.get_by_id(p1_default.id)
            self.assertEqual(p1.text, "User customized transcription instructions")

    def test_preserves_existing_custom_prompt_in_non_empty_table(self):
        """If the prompts table already contains user prompts, seeding is skipped completely."""
        with self.uow_factory.create() as uow:
            custom = Prompt(
                id=None,
                name="Custom Sole Prompt",
                text="Custom prompt content",
                prompt_type=PromptType.PIPELINE_1,
                is_default=True,
            )
            uow.prompts.save(custom)
            uow.commit()

        result = self.seeder.seed_default_prompts()
        self.assertEqual(result, [])

        with self.uow_factory.create() as uow:
            all_prompts = uow.prompts.list_all()
            self.assertEqual(len(all_prompts), 1)
            self.assertEqual(all_prompts[0].name, "Custom Sole Prompt")

    def test_missing_source_file_aborts_and_rolls_back(self):
        """If a required prompt asset is missing, seeding raises FileNotFoundError and leaves 0 rows."""
        bogus_manifest = list(CURATED_PROMPTS) + [
            CuratedPromptDefinition(
                relative_path="prompts/templates/fa/non_existent.txt",
                name="Missing Prompt",
                prompt_type=PromptType.PIPELINE_1,
                is_default=False,
            )
        ]
        bad_seeder = PromptSeedingService(
            uow_factory=self.uow_factory,
            resource_resolver=get_runtime_resource_path,
            curated_prompts=bogus_manifest,
        )

        with self.assertRaises(FileNotFoundError):
            bad_seeder.seed_default_prompts()

        with self.uow_factory.create() as uow:
            self.assertEqual(len(uow.prompts.list_all()), 0)

    def test_empty_source_file_aborts_and_rolls_back(self):
        """If a prompt file is empty, seeding raises ValueError and leaves 0 rows."""
        empty_file = Path(self.tmp_dir.name) / "empty_prompt.txt"
        empty_file.write_text("   \n  ", encoding="utf-8")

        bogus_manifest = [
            CuratedPromptDefinition(
                relative_path=str(empty_file),
                name="Empty Prompt",
                prompt_type=PromptType.PIPELINE_1,
                is_default=True,
            )
        ]
        bad_seeder = PromptSeedingService(
            uow_factory=self.uow_factory,
            resource_resolver=lambda p: Path(p),
            curated_prompts=bogus_manifest,
        )

        with self.assertRaises(ValueError):
            bad_seeder.seed_default_prompts()

        with self.uow_factory.create() as uow:
            self.assertEqual(len(uow.prompts.list_all()), 0)

    def test_retry_after_failure_succeeds(self):
        """A failed seed attempt is safely retryable and leaves no partial state."""
        missing_rel = "prompts/templates/fa/temp_missing.txt"
        temp_file = Path(self.tmp_dir.name) / "temp_missing.txt"

        custom_manifest = [
            CuratedPromptDefinition(
                relative_path=missing_rel,
                name="Recoverable Prompt",
                prompt_type=PromptType.PIPELINE_1,
                is_default=True,
            )
        ]

        def resolver(p: str | Path) -> Path:
            if str(p) == missing_rel:
                return temp_file
            return get_runtime_resource_path(p)

        retry_seeder = PromptSeedingService(
            uow_factory=self.uow_factory,
            resource_resolver=resolver,
            curated_prompts=custom_manifest,
        )

        # 1. First attempt fails
        with self.assertRaises(FileNotFoundError):
            retry_seeder.seed_default_prompts()

        with self.uow_factory.create() as uow:
            self.assertEqual(len(uow.prompts.list_all()), 0)

        # 2. Fix the file
        temp_file.write_text("Valid prompt content", encoding="utf-8")

        # 3. Second attempt succeeds
        seeded = retry_seeder.seed_default_prompts()
        self.assertEqual(len(seeded), 1)

        with self.uow_factory.create() as uow:
            all_prompts = uow.prompts.list_all()
            self.assertEqual(len(all_prompts), 1)
            self.assertEqual(all_prompts[0].text, "Valid prompt content")

    def test_injected_save_failure_rolls_back_atomically(self):
        """If an error occurs while inserting prompts, the entire batch is rolled back."""
        original_create = self.uow_factory.create

        class FailingUoW:
            def __init__(self, real_uow):
                self._real = real_uow
                self.count = 0

            def __enter__(self):
                self._real.__enter__()
                self.settings = self._real.settings
                self.prompts = self
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return self._real.__exit__(exc_type, exc_val, exc_tb)

            def begin_immediate(self):
                return self._real.begin_immediate()

            def commit(self):
                return self._real.commit()

            def rollback(self):
                return self._real.rollback()

            def list_all(self, prompt_type=None):
                return self._real.prompts.list_all(prompt_type)

            def save(self, prompt):
                self.count += 1
                if self.count == 3:
                    raise sqlite3.OperationalError("Simulated disk error on 3rd insert")
                return self._real.prompts.save(prompt)

        mock_factory = MagicMock()
        mock_factory.create.side_effect = lambda: FailingUoW(original_create())

        failing_seeder = PromptSeedingService(
            uow_factory=mock_factory,
            resource_resolver=get_runtime_resource_path,
        )

        with self.assertRaises(sqlite3.OperationalError):
            failing_seeder.seed_default_prompts()

        with self.uow_factory.create() as uow:
            self.assertEqual(len(uow.prompts.list_all()), 0)

    def test_concurrent_seeding_safety(self):
        """Simultaneous seeding threads cannot produce duplicate prompt records."""
        barrier = threading.Barrier(5)
        results = []
        errors = []

        def worker():
            try:
                barrier.wait()
                seeder = PromptSeedingService(
                    uow_factory=self.uow_factory,
                    resource_resolver=get_runtime_resource_path,
                )
                res = seeder.seed_default_prompts()
                results.append(res)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Thread errors: {errors}")
        with self.uow_factory.create() as uow:
            all_prompts = uow.prompts.list_all()
            self.assertEqual(len(all_prompts), 9)

        # Exactly one thread should have performed the seeding (returned 9 items)
        seeded_threads = [r for r in results if len(r) == 9]
        skipped_threads = [r for r in results if len(r) == 0]
        self.assertEqual(len(seeded_threads), 1)
        self.assertEqual(len(skipped_threads), 4)

    def test_desktop_app_container_initialize_seeds_prompts(self):
        """DesktopAppContainer.initialize() automatically seeds default prompts."""
        fresh_db = Path(self.tmp_dir.name) / "container_fresh.db"
        container = DesktopAppContainer(
            db_path=fresh_db,
            artifacts_dir=Path(self.tmp_dir.name) / "artifacts",
            vault_path=Path(self.tmp_dir.name) / "vault.enc",
            scheduler_tick_interval=100.0,
        )
        try:
            container.initialize()
            with container.uow_factory.create() as uow:
                prompts = uow.prompts.list_all()
                self.assertEqual(len(prompts), 9)
        finally:
            container.shutdown()


if __name__ == "__main__":
    unittest.main()
