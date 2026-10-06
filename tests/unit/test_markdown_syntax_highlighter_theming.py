# ============================================================
#  tests/unit/test_markdown_syntax_highlighter_theming.py
#  Unit tests for Markdown Syntax Highlighter Theming (TICK-P01C)
# ============================================================

from unittest.mock import MagicMock
import pytest

from interfaces.desktop.qt_compat import (
    QGuiApplication,
    QTextDocument,
    QFont,
)
from interfaces.desktop.syntax.markdown_syntax_highlighter import (
    MarkdownSyntaxHighlighter,
    DARK_SYNTAX_PALETTE,
    LIGHT_SYNTAX_PALETTE,
    STATE_CODE_BLOCK,
)
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


def test_syntax_highlighter_default_theme_is_dark(qapp):
    """Highlighter initializes with dark palette by default."""
    doc = QTextDocument()
    doc.setPlainText("# Heading 1\n`code`\n**bold**")
    hl = MarkdownSyntaxHighlighter(doc)

    assert hl.current_theme == "dark"
    b0 = doc.findBlockByNumber(0)
    formats0 = b0.layout().formats()
    assert len(formats0) > 0
    assert formats0[0].format.foreground().color().name() == DARK_SYNTAX_PALETTE["headings"][0]


def test_syntax_highlighter_switch_to_light_theme(qapp):
    """Highlighter updates formatting rules and rehighlights when set_theme('light') is called."""
    doc = QTextDocument()
    doc.setPlainText("# Heading 1\n```\ncode block\n```\n`code`\n**bold**\n*italic*\n[link](url)\n---\n> quote")
    hl = MarkdownSyntaxHighlighter(doc)

    # Initial dark check
    b0 = doc.findBlockByNumber(0)
    assert b0.layout().formats()[0].format.foreground().color().name() == DARK_SYNTAX_PALETTE["headings"][0]

    # Switch to light theme
    hl.set_theme("light")
    assert hl.current_theme == "light"

    # Verify H1 updated to light heading color
    b0 = doc.findBlockByNumber(0)
    assert b0.layout().formats()[0].format.foreground().color().name() == LIGHT_SYNTAX_PALETTE["headings"][0]

    # Verify code block (block 1) updated to light code block colors
    b1 = doc.findBlockByNumber(1)
    assert b1.userState() == STATE_CODE_BLOCK
    fmts1 = b1.layout().formats()
    assert len(fmts1) > 0
    b1_fmt = fmts1[0].format
    assert b1_fmt.foreground().color().name() == LIGHT_SYNTAX_PALETTE["code_block_fg"]
    assert b1_fmt.background().color().name() == LIGHT_SYNTAX_PALETTE["code_block_bg"]

    # Verify inline code (block 4)
    b4 = doc.findBlockByNumber(4)
    fmts4 = b4.layout().formats()
    assert fmts4[0].format.foreground().color().name() == LIGHT_SYNTAX_PALETTE["code_span_fg"]

    # Verify bold (block 5)
    b5 = doc.findBlockByNumber(5)
    fmts5 = b5.layout().formats()
    assert fmts5[0].format.foreground().color().name() == LIGHT_SYNTAX_PALETTE["bold_fg"]

    # Verify italic (block 6)
    b6 = doc.findBlockByNumber(6)
    fmts6 = b6.layout().formats()
    assert fmts6[0].format.foreground().color().name() == LIGHT_SYNTAX_PALETTE["italic_fg"]

    # Verify link (block 7)
    b7 = doc.findBlockByNumber(7)
    fmts7 = b7.layout().formats()
    link_formats = [f for f in fmts7 if f.format.fontUnderline()]
    assert len(link_formats) == 1
    assert link_formats[0].format.foreground().color().name() == LIGHT_SYNTAX_PALETTE["link_fg"]

    # Verify thematic break (block 8)
    b8 = doc.findBlockByNumber(8)
    fmts8 = b8.layout().formats()
    assert fmts8[0].format.foreground().color().name() == LIGHT_SYNTAX_PALETTE["thematic_break_fg"]

    # Verify blockquote (block 9)
    b9 = doc.findBlockByNumber(9)
    fmts9 = b9.layout().formats()
    assert fmts9[0].format.foreground().color().name() == LIGHT_SYNTAX_PALETTE["blockquote_fg"]


