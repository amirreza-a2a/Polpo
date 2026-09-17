# ============================================================
#  tests/unit/test_markdown_editor_search.py
#  Unit tests for Markdown Editor Search & Replace Engine
# ============================================================

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from interfaces.desktop.qt_compat import (
    QGuiApplication,
    QTextDocument,
    QTextCursor,
    QQmlApplicationEngine,
    QObject,
)
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


def test_search_basic_and_case_sensitivity(qapp):
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    ctrl.setSourceText("Hello world hello HELLO World")

    # Default: case-insensitive
    ctrl.setSearchQuery("hello")
    assert ctrl.searchTotalMatches == 3
    assert ctrl.searchMatchIndex == 1

    # Case-sensitive
    ctrl.setSearchCaseSensitive(True)
    assert ctrl.searchTotalMatches == 1
    assert ctrl.searchMatchIndex == 1
    ranges = ctrl.getMatchRanges()
    assert len(ranges) == 1
    assert ranges[0] == [12, 17]


def test_search_whole_word(qapp):
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    ctrl.setSourceText("the theme is authentic")

    ctrl.setSearchQuery("the")
    assert ctrl.searchTotalMatches == 3  # the, theme, authentic

    ctrl.setSearchWholeWord(True)
    assert ctrl.searchTotalMatches == 1
    assert ctrl.getMatchRanges()[0] == [0, 3]


def test_search_unicode_non_bmp_emoji_offsets(qapp):
    """
    CRITICAL TEST:
    Non-BMP characters (surrogate pairs like 🐙 and 😀) span 2 UTF-16 code units.
    Verify that matchSelected emits exact UTF-16 offsets matching QTextCursor selection.
    """
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    text = "🐙 hello world 😀 hello again"
    ctrl.setSourceText(text)

    selected_spy = MagicMock()
    ctrl.matchSelected.connect(selected_spy)

    ctrl.setSearchQuery("hello")
    assert ctrl.searchTotalMatches == 2
    assert ctrl.searchMatchIndex == 1

    # Match 1: after '🐙 ' (2 code units for emoji + 1 for space = offset 3)
    selected_spy.assert_called_with(3, 8)

    # Verify QTextCursor selection at (3, 8) extracts exactly 'hello'
    doc = ctrl._get_document()
    c = QTextCursor(doc)
    c.setPosition(3)
    c.setPosition(8, QTextCursor.MoveMode.KeepAnchor)
    assert c.selectedText() == "hello"

    # Forward navigation to Match 2
    ctrl.findNext()
    assert ctrl.searchMatchIndex == 2
    # Match 2: '🐙 hello world 😀 ' -> 3 + 5 ('hello') + 7 (' world ') + 2 ('😀') + 1 (' ') = 18
    selected_spy.assert_called_with(18, 23)

    c.setPosition(18)
    c.setPosition(23, QTextCursor.MoveMode.KeepAnchor)
    assert c.selectedText() == "hello"


def test_search_persian_arabic_and_mixed_ltr_rtl(qapp):
    """Verifies search offsets and selection in Persian/Arabic and mixed LTR/RTL text."""
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    text = "متن سلام دنیا سلام پایان OCR text سلام"
    ctrl.setSourceText(text)

    ctrl.setSearchQuery("سلام")
    assert ctrl.searchTotalMatches == 3
    ranges = ctrl.getMatchRanges()
    assert len(ranges) == 3

    doc = ctrl._get_document()
    for s, e in ranges:
        c = QTextCursor(doc)
        c.setPosition(s)
        c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
        assert c.selectedText() == "سلام"


