# ============================================================
#  tests/unit/test_markdown_editor_ux_integration.py
#  Unit and Integration Tests for Markdown Editor Cursor & Document Metrics
# ============================================================

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from interfaces.desktop.qt_compat import (
    QGuiApplication,
    QQmlApplicationEngine,
    QObject,
    QTextDocument,
)
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


def test_cursor_initial_and_empty_metrics(qapp):
    """Verifies default cursor position and metrics on clean controller."""
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)

    assert ctrl.cursorLine == 1
    assert ctrl.cursorColumn == 1
    assert ctrl.characterCount == 0
    assert ctrl.wordCount == 0


def test_cursor_multiline_navigation(qapp):
    """Verifies cursor line and column indexing across multiple lines."""
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    # Line 1: "first line\n" (11 UTF-16 code units: 10 chars + \n)
    # Line 2: "second line\n" (12 UTF-16 code units: 11 chars + \n)
    # Line 3: "third"
    text = "first line\nsecond line\nthird"
    ctrl.setSourceText(text)

    # Start of document
    ctrl.updateCursorPosition(0)
    assert ctrl.cursorLine == 1
    assert ctrl.cursorColumn == 1

    # End of first line (before newline)
    ctrl.updateCursorPosition(10)
    assert ctrl.cursorLine == 1
    assert ctrl.cursorColumn == 11

    # Start of second line (after first newline)
    ctrl.updateCursorPosition(11)
    assert ctrl.cursorLine == 2
    assert ctrl.cursorColumn == 1

    # Fifth character of second line ('n')
    ctrl.updateCursorPosition(15)
    assert ctrl.cursorLine == 2
    assert ctrl.cursorColumn == 5

    # Start of third line (11 + 12 = 23)
    ctrl.updateCursorPosition(23)
    assert ctrl.cursorLine == 3
    assert ctrl.cursorColumn == 1


def test_cursor_non_bmp_emoji_utf16_semantics(qapp):
    """
    CRITICAL INVARIANT:
    Non-BMP characters like 🐙 (surrogate pair) occupy 2 UTF-16 code units in Qt.
    Advancing past the emoji must advance column by 2.
    """
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    ctrl.setSourceText("🐙 hello\n😀 world")

    # Before emoji
    ctrl.updateCursorPosition(0)
    assert ctrl.cursorLine == 1
    assert ctrl.cursorColumn == 1

    # Immediately after emoji (position 2 in UTF-16 code units)
    ctrl.updateCursorPosition(2)
    assert ctrl.cursorLine == 1
    assert ctrl.cursorColumn == 3

    # After emoji + space
    ctrl.updateCursorPosition(3)
    assert ctrl.cursorLine == 1
    assert ctrl.cursorColumn == 4

    # Line 2 before second emoji (8 chars on line 1 + 1 newline = 9)
    ctrl.updateCursorPosition(9)
    assert ctrl.cursorLine == 2
    assert ctrl.cursorColumn == 1

    # Line 2 after second emoji (9 + 2 = 11)
    ctrl.updateCursorPosition(11)
    assert ctrl.cursorLine == 2
    assert ctrl.cursorColumn == 3


def test_cursor_persian_arabic_and_mixed_text(qapp):
    """Verifies cursor line and column tracking in Persian/Arabic and mixed text."""
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    text = "سلام دنیا\nHello جهان"
    ctrl.setSourceText(text)

    # Start of Line 1
    ctrl.updateCursorPosition(0)
    assert ctrl.cursorLine == 1
    assert ctrl.cursorColumn == 1

    # After 'سلام ' (5 characters)
    ctrl.updateCursorPosition(5)
    assert ctrl.cursorLine == 1
    assert ctrl.cursorColumn == 6

    # Line 2 (9 chars on line 1 + 1 newline = 10)
    ctrl.updateCursorPosition(10)
    assert ctrl.cursorLine == 2
    assert ctrl.cursorColumn == 1


