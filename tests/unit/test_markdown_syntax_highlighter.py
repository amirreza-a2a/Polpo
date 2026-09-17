# ============================================================
#  tests/unit/test_markdown_syntax_highlighter.py
#  Unit tests for Desktop Markdown Syntax Highlighter
# ============================================================

from unittest.mock import MagicMock
import pytest

from interfaces.desktop.qt_compat import (
    QGuiApplication,
    QTextDocument,
    QTextCursor,
    QFont,
)
from interfaces.desktop.syntax.markdown_syntax_highlighter import (
    MarkdownSyntaxHighlighter,
    STATE_DEFAULT,
    STATE_CODE_BLOCK,
    STATE_COMMENT_BLOCK,
)
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


def test_highlighter_headings(qapp):
    doc = QTextDocument()
    text = "# Heading 1\n## Heading 2\n### Heading 3\n#### Heading 4"
    doc.setPlainText(text)
    hl = MarkdownSyntaxHighlighter(doc)

    # Check Block 0 (H1)
    b0 = doc.findBlockByNumber(0)
    layout0 = b0.layout()
    formats0 = layout0.formats()
    assert len(formats0) > 0
    fmt0 = formats0[0].format
    assert fmt0.fontWeight() == QFont.Weight.Bold
    assert fmt0.foreground().color().name() == "#60a5fa"

    # Check Block 1 (H2)
    b1 = doc.findBlockByNumber(1)
    formats1 = b1.layout().formats()
    assert len(formats1) > 0
    assert formats1[0].format.foreground().color().name() == "#93c5fd"


def test_highlighter_code_block_suppression(qapp):
    doc = QTextDocument()
    text = "```python\n# This is code, not heading\n**not bold** and ![[not_region]]\n```\n# Real Heading"
    doc.setPlainText(text)
    hl = MarkdownSyntaxHighlighter(doc)

    # Block 0: opening fence
    b0 = doc.findBlockByNumber(0)
    assert b0.userState() == STATE_CODE_BLOCK
    assert b0.layout().formats()[0].format.foreground().color().name() == "#c4b5fd"

    # Block 1: code line with '#' - must be styled as code, NOT heading!
    b1 = doc.findBlockByNumber(1)
    assert b1.userState() == STATE_CODE_BLOCK
    formats1 = b1.layout().formats()
    assert len(formats1) == 1
    assert formats1[0].format.foreground().color().name() == "#c4b5fd"

    # Block 2: code line with '**' and '![[' - must be code, NOT bold or region!
    b2 = doc.findBlockByNumber(2)
    assert b2.userState() == STATE_CODE_BLOCK
    formats2 = b2.layout().formats()
    assert len(formats2) == 1
    assert formats2[0].format.foreground().color().name() == "#c4b5fd"

    # Block 3: closing fence
    b3 = doc.findBlockByNumber(3)
    assert b3.userState() == STATE_DEFAULT
    assert b3.layout().formats()[0].format.foreground().color().name() == "#c4b5fd"

    # Block 4: Real Heading outside code block
    b4 = doc.findBlockByNumber(4)
    assert b4.userState() == STATE_DEFAULT
    assert b4.layout().formats()[0].format.foreground().color().name() == "#60a5fa"


def test_highlighter_html_comment_multiline(qapp):
    doc = QTextDocument()
    text = "<!-- Start of comment\nMiddle of comment\nEnd of comment -->\nRegular text"
    doc.setPlainText(text)
    hl = MarkdownSyntaxHighlighter(doc)

    b0 = doc.findBlockByNumber(0)
    assert b0.userState() == STATE_COMMENT_BLOCK
    assert b0.layout().formats()[0].format.fontItalic() is True

    b1 = doc.findBlockByNumber(1)
    assert b1.userState() == STATE_COMMENT_BLOCK
    assert b1.layout().formats()[0].format.fontItalic() is True

    b2 = doc.findBlockByNumber(2)
    assert b2.userState() == STATE_DEFAULT
    assert b2.layout().formats()[0].format.fontItalic() is True

    b3 = doc.findBlockByNumber(3)
    assert b3.userState() == STATE_DEFAULT
    assert len(b3.layout().formats()) == 0


