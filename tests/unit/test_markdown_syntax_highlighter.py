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


def test_highlighter_multiline_comment_closing_line_suppresses_inline_markdown(qapp):
    """
    CRITICAL REGRESSION TEST (Finding 4):
    On the closing line of a multiline comment, any Markdown syntax occurring
    before '-->' must remain comment-styled and must NOT be formatted as Markdown.
    Any Markdown occurring after '-->' must be formatted normally.
    """
    doc = QTextDocument()
    text = "<!-- Start of comment\n**bold** inside comment --> and `code` after"
    doc.setPlainText(text)
    hl = MarkdownSyntaxHighlighter(doc)
    hl.rehighlight()

    b1 = doc.findBlockByNumber(1)
    formats = b1.layout().formats()

    # Formats should be:
    # 1. 0 to 27 (comment): comment_fmt (#6b7280, italic)
    # 2. 32 to 38 (`code`): code_span_fmt (#fcd34d)
    assert len(formats) == 2

    comment_part = formats[0]
    assert comment_part.start == 0
    assert comment_part.length == 27
    assert comment_part.format.fontItalic() is True
    assert comment_part.format.foreground().color().name() == "#6b7280"

    code_part = formats[1]
    assert code_part.format.foreground().color().name() == "#fcd34d"
    assert code_part.start == 32
    assert code_part.length == len("`code`")


def test_controller_safe_detachment_when_cpp_object_deleted(qapp):
    """
    CRITICAL REGRESSION TEST (Finding 5):
    If the underlying QTextDocument is destroyed by Qt Quick before controller detachment,
    calling attachTextDocument() or shutdown() must safely handle the deleted C++ wrapper
    without raising an unhandled RuntimeError.
    """
    mock_service = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_service)

    doc = QTextDocument()
    mock_quick_doc = MagicMock()
    mock_quick_doc.textDocument.return_value = doc

    ctrl.attachTextDocument(mock_quick_doc)
    assert ctrl._highlighter is not None

    # Simulate underlying Qt destruction of doc and its child highlighter
    del doc

    # Subsequent detachment or shutdown must not raise RuntimeError!
    ctrl.attachTextDocument(None)
    assert ctrl._highlighter is None

    ctrl.shutdown()
    assert ctrl._is_shutdown is True


def test_highlighter_canonical_visual_region_tokens(qapp):
    """Verifies that canonical CommonMark visual region tokens are styled as emerald badges."""
    doc = QTextDocument()
    token = '![Chart](crops/chart_p1.jpg "polpo:region=123e4567-e89b-12d3-a456-426614174000;occ=223e4567-e89b-12d3-a456-426614174000")'
    text = f"Before {token} after."
    doc.setPlainText(text)
    hl = MarkdownSyntaxHighlighter(doc, theme="dark")

    b0 = doc.findBlockByNumber(0)
    formats = b0.layout().formats()
    assert len(formats) > 0

    region_fmt = None
    for f in formats:
        if f.format.foreground().color().name() == "#34d399":
            region_fmt = f
            break

    assert region_fmt is not None
    assert region_fmt.format.background().color().name() == "#064e3b"
    assert region_fmt.format.fontWeight() == QFont.Weight.Bold
    assert region_fmt.start == text.find("![Chart")
    assert region_fmt.length == len(token)


def test_highlighter_canonical_token_variations(qapp):
    """Tests angle bracket destination, single quote title, empty alt, and escaped brackets."""
    doc = QTextDocument()
    tokens = [
        '![Angle](<crops/with space.png> "polpo:region=r1;occ=o1")',
        "![Single](crop.png 'polpo:region=r2;occ=o2')",
        '![](crop.png "polpo:region=r3;occ=o3")',
        '![Escaped [1\\]](crop.png "polpo:region=r4;occ=o4")',
    ]
    text = "\n".join(tokens)
    doc.setPlainText(text)
    hl = MarkdownSyntaxHighlighter(doc, theme="dark")

    for i, tok in enumerate(tokens):
        block = doc.findBlockByNumber(i)
        formats = block.layout().formats()
        assert len(formats) == 1, f"Failed for token variation: {tok}"
        f = formats[0]
        assert f.start == 0
        assert f.length == len(tok)
        assert f.format.foreground().color().name() == "#34d399"
        assert f.format.background().color().name() == "#064e3b"


