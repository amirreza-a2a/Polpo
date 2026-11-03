# ============================================================
#  tests/unit/test_quarto_prompt_contracts.py
#  Prompt Output Contract Unit Tests (ADR-005 & Quarto Compatibility)
# ============================================================

import os
from pathlib import Path
import re
import tempfile
import unittest

from application.services.prompt_seeding_service import PromptSeedingService, CURATED_PROMPTS
from core.entities.prompt import PromptType
from infrastructure.paths import get_runtime_resource_path
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory


class TestQuartoPromptContracts(unittest.TestCase):
    """
    Verifies that all active and curated AI prompt templates strictly comply
    with the Quarto authoring standard (ADR-005 D01 & D09):
    1. Diagram blocks use standard Quarto raw LaTeX blocks: ```{=latex}
    2. Prohibits obsolete proprietary format ```tikz
    3. Prohibits standalone document wrappers (\\documentclass, \\usepackage, \\begin{document}, \\end{document})
    4. Mandates preservation of Persian labels in diagrams (no forced English translation)
    5. Preserves exact mathematical formulas and topological connectivity
    """

    def test_curated_tikz_template_quarto_contract(self):
        tikz_path = get_runtime_resource_path("prompts/templates/fa/stem_transcription_tikz.txt")
        self.assertTrue(tikz_path.is_file())
        text = tikz_path.read_text(encoding="utf-8")

        # Must mandate canonical Quarto raw LaTeX block syntax
        self.assertIn("```{=latex}", text)
        self.assertIn("\\begin{tikzpicture}", text)
        self.assertIn("\\begin{circuitikz}", text)

        # Must prohibit proprietary ```tikz and standalone wrappers
        self.assertIn("استفاده از بلوک‌های اختصاصی مانند ```tikz اکیداً ممنوع است", text)
        self.assertIn("\\documentclass", text)
        self.assertIn("\\usepackage", text)
        self.assertIn("\\begin{document}", text)
        self.assertIn("\\end{document}", text)
        # Verify that document wrappers do NOT appear as valid code blocks
        self.assertNotIn("```tikz\n\\usepackage", text)
        self.assertNotIn("```tikz\n\\begin{document}", text)
        self.assertNotIn("```latex\n\\usepackage", text)
        self.assertNotIn("```latex\n\\begin{document}", text)

        # Must explicitly mandate Persian label preservation
        self.assertIn("برچسب‌ها و متون قابل مشاهده (Labels)", text)
        self.assertIn("باید همان متن و برچسب فارسی عیناً حفظ شود و هرگز به انگلیسی ترجمه نشود", text)

        # Must NOT contain obsolete TikZJax / Obsidian requirement
        self.assertNotIn("سازگاری با TikZJax", text)
        self.assertNotIn("تمام کدهای گرافیکی باید با TikZJax و Obsidian سازگار باشند", text)

    def test_curated_quick_convert_template_quarto_contract(self):
        qc_path = get_runtime_resource_path("prompts/templates/fa/quick_convert.txt")
        self.assertTrue(qc_path.is_file())
        text = qc_path.read_text(encoding="utf-8")

        # Must instruct canonical Quarto raw block
        self.assertIn("```{=latex}", text)
        self.assertIn("\\begin{circuitikz}", text)
        self.assertIn("\\begin{tikzpicture}", text)

        # Must prohibit proprietary fences and wrappers
        self.assertNotIn("```tikz\n\\usepackage", text)
        self.assertNotIn("```tikz\n\\begin{document}", text)
        self.assertIn("استفاده از ```tikz، \\documentclass، \\usepackage، \\begin{document} و \\end{document} اکیداً ممنوع است", text)

        # Must preserve Persian labels
        self.assertIn("برچسب‌ها و متون قابل مشاهده در تصویر (Labels) در صورت فارسی بودن در تصویر اصلی، باید عیناً به زبان فارسی حفظ شوند و نباید ترجمه شوند", text)

    def test_pipeline2_quick_convert_quarto_contract(self):
        p2_qc_path = get_runtime_resource_path("prompts/pipeline2/quick_convert.txt")
        self.assertTrue(p2_qc_path.is_file())
        text = p2_qc_path.read_text(encoding="utf-8")

        # Must instruct canonical Quarto raw block
        self.assertIn("```{=latex}", text)
        self.assertNotIn("```latex\n\\usepackage", text)
        self.assertNotIn("```latex\n\\begin{document}", text)

        # Must preserve Persian labels
        self.assertIn("برچسب‌ها و متون توضیحی داخل شکل در صورت فارسی بودن در تصویر اصلی، باید عیناً به زبان فارسی حفظ شوند و ترجمه نشوند", text)

    def test_pipeline2_unify_markdown_quarto_contract(self):
        unify_path = get_runtime_resource_path("prompts/pipeline2/unify_markdown.txt")
        self.assertTrue(unify_path.is_file())
        text = unify_path.read_text(encoding="utf-8")

        # Must protect Quarto raw latex blocks
        self.assertIn("```{=latex}", text)
        self.assertIn("تمام بلوک‌های خام LaTeX و کدهای گرافیکی", text)

    def test_seeded_prompts_conform_to_quarto_contract(self):
        """Verify that all prompts seeded into SQLite database follow the Quarto contract."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_contract.db"
            db_manager = SQLiteDatabaseManager(str(db_path))
            migration_runner = SQLiteMigrationRunner(db_manager)
            migration_runner.run_migrations()
            uow_factory = SQLiteUnitOfWorkFactory(db_manager)
            seeder = PromptSeedingService(
                uow_factory=uow_factory,
                resource_resolver=get_runtime_resource_path,
            )
            seeded = seeder.seed_default_prompts()
            self.assertEqual(len(seeded), 9)

            with uow_factory.create() as uow:
                all_prompts = {p.name: p for p in uow.prompts.list_all()}

            tikz_p = all_prompts.get("Persian STEM with TikZ")
            self.assertIsNotNone(tikz_p)
            self.assertIn("```{=latex}", tikz_p.text)
            self.assertNotIn("## سازگاری با TikZJax", tikz_p.text)
            self.assertNotIn("```tikz\n\\usepackage", tikz_p.text)

            qc_p = all_prompts.get("Quick Convert")
            self.assertIsNotNone(qc_p)
            self.assertIn("```{=latex}", qc_p.text)
            self.assertNotIn("```tikz\n\\usepackage", qc_p.text)

            unify_p = all_prompts.get("Pipeline 2 Unify Markdown")
            self.assertIsNotNone(unify_p)
            self.assertIn("```{=latex}", unify_p.text)


if __name__ == "__main__":
    unittest.main()