def test_highlighter_visual_region_tokens(qapp):
    doc = QTextDocument()
    text = "Here is an image: ![[crop_p1_r1.png|region_id=123e4567-e89b-12d3-a456-426614174000]] in text."
    doc.setPlainText(text)
    hl = MarkdownSyntaxHighlighter(doc)

    b0 = doc.findBlockByNumber(0)
    formats = b0.layout().formats()
    assert len(formats) > 0

    # Locate region token format
    region_fmt = None
    for f in formats:
        if f.format.foreground().color().name() == "#34d399":
            region_fmt = f
            break

    assert region_fmt is not None
    assert region_fmt.format.background().color().name() == "#064e3b"
    assert region_fmt.format.fontWeight() == QFont.Weight.Bold
    assert region_fmt.start == text.find("![[")
    assert region_fmt.length == len("![[crop_p1_r1.png|region_id=123e4567-e89b-12d3-a456-426614174000]]")


def test_highlighter_inline_code_suppresses_bold(qapp):
    doc = QTextDocument()
    text = "Normal `code with **bold** inside` and **real bold**"
    doc.setPlainText(text)
    hl = MarkdownSyntaxHighlighter(doc)

    b0 = doc.findBlockByNumber(0)
    formats = b0.layout().formats()
    assert len(formats) == 2

    # First format is code span
    code_span = formats[0]
    assert code_span.format.foreground().color().name() == "#fcd34d"
    assert code_span.start == text.find("`")

    # Second format is real bold
    bold_span = formats[1]
    assert bold_span.format.foreground().color().name() == "#f9fafb"
    assert bold_span.format.fontWeight() == QFont.Weight.Bold
    assert bold_span.start == text.find("**real bold**")


def test_highlighter_links_and_thematic_breaks(qapp):
    doc = QTextDocument()
    text = "A link: [Open Document](https://example.com/doc_1)\n---\n> Blockquote text"
    doc.setPlainText(text)
    hl = MarkdownSyntaxHighlighter(doc)

    # Block 0: Link
    b0 = doc.findBlockByNumber(0)
    link_formats = [f for f in b0.layout().formats() if f.format.fontUnderline()]
    assert len(link_formats) == 1
    assert link_formats[0].format.foreground().color().name() == "#38bdf8"

    # Block 1: Thematic break
    b1 = doc.findBlockByNumber(1)
    assert len(b1.layout().formats()) == 1
    assert b1.layout().formats()[0].format.foreground().color().name() == "#4b5563"

    # Block 2: Blockquote
    b2 = doc.findBlockByNumber(2)
    assert len(b2.layout().formats()) >= 1
    assert b2.layout().formats()[0].format.foreground().color().name() == "#3b82f6"


def test_highlighter_source_text_integrity_invariant(qapp):
    """
    CRITICAL INVARIANT:
    Syntax highlighting MUST NOT mutate document text.
    doc.toPlainText() must be 100% identical before and after attaching highlighter.
    """
    raw_markdown = (
        "# Heading 1\n\n"
        "Paragraph with `code` and **bold** and *italic*.\n\n"
        "![[crop_p1_r1.png|region_id=abc_123]]\n\n"
        "```python\ndef test():\n    pass\n```\n\n"
        "<!-- Page 1 -->\n"
    )
    doc = QTextDocument()
    doc.setPlainText(raw_markdown)

    assert doc.toPlainText() == raw_markdown

    hl = MarkdownSyntaxHighlighter(doc)
    hl.rehighlight()

    # Must be 100% byte-for-byte identical
    assert doc.toPlainText() == raw_markdown


def test_controller_attach_text_document_lifecycle(qapp):
    """Verifies idempotent attachment, duplicate prevention, and clean detachment on shutdown."""
    mock_service = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_service)

    doc1 = QTextDocument()
    doc2 = QTextDocument()

    # Mock quickTextDocument objects
    mock_quick_doc1 = MagicMock()
    mock_quick_doc1.textDocument.return_value = doc1

    mock_quick_doc2 = MagicMock()
    mock_quick_doc2.textDocument.return_value = doc2

    assert ctrl._highlighter is None
    assert ctrl._text_document is None

    # Attach doc1
    ctrl.attachTextDocument(mock_quick_doc1)
    assert ctrl._highlighter is not None
    assert ctrl._text_document == doc1
    hl1 = ctrl._highlighter

    # Attach doc2 (re-attachment must detach hl1 cleanly)
    ctrl.attachTextDocument(mock_quick_doc2)
    assert ctrl._highlighter is not None
    assert ctrl._highlighter != hl1
    assert ctrl._text_document == doc2
    assert hl1.document() is None

    # Calling with None detaches cleanly
    ctrl.attachTextDocument(None)
    assert ctrl._highlighter is None
    assert ctrl._text_document is None

    # Attach again and verify shutdown cleanup
    ctrl.attachTextDocument(mock_quick_doc1)
    hl3 = ctrl._highlighter
    assert hl3 is not None
    ctrl.shutdown()
    assert ctrl._highlighter is None
    assert ctrl._text_document is None
    assert hl3.document() is None