def test_cursor_clamping_and_out_of_bounds(qapp):
    """Verifies that negative or out-of-bounds cursor positions clamp safely without exceptions."""
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    ctrl.setSourceText("hello")

    # Negative offset clamps to beginning
    ctrl.updateCursorPosition(-10)
    assert ctrl.cursorLine == 1
    assert ctrl.cursorColumn == 1

    # Excess offset clamps to end of document (position 5: Ln 1, Col 6)
    ctrl.updateCursorPosition(9999)
    assert ctrl.cursorLine == 1
    assert ctrl.cursorColumn == 6


def test_document_metrics_word_and_char_counts(qapp):
    """
    CRITICAL INVARIANT:
    characterCount counts Unicode code points (len(source_text)).
    wordCount counts Unicode whitespace-delimited tokens.
    """
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)

    # Unicode emoji: 1 code point, 1 token
    ctrl.setSourceText("🐙")
    assert ctrl.characterCount == 1
    assert ctrl.wordCount == 1

    # Persian text: 9 code points, 2 words
    ctrl.setSourceText("سلام دنیا")
    assert ctrl.characterCount == 9
    assert ctrl.wordCount == 2

    # Multiple whitespace, newlines, and mixed language
    ctrl.setSourceText("  Line 1   word\n\nLine 2   🐙  سلام  \t\n")
    # Tokens: ['Line', '1', 'word', 'Line', '2', '🐙', 'سلام'] -> 7 words
    assert ctrl.wordCount == 7
    assert ctrl.characterCount == len("  Line 1   word\n\nLine 2   🐙  سلام  \t\n")


def test_document_metrics_reset_on_clear_and_discard(qapp):
    """Verifies that metrics update accurately on clear() and discard()."""
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    initial_text = "# Header\nInitial text."
    ctrl._saved_source_text = initial_text
    ctrl.setSourceText(initial_text)

    assert ctrl.wordCount == 4
    assert ctrl.characterCount == len(initial_text)

    # Edit buffer
    ctrl.setSourceText(initial_text + "\nAdded more words here.")
    assert ctrl.wordCount == 8

    # Discard
    ctrl.discard()
    assert ctrl.sourceText == initial_text
    assert ctrl.wordCount == 4
    assert ctrl.characterCount == len(initial_text)
    assert ctrl.cursorLine == 1
    assert ctrl.cursorColumn == 1

    # Clear
    ctrl.clear()
    assert ctrl.sourceText == ""
    assert ctrl.wordCount == 0
    assert ctrl.characterCount == 0
    assert ctrl.cursorLine == 1
    assert ctrl.cursorColumn == 1


def test_qml_status_bar_integration(qapp):
    """Verifies MarkdownEditorStatusBar presence, wiring, and metrics updates in QML."""
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    ctrl.setSourceText("Hello world\nSecond line")

    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("markdownEditorController", ctrl)

    qml_file = Path(__file__).parent.parent.parent / "interfaces" / "desktop" / "qml" / "components" / "MarkdownEditorPane.qml"
    engine.load(str(qml_file))

    root_objs = engine.rootObjects()
    assert len(root_objs) > 0
    pane = root_objs[-1]

    status_bar = pane.findChild(QObject, "markdownEditorStatusBar")
    assert status_bar is not None

    cursor_label = status_bar.findChild(QObject, "editorCursorPositionLabel")
    assert cursor_label is not None
    assert "Ln 1, Col 1" in cursor_label.property("text")

    metrics_label = status_bar.findChild(QObject, "editorDocumentMetricsLabel")
    assert metrics_label is not None
    assert "4 words" in metrics_label.property("text")

    encoding_label = status_bar.findChild(QObject, "editorEncodingModeLabel")
    assert encoding_label is not None
    assert "Markdown" in encoding_label.property("text")

    # Move cursor
    ctrl.updateCursorPosition(15)
    assert "Ln 2, Col 4" in cursor_label.property("text")

    # Update text
    ctrl.setSourceText("One two three four five six")
    assert "6 words" in metrics_label.property("text")