def test_syntax_highlighter_switch_back_to_dark(qapp):
    """Highlighter toggles cleanly between light and dark modes repeatedly."""
    doc = QTextDocument()
    doc.setPlainText("# Heading 1")
    hl = MarkdownSyntaxHighlighter(doc, theme="light")

    assert hl.current_theme == "light"
    b0 = doc.findBlockByNumber(0)
    fmts0 = b0.layout().formats()
    assert fmts0[0].format.foreground().color().name() == LIGHT_SYNTAX_PALETTE["headings"][0]

    hl.set_theme("dark")
    assert hl.current_theme == "dark"
    fmts0_dark = b0.layout().formats()
    assert fmts0_dark[0].format.foreground().color().name() == DARK_SYNTAX_PALETTE["headings"][0]


def test_syntax_highlighter_region_token_theming(qapp):
    """Region tokens use distinct high-contrast emerald colors in both dark and light modes."""
    doc = QTextDocument()
    text = "![[crop_p1_r1.png|region_id=abc-123]]"
    doc.setPlainText(text)
    hl = MarkdownSyntaxHighlighter(doc)

    b0 = doc.findBlockByNumber(0)
    dark_fmts = b0.layout().formats()
    assert len(dark_fmts) > 0
    dark_fmt = dark_fmts[0].format
    assert dark_fmt.foreground().color().name() == DARK_SYNTAX_PALETTE["region_fg"]
    assert dark_fmt.background().color().name() == DARK_SYNTAX_PALETTE["region_bg"]

    hl.set_theme("light")
    light_fmts = b0.layout().formats()
    assert len(light_fmts) > 0
    light_fmt = light_fmts[0].format
    assert light_fmt.foreground().color().name() == LIGHT_SYNTAX_PALETTE["region_fg"]
    assert light_fmt.background().color().name() == LIGHT_SYNTAX_PALETTE["region_bg"]


def test_syntax_highlighter_source_text_integrity_on_theme_switch(qapp):
    """Document plain text must remain byte-for-byte identical across theme switches."""
    raw_text = "# Title\n\n```python\nprint('hello')\n```\n\n**Bold** text with `code`.\n"
    doc = QTextDocument()
    doc.setPlainText(raw_text)

    hl = MarkdownSyntaxHighlighter(doc)
    assert doc.toPlainText() == raw_text

    hl.set_theme("light")
    assert doc.toPlainText() == raw_text

    hl.set_theme("dark")
    assert doc.toPlainText() == raw_text


def test_editor_controller_forwards_syntax_theme_to_highlighter(qapp):
    """MarkdownEditorController.set_syntax_theme forwards the active theme to its highlighter."""
    mock_service = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_service)

    doc = QTextDocument()
    doc.setPlainText("# Heading 1")
    mock_quick_doc = MagicMock()
    mock_quick_doc.textDocument.return_value = doc

    ctrl.attachTextDocument(mock_quick_doc)
    assert ctrl._highlighter is not None
    assert ctrl._highlighter.current_theme == "dark"

    # Update theme via controller
    ctrl.set_syntax_theme("light")
    assert ctrl._highlighter.current_theme == "light"
    b0 = doc.findBlockByNumber(0)
    assert b0.layout().formats()[0].format.foreground().color().name() == LIGHT_SYNTAX_PALETTE["headings"][0]

    # Switch back via controller
    ctrl.set_syntax_theme("dark")
    assert ctrl._highlighter.current_theme == "dark"
    assert b0.layout().formats()[0].format.foreground().color().name() == DARK_SYNTAX_PALETTE["headings"][0]


def test_editor_controller_applies_theme_to_newly_attached_document(qapp):
    """If syntax theme was set to light before attaching a document, the new highlighter is light."""
    mock_service = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_service)

    # Set syntax theme before document is attached
    ctrl.set_syntax_theme("light")

    doc = QTextDocument()
    doc.setPlainText("# Heading 1")
    mock_quick_doc = MagicMock()
    mock_quick_doc.textDocument.return_value = doc

    ctrl.attachTextDocument(mock_quick_doc)
    assert ctrl._highlighter is not None
    assert ctrl._highlighter.current_theme == "light"

    b0 = doc.findBlockByNumber(0)
    assert b0.layout().formats()[0].format.foreground().color().name() == LIGHT_SYNTAX_PALETTE["headings"][0]