def test_search_forward_and_backward_wrapping(qapp):
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    ctrl.setSourceText("one two one two one")

    ctrl.setSearchQuery("one")
    assert ctrl.searchTotalMatches == 3
    assert ctrl.searchMatchIndex == 1

    # Next: 1 -> 2 -> 3 -> 1 (wrap)
    ctrl.findNext()
    assert ctrl.searchMatchIndex == 2
    ctrl.findNext()
    assert ctrl.searchMatchIndex == 3
    ctrl.findNext()
    assert ctrl.searchMatchIndex == 1

    # Prev: 1 -> 3 (wrap) -> 2 -> 1
    ctrl.findPrevious()
    assert ctrl.searchMatchIndex == 3
    ctrl.findPrevious()
    assert ctrl.searchMatchIndex == 2
    ctrl.findPrevious()
    assert ctrl.searchMatchIndex == 1


def test_search_cursor_aware_navigation(qapp):
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    ctrl.setSourceText("alpha beta alpha gamma alpha")

    ctrl._search_query = "alpha"
    ctrl._recompute_matches()
    ctrl._search_match_index = 0  # reset active match

    # Search next starting from cursor position 10 (after first alpha)
    ctrl.findNext(current_cursor_pos=10)
    assert ctrl.searchMatchIndex == 2
    assert ctrl._matches[1] == (11, 16)


def test_search_empty_and_no_match(qapp):
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    ctrl.setSourceText("simple text content")

    ctrl.setSearchQuery("missing")
    assert ctrl.searchTotalMatches == 0
    assert ctrl.searchMatchIndex == 0

    ctrl.setSearchQuery("")
    assert ctrl.searchTotalMatches == 0
    assert ctrl.searchMatchIndex == 0


def test_replace_current_refuses_arbitrary_selection(qapp):
    """
    CRITICAL INVARIANT:
    Replace Current MUST NOT silently replace arbitrary user selection.
    If selectionStart/selectionEnd does not match active match, it must refuse.
    """
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    ctrl.setSourceText("apple banana cherry apple")

    ctrl.setSearchQuery("apple")
    ctrl.setReplaceQuery("orange")
    assert ctrl.searchMatchIndex == 1
    assert ctrl._matches[0] == (0, 5)

    reselected_spy = MagicMock()
    ctrl.matchSelected.connect(reselected_spy)

    # User arbitrarily selects 'banana' (6, 12)
    success = ctrl.replaceCurrent(selection_start=6, selection_end=12)
    assert success is False
    assert "banana" in ctrl.sourceText
    assert ctrl.sourceText == "apple banana cherry apple"
    # Re-emitted active match
    reselected_spy.assert_called_with(0, 5)

    # Valid replacement matching active match
    success2 = ctrl.replaceCurrent(selection_start=0, selection_end=5)
    assert success2 is True
    assert ctrl.sourceText == "orange banana cherry apple"
    assert ctrl.isDirty is True


def test_replace_all_undo_atomicity(qapp):
    """
    CRITICAL INVARIANT:
    Replace All using beginEditBlock/endEditBlock groups all replacements
    into a SINGLE user-level undo transaction.
    """
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    initial_text = "🐙 item one, item two, item three"
    ctrl.setSourceText(initial_text)
    ctrl.set_saved_source_text_for_test = lambda t: setattr(ctrl, "_saved_source_text", t)
    ctrl._saved_source_text = initial_text

    ctrl.setSearchQuery("item")
    ctrl.setReplaceQuery("element")
    assert ctrl.searchTotalMatches == 3

    count = ctrl.replaceAll()
    assert count == 3
    assert ctrl.sourceText == "🐙 element one, element two, element three"
    assert ctrl.isDirty is True

    # Test single undo on underlying QTextDocument
    doc = ctrl._get_document()
    assert doc.isUndoAvailable() is True
    doc.undo()

    # Document text must revert COMPLETELY in one undo step!
    assert doc.toPlainText() == initial_text


