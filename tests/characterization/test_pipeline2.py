# ============================================================
#  tests/characterization/test_pipeline2.py
# ============================================================

import unittest
import tempfile
import os
from infrastructure.document.pymupdf_processor import PyMuPDFDocumentProcessor
from infrastructure.storage.local_storage import LocalStorageAdapter
from core.entities.artifact import ArtifactType


class TestPipeline2Characterization(unittest.TestCase):
    """
    Characterizes Pipeline 2 text unification, regex stripping, and artifact handling.
    """

    def setUp(self):
        self.doc_processor = PyMuPDFDocumentProcessor()

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
        unified = self.doc_processor.unify_markdown(raw_text)

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
        unified = self.doc_processor.unify_markdown(raw_text)

        self.assertNotIn("---", unified)
        self.assertIn("Page 1 Content", unified)
        self.assertIn("Page 2 Content", unified)

    def test_unify_markdown_normalizes_excessive_newlines(self):
        """
        Characterization: unify_markdown collapses 3 or more newlines into double newlines.
        """
        raw_text = "Paragraph 1\n\n\n\n\n\nParagraph 2"
        unified = self.doc_processor.unify_markdown(raw_text)

        self.assertEqual(unified, "Paragraph 1\n\nParagraph 2")

    def test_pipeline2_artifact_storage_and_unification(self):
        """
        Characterization: Pipeline 2 unifies raw markdown and stores it via IArtifactStorage.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorageAdapter(base_dir=tmpdir)
            raw_content = "## صفحه 1\nRaw content\n---\n## صفحه 2\nMore raw content"
            unified = self.doc_processor.unify_markdown(raw_content)

            handle = storage.store(
                job_id=777,
                artifact_type=ArtifactType.PIPELINE2_MARKDOWN,
                filename="p2_unified.md",
                data=unified.encode("utf-8"),
                mime_type="text/markdown",
            )

            retrieved = storage.retrieve(handle).decode("utf-8")
            self.assertNotIn("## صفحه", retrieved)
            self.assertNotIn("---", retrieved)
            self.assertIn("Raw content\n\nMore raw content", retrieved)


if __name__ == "__main__":
    unittest.main()