def test_highlighter_multiple_canonical_tokens_on_same_line(qapp):
    """Verifies that multiple canonical tokens on the same line are highlighted independently."""
    tok1 = '![First](crop1.jpg "polpo:region=r1;occ=o1")'
    tok2 = '![Second](crop2.jpg "polpo:region=r2;occ=o2")'
    text = f"Lead {tok1} middle {tok2} tail"
    doc = QTextDocument()
    doc.setPlainText(text)
    hl = MarkdownSyntaxHighlighter(doc, theme="dark")

    b0 = doc.findBlockByNumber(0)
    formats = [f for f in b0.layout().formats() if f.format.foreground().color().name() == "#34d399"]
    assert len(formats) == 2
    assert formats[0].start == text.find(tok1)
    assert formats[0].length == len(tok1)
    assert formats[1].start == text.find(tok2)
    assert formats[1].length == len(tok2)


def test_highlighter_code_fences_suppress_canonical_tokens(qapp):
    """Verifies that canonical tokens inside backtick or tilde code fences are NOT styled as badges."""
    canonical_token = '![Chart](crop.jpg "polpo:region=r;occ=o")'
    text = f"```\n{canonical_token}\n```\n~~~\n{canonical_token}\n~~~"
    doc = QTextDocument()
    doc.setPlainText(text)
    hl = MarkdownSyntaxHighlighter(doc, theme="dark")

    # Line 1: inside ``` fence
    b1 = doc.findBlockByNumber(1)
    assert b1.userState() == STATE_CODE_BLOCK
    assert len(b1.layout().formats()) == 1
    assert b1.layout().formats()[0].format.foreground().color().name() == "#c4b5fd"

    # Line 4: inside ~~~ fence
    b4 = doc.findBlockByNumber(4)
    assert b4.userState() == STATE_CODE_BLOCK
    assert len(b4.layout().formats()) == 1
    assert b4.layout().formats()[0].format.foreground().color().name() == "#c4b5fd"


def test_highlighter_inline_code_and_comments_suppress_tokens(qapp):
    """Inline code span and HTML comments suppress inner visual region formatting."""
    token = '![Chart](crop.jpg "polpo:region=r;occ=o")'
    text = f"`{token}` and <!-- {token} -->"
    doc = QTextDocument()
    doc.setPlainText(text)
    hl = MarkdownSyntaxHighlighter(doc, theme="dark")

    b0 = doc.findBlockByNumber(0)
    formats = b0.layout().formats()
    # Should only contain code span format and comment format, zero region badges (#34d399)
    badge_formats = [f for f in formats if f.format.foreground().color().name() == "#34d399"]
    assert len(badge_formats) == 0


def test_highlighter_negative_matching_cases(qapp):
    """Standard images and malformed polpo tokens must NOT be styled as visual region badges."""
    cases = [
        "![Alt](photo.jpg)",                     # Standard image
        '![Alt](photo.jpg "Regular Title")',     # Image with regular title
        '![Alt](photo.jpg "polpo:unknown")',     # Missing region and occ
        '![Alt](photo.jpg "polpo:region=r\')',   # Mismatched quotes
    ]
    doc = QTextDocument()
    doc.setPlainText("\n".join(cases))
    hl = MarkdownSyntaxHighlighter(doc, theme="dark")

    for i, line in enumerate(cases):
        block = doc.findBlockByNumber(i)
        badge_formats = [f for f in block.layout().formats() if f.format.foreground().color().name() == "#34d399"]
        assert len(badge_formats) == 0, f"False positive badge on: {line}"


def test_highlighter_theme_switching(qapp):
    """Verifies that dark and light themes apply correct semantic palette badge styling."""
    doc = QTextDocument()
    token = '![Badge](crop.jpg "polpo:region=r;occ=o")'
    doc.setPlainText(token)
    hl = MarkdownSyntaxHighlighter(doc, theme="dark")

    b0 = doc.findBlockByNumber(0)
    formats_dark = b0.layout().formats()
    assert formats_dark[0].format.foreground().color().name() == "#34d399"
    assert formats_dark[0].format.background().color().name() == "#064e3b"

    # Switch to light theme
    hl.set_theme("light")
    b0_light = doc.findBlockByNumber(0)
    formats_light = b0_light.layout().formats()
    assert formats_light[0].format.foreground().color().name() == "#047857"
    assert formats_light[0].format.background().color().name() == "#d1fae5"


def test_highlighter_undo_redo_and_text_stability(qapp):
    """
    Verifies that formatting never mutates document text or breaks native undo/redo.
    """
    doc = QTextDocument()
    token = '![Badge](crop.jpg "polpo:region=r;occ=o")'
    cursor = QTextCursor(doc)

    cursor.beginEditBlock()
    cursor.insertText("Initial text ")
    cursor.endEditBlock()

    hl = MarkdownSyntaxHighlighter(doc)

    cursor.beginEditBlock()
    cursor.insertText(token)
    cursor.endEditBlock()

    assert doc.toPlainText() == f"Initial text {token}"

    doc.undo()
    assert doc.toPlainText() == "Initial text "

    doc.redo()
    assert doc.toPlainText() == f"Initial text {token}"