def test_qml_markdown_editor_search_bar_interaction(qapp):
    """Verifies search bar presence, visibility toggle, and selection binding in QML."""
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    ctrl.setSourceText("first alpha second alpha third alpha")

    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("markdownEditorController", ctrl)

    qml_file = Path(__file__).parent.parent.parent / "interfaces" / "desktop" / "qml" / "components" / "MarkdownEditorPane.qml"
    engine.load(str(qml_file))

    root_objs = engine.rootObjects()
    assert len(root_objs) > 0
    pane = root_objs[-1]

    search_bar = pane.findChild(QObject, "markdownEditorSearchBar")
    assert search_bar is not None
    assert search_bar.property("visible") is False

    # Open search
    ctrl.openSearch()
    assert search_bar.property("visible") is True

    search_field = search_bar.findChild(QObject, "editorSearchField")
    assert search_field is not None

    find_next_btn = search_bar.findChild(QObject, "editorFindNextButton")
    assert find_next_btn is not None

    find_prev_btn = search_bar.findChild(QObject, "editorFindPrevButton")
    assert find_prev_btn is not None

    # Search for alpha
    ctrl.setSearchQuery("alpha")
    assert ctrl.searchTotalMatches == 3

    # Close search
    ctrl.closeSearch()
    assert search_bar.property("visible") is False


def test_replace_current_sequential_advancement_and_no_skipping(qapp):
    """
    CRITICAL REGRESSION TEST (Finding 1):
    Sequential replaceCurrent() must reach and replace EVERY occurrence
    without skipping the match immediately following each replacement.
    """
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    ctrl.setSourceText("foo foo foo")
    ctrl.setSearchQuery("foo")
    ctrl.setReplaceQuery("bar")

    selected_ranges = []
    ctrl.matchSelected.connect(lambda s, e: selected_ranges.append((s, e)))

    assert ctrl.searchTotalMatches == 3
    assert ctrl.searchMatchIndex == 1
    # Match 1 is initially selected at (0, 3)

    # 1st replace: replaces Match 1 at (0, 3)
    ok1 = ctrl.replaceCurrent(0, 3)
    assert ok1 is True
    assert ctrl.sourceText == "bar foo foo"
    assert ctrl.searchTotalMatches == 2
    assert ctrl.searchMatchIndex == 1
    # Next active match MUST be the former Match 2 at (4, 7)
    assert selected_ranges[-1] == (4, 7)

    # 2nd replace: replaces former Match 2 at (4, 7)
    ok2 = ctrl.replaceCurrent(4, 7)
    assert ok2 is True
    assert ctrl.sourceText == "bar bar foo"
    assert ctrl.searchTotalMatches == 1
    assert ctrl.searchMatchIndex == 1
    # Next active match MUST be the former Match 3 at (8, 11)
    assert selected_ranges[-1] == (8, 11)

    # 3rd replace: replaces former Match 3 at (8, 11)
    ok3 = ctrl.replaceCurrent(8, 11)
    assert ok3 is True
    assert ctrl.sourceText == "bar bar bar"
    assert ctrl.searchTotalMatches == 0
    assert ctrl.searchMatchIndex == 0


def test_replace_current_shorter_longer_and_self_referential(qapp):
    """
    CRITICAL REGRESSION TEST (Finding 1):
    Tests replaceCurrent() with:
    1. Shorter replacement string.
    2. Longer replacement string.
    3. Replacement text containing the search query itself (e.g. 'foo' -> 'foobar').
    """
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)

    # 1. Shorter replacement: "longer" -> "s"
    ctrl.setSourceText("longer longer longer")
    ctrl.setSearchQuery("longer")
    ctrl.setReplaceQuery("s")
    assert ctrl.replaceCurrent(0, 6) is True
    assert ctrl.sourceText == "s longer longer"
    assert ctrl._matches[ctrl.searchMatchIndex - 1] == (2, 8)

    # 2. Longer replacement: "s" -> "longer"
    ctrl.setSourceText("s s s")
    ctrl.setSearchQuery("s")
    ctrl.setReplaceQuery("longer")
    assert ctrl.replaceCurrent(0, 1) is True
    assert ctrl.sourceText == "longer s s"
    assert ctrl._matches[ctrl.searchMatchIndex - 1] == (7, 8)

    # 3. Replacement text containing the search query: "foo" -> "foobar"
    ctrl.setSourceText("foo foo foo")
    ctrl.setSearchQuery("foo")
    ctrl.setReplaceQuery("foobar")
    assert ctrl.replaceCurrent(0, 3) is True
    assert ctrl.sourceText == "foobar foo foo"
    # The next active match MUST NOT be the 'foo' inside 'foobar' (0, 3)!
    # It MUST be the second occurrence at (7, 10)!
    assert ctrl._matches[ctrl.searchMatchIndex - 1] == (7, 10)
    assert ctrl.searchMatchIndex == 2


