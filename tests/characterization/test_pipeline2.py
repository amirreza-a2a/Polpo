# ============================================================
#  tests/characterization/test_pipeline2.py
# ============================================================

import unittest
import tempfile
import os
from services.pipeline2_processor import (
    unify_markdown, prepare_unified_input,
    get_pipeline2_input_path, get_pipeline2_output_path,
)


class TestPipeline2Characterization(unittest.TestCase):
    """
    Characterizes Pipeline 2 text unification, regex stripping, and path resolution.
    """

    def test_unify_markdown_strips_page_headers(self):
        """
        Characterization: unify_markdown removes '## صفحه X' headers.
        """
        raw_text = (
            "## صفحه 1\n"
            "This is paragraph one.\n\n"
            "## صفحه 2\n"
            "This is paragraph two."
        )
        unified = unify_markdown(raw_text)

        self.assertNotIn("## صفحه 1", unified)
        self.assertNotIn("## صفحه 2", unified)
        self.assertIn("This is paragraph one.", unified)
        self.assertIn("This is paragraph two.", unified)

    def test_unify_markdown_strips_horizontal_rules(self):
        """
        Characterization: unify_markdown removes '---' separators.
        """
        raw_text = (
            "Page 1 Content\n"
            "---\n"
            "Page 2 Content"
        )
        unified = unify_markdown(raw_text)

        self.assertNotIn("---", unified)
        self.assertIn("Page 1 Content", unified)
        self.assertIn("Page 2 Content", unified)

    def test_unify_markdown_normalizes_excessive_newlines(self):
        """
        Characterization: unify_markdown collapses 3 or more newlines into double newlines.
        """
        raw_text = "Paragraph 1\n\n\n\n\n\nParagraph 2"
        unified = unify_markdown(raw_text)

        self.assertEqual(unified, "Paragraph 1\n\nParagraph 2")

    def test_pipeline2_path_resolution(self):
        """
        Characterization: Pipeline 2 derives standardized input and output paths.
        """
        p2_job_id = 99
        source_name = "test_document.pdf"

        input_path = get_pipeline2_input_path(p2_job_id)
        output_path = get_pipeline2_output_path(p2_job_id, source_name)

        self.assertTrue(input_path.endswith("unified_input.md"))
        self.assertTrue(output_path.endswith("unified_test_document.md"))
        self.assertIn("pipeline2", input_path)
        self.assertIn("pipeline2", output_path)

    def test_prepare_unified_input_file_creation(self):
        """
        Characterization: prepare_unified_input reads source markdown, cleans it, and writes unified file.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("## صفحه 1\nRaw content\n---\n## صفحه 2\nMore raw content")
            src_path = f.name

        try:
            p2_job_id = 777
            out_in_path = prepare_unified_input(p2_job_id, src_path)

            self.assertTrue(os.path.exists(out_in_path))
            with open(out_in_path, "r", encoding="utf-8") as f_out:
                content = f_out.read()

            self.assertNotIn("## صفحه", content)
            self.assertNotIn("---", content)
            self.assertIn("Raw content\n\nMore raw content", content)

            # Cleanup created p2 directory
            if os.path.exists(out_in_path):
                os.remove(out_in_path)
                parent = os.path.dirname(out_in_path)
                if os.path.exists(parent):
                    os.rmdir(parent)
        finally:
            if os.path.exists(src_path):
                os.remove(src_path)


if __name__ == "__main__":
    unittest.main()