def test_search_state_invalidation_after_discard_and_reload(qapp):
    """
    CRITICAL REGRESSION TEST (Finding 3):
    1. After discard(), search matches must recompute against the restored saved text.
    2. After loading another document via _on_internal_loaded(), matches from previous doc must not survive.
    """
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    saved = "clean document with target"
    ctrl._saved_source_text = saved
    ctrl.setSourceText(saved)
    ctrl.setSearchQuery("extra")
    assert ctrl.searchTotalMatches == 0

    # User types dirty text containing the query
    ctrl.setSourceText("clean document with target and extra word")
    assert ctrl.searchTotalMatches == 1
    assert ctrl.getMatchRanges() == [[31, 36]]

    # Discard dirty text: matches MUST reset to 0 for 'extra'
    ctrl.discard()
    assert ctrl.sourceText == saved
    assert ctrl.searchTotalMatches == 0
    assert ctrl.getMatchRanges() == []

    # Now search for 'target' (present in Job A)
    ctrl.setSearchQuery("target")
    assert ctrl.searchTotalMatches == 1
    assert ctrl.getMatchRanges() == [[20, 26]]

    # Load Job B which does not contain 'target'
    ctrl._on_internal_loaded(ctrl._request_id, "different document text without it", 2)
    assert ctrl.searchTotalMatches == 0
    assert ctrl.getMatchRanges() == []

    # Load Job C which has multiple occurrences of 'target'
    ctrl._on_internal_loaded(ctrl._request_id, "target in header and target in footer", 3)
    assert ctrl.searchTotalMatches == 2
    assert ctrl.getMatchRanges() == [[0, 6], [21, 27]]


def test_qml_replace_drawer_toggle_and_collapse(qapp):
    """
    CRITICAL REGRESSION TEST (Finding 2):
    Verifies that clicking the Replace toggle button invokes closeReplace() / openReplace()
    without read-only property assignment errors, and can re-open cleanly.
    """
    mock_svc = MagicMock()
    ctrl = MarkdownEditorController(editor_service=mock_svc)
    ctrl.setSourceText("some text for testing")

    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("markdownEditorController", ctrl)

    qml_file = Path(__file__).parent.parent.parent / "interfaces" / "desktop" / "qml" / "components" / "MarkdownEditorPane.qml"
    engine.load(str(qml_file))

    pane = engine.rootObjects()[-1]
    search_bar = pane.findChild(QObject, "markdownEditorSearchBar")
    assert search_bar is not None

    toggle_btn = search_bar.findChild(QObject, "editorToggleReplaceButton")
    assert toggle_btn is not None

    # Open search drawer
    ctrl.openSearch()
    assert ctrl.isSearchOpen is True
    assert ctrl.isReplaceOpen is False

    # Click toggle button to OPEN Replace row
    toggle_btn.clicked.emit()
    assert ctrl.isReplaceOpen is True
    assert toggle_btn.property("text") == "▼ Replace"

    # Click toggle button to COLLAPSE Replace row (Finding 2 fix verification)
    toggle_btn.clicked.emit()
    assert ctrl.isReplaceOpen is False
    assert toggle_btn.property("text") == "▶ Replace"

    # Click again to RE-OPEN
    toggle_btn.clicked.emit()
    assert ctrl.isReplaceOpen is True

