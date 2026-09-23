"""End-to-End Bidirectional Caret and Scroll Characterization Test Suite (TICK-011B).

Verifies authoritative bidirectional synchronization across complex document types and Unicode edge cases:
1. Plain ASCII documents (headings, lists, code blocks, blockquotes).
2. Persian / Arabic RTL text with multibyte UTF-8 characters.
3. Supplementary-plane Unicode (emojis, surrogate pairs, ZWJ sequences).
4. Legacy visual crop tokens (![[...]]) with reverse coordinate mapping.
5. Display and inline math equations ($$...$$ and $...$).
6. Literal tabs and CRLF line endings.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtGui import QGuiApplication

from application.services.markdown_viewer_service import MarkdownViewerService
from infrastructure.markdown.pandoc_parser import PandocParser
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.coordinators.review_workspace_sync_coordinator import ReviewWorkspaceSyncCoordinator
from interfaces.desktop.models.markdown_document_model import MarkdownDocumentModel


@pytest.fixture(scope="module")
def qapp() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


@pytest.fixture
def sync_harness(qapp: QGuiApplication):
    """Factory fixture creating an integrated editor-preview-sync harness."""
    parser = PandocParser()
    viewer_service = MarkdownViewerService(
        parser=parser,
        uow_factory=MagicMock(),
        storage=MagicMock(),
    )

    def _setup(raw_text: str):
        dto = viewer_service.render_text(raw_text=raw_text, active_regions=[], job_id=1)

        model = MarkdownDocumentModel()
        viewer_ctrl = MarkdownViewerController(viewer_service=None)
        viewer_ctrl._model = model
        model.set_document(dto)

        editor_ctrl = MarkdownEditorController(editor_service=None)
        editor_ctrl.set_source_text(raw_text)

        coordinator = ReviewWorkspaceSyncCoordinator(
            editor_controller=editor_ctrl,
            viewer_controller=viewer_ctrl,
            debounce_source_ms=0,
            debounce_preview_ms=0,
            lock_release_ms=0,
        )
        coordinator.setDualPaneActive(True)

        return {
            "dto": dto,
            "model": model,
            "viewer_ctrl": viewer_ctrl,
            "editor_ctrl": editor_ctrl,
            "coordinator": coordinator,
            "raw_text": raw_text,
        }

    return _setup


# ===========================================================================
# 1. Plain ASCII Documents
# ===========================================================================

def test_sync_plain_ascii_document(sync_harness):
    """Validates bidirectional synchronization on structured ASCII documents."""
    text = (
        "# Main Title\n\n"
        "This is an introductory paragraph with some text.\n\n"
        "## Section Two\n\n"
        "- First item\n"
        "- Second item\n\n"
        "```python\n"
        "def hello():\n"
        "    return 'world'\n"
        "```\n"
    )
    env = sync_harness(text)
    editor_ctrl: MarkdownEditorController = env["editor_ctrl"]
    viewer_ctrl: MarkdownViewerController = env["viewer_ctrl"]
    coord: ReviewWorkspaceSyncCoordinator = env["coordinator"]

    # 1. Preview Click -> Editor Navigation
    navigated_positions = []
    editor_ctrl.requestNavigateToPosition.connect(navigated_positions.append)

    # Click Node 0 (# Main Title, line 1, col 1)
    coord.report_node_clicked(0)
    assert navigated_positions[-1] == 0

    # Click Node 1 (intro paragraph, line 3, col 1)
    doc = editor_ctrl._get_document()
    p1_expected_pos = doc.findBlockByLineNumber(2).position()
    coord.report_node_clicked(1)
    assert navigated_positions[-1] == p1_expected_pos

    # Click Node 2 (## Section Two, line 5, col 1)
    sec2_expected_pos = doc.findBlockByLineNumber(4).position()
    coord.report_node_clicked(2)
    assert navigated_positions[-1] == sec2_expected_pos

    # Click Node 4 (Code block)
    code_line = None
    for idx, node in enumerate(env["dto"].nodes):
        if node.node_type == "code_block":
            code_line = node.source_start_line
            code_node_idx = idx
            break
    assert code_line is not None
    code_expected_pos = doc.findBlockByLineNumber(code_line - 1).position()
    coord.report_node_clicked(code_node_idx)
    assert navigated_positions[-1] == code_expected_pos

    # 2. Editor Caret Movement -> Preview Selection
    # Place cursor inside section two heading
    editor_ctrl.updateCursorPosition(sec2_expected_pos + 4)
    assert viewer_ctrl.selectedNodeIndex == 2

    # Place cursor inside code block body
    editor_ctrl.updateCursorPosition(code_expected_pos + 12)
    assert viewer_ctrl.selectedNodeIndex == code_node_idx


# ===========================================================================
# 2. Persian / Arabic RTL Text
# ===========================================================================

def test_sync_persian_rtl_document(sync_harness):
    """Validates that multibyte UTF-8 Persian text maintains 1:1 UTF-16 caret synchronization."""
    text = (
        "# مستند تست پلپو\n\n"
        "این یک متن فارسی برای آزمایش همگام‌سازی دوطرفه میان ویرایشگر و پیش‌نمایش است.\n\n"
        "## بخش دوم\n\n"
        "- نکته اول\n"
        "- نکته دوم\n"
    )
    env = sync_harness(text)
    editor_ctrl: MarkdownEditorController = env["editor_ctrl"]
    viewer_ctrl: MarkdownViewerController = env["viewer_ctrl"]
    coord: ReviewWorkspaceSyncCoordinator = env["coordinator"]

    doc = editor_ctrl._get_document()
    navigated_positions = []
    editor_ctrl.requestNavigateToPosition.connect(navigated_positions.append)

    # Node 0: Main heading
    coord.report_node_clicked(0)
    assert navigated_positions[-1] == 0

    # Node 1: Persian paragraph
    p1_pos = doc.findBlockByLineNumber(2).position()
    coord.report_node_clicked(1)
    assert navigated_positions[-1] == p1_pos

    # Node 2: Section two heading
    sec2_pos = doc.findBlockByLineNumber(4).position()
    coord.report_node_clicked(2)
    assert navigated_positions[-1] == sec2_pos

    # Move cursor inside Persian paragraph
    editor_ctrl.updateCursorPosition(p1_pos + 10)
    assert viewer_ctrl.selectedNodeIndex == 1

    # Move cursor into list item
    list_line_pos = doc.findBlockByLineNumber(7).position()
    editor_ctrl.updateCursorPosition(list_line_pos + 2)
    assert viewer_ctrl.selectedNodeIndex == 3


# ===========================================================================
# 3. Supplementary-Plane Unicode (Emojis & Surrogate Pairs)
# ===========================================================================

def test_sync_emojis_and_surrogate_pairs(sync_harness):
    """Validates that surrogate pairs and ZWJ emoji sequences do not cause coordinate drift."""
    text = (
        "# Emojis Test\n\n"
        "Celebration 🎉 party popper!\n\n"
        "Family unit 👨‍👩‍👧‍👦 together.\n"
    )
    env = sync_harness(text)
    editor_ctrl: MarkdownEditorController = env["editor_ctrl"]
    viewer_ctrl: MarkdownViewerController = env["viewer_ctrl"]
    coord: ReviewWorkspaceSyncCoordinator = env["coordinator"]

    doc = editor_ctrl._get_document()
    navigated_positions = []
    editor_ctrl.requestNavigateToPosition.connect(navigated_positions.append)

    # Node 0: Heading
    coord.report_node_clicked(0)
    assert navigated_positions[-1] == 0

    # Node 1: Celebration paragraph
    p1_pos = doc.findBlockByLineNumber(2).position()
    coord.report_node_clicked(1)
    assert navigated_positions[-1] == p1_pos

    # Node 2: Family paragraph
    p2_pos = doc.findBlockByLineNumber(4).position()
    coord.report_node_clicked(2)
    assert navigated_positions[-1] == p2_pos

    # Editor cursor after 🎉 (pos in line 3)
    editor_ctrl.updateCursorPosition(p1_pos + 16)
    assert viewer_ctrl.selectedNodeIndex == 1

    # Editor cursor inside family paragraph (pos in line 5)
    editor_ctrl.updateCursorPosition(p2_pos + 4)
    assert viewer_ctrl.selectedNodeIndex == 2


# ===========================================================================
# 4. Legacy Visual Crop Tokens (![[...]]) with Reverse Mapping
# ===========================================================================

def test_sync_legacy_crop_tokens_reverse_mapping(sync_harness):
    """Validates zero cursor drift when legacy ![[...]] syntax is normalized and reverse-mapped."""
    text = (
        "# Document With Crops\n\n"
        "Here is an inline reference to ![[diagram_alpha.png]] in text.\n\n"
        "Following paragraph after image.\n"
    )
    env = sync_harness(text)
    editor_ctrl: MarkdownEditorController = env["editor_ctrl"]
    viewer_ctrl: MarkdownViewerController = env["viewer_ctrl"]
    coord: ReviewWorkspaceSyncCoordinator = env["coordinator"]

    doc = editor_ctrl._get_document()
    navigated_positions = []
    editor_ctrl.requestNavigateToPosition.connect(navigated_positions.append)

    # Node 0: Title
    coord.report_node_clicked(0)
    assert navigated_positions[-1] == 0

    # Node 1: Paragraph containing legacy token
    p1_pos = doc.findBlockByLineNumber(2).position()
    coord.report_node_clicked(1)
    assert navigated_positions[-1] == p1_pos

    # Node 2: Paragraph following image
    p2_pos = doc.findBlockByLineNumber(4).position()
    coord.report_node_clicked(2)
    assert navigated_positions[-1] == p2_pos

    # Position cursor right inside the legacy token ![[...]]
    crop_pos = text.index("diagram_alpha")
    editor_ctrl.updateCursorPosition(crop_pos)
    assert viewer_ctrl.selectedNodeIndex == 1


# ===========================================================================
# 5. Math Equations (Display and Inline)
# ===========================================================================

def test_sync_math_equations(sync_harness):
    """Validates bidirectional synchronization on display and inline LaTeX math equations."""
    text = (
        "# Mathematics Section\n\n"
        "Consider Einstein's mass-energy equation $E = mc^2$ in physics.\n\n"
        "$$\n"
        r"\int_{-\infty}^{+\infty} e^{-x^2} dx = \sqrt{\pi}" "\n"
        "$$\n\n"
        "Conclusion paragraph after formula.\n"
    )
    env = sync_harness(text)
    editor_ctrl: MarkdownEditorController = env["editor_ctrl"]
    viewer_ctrl: MarkdownViewerController = env["viewer_ctrl"]
    coord: ReviewWorkspaceSyncCoordinator = env["coordinator"]

    doc = editor_ctrl._get_document()
    navigated_positions = []
    editor_ctrl.requestNavigateToPosition.connect(navigated_positions.append)

    # Click display math block (Node 2)
    math_pos = doc.findBlockByLineNumber(4).position()
    coord.report_node_clicked(2)
    assert navigated_positions[-1] == math_pos

    # Click conclusion paragraph (Node 3)
    conclusion_pos = doc.findBlockByLineNumber(8).position()
    coord.report_node_clicked(3)
    assert navigated_positions[-1] == conclusion_pos

    # Move cursor inside display math body
    editor_ctrl.updateCursorPosition(math_pos + 3)
    assert viewer_ctrl.selectedNodeIndex == 2

    # Move cursor inside conclusion paragraph
    editor_ctrl.updateCursorPosition(conclusion_pos + 4)
    assert viewer_ctrl.selectedNodeIndex == 3


# ===========================================================================
# 6. Literal Tabs and CRLF Newlines
# ===========================================================================

def test_sync_literal_tabs_and_crlf_newlines(sync_harness):
    """Validates that literal tabs and CRLF line breaks map accurately without coordinate drift."""
    text = (
        "# Title\r\n"
        "\r\n"
        "\tIndented line with a tab\r\n"
        "\r\n"
        "Regular line without tabs\r\n"
    )
    env = sync_harness(text)
    editor_ctrl: MarkdownEditorController = env["editor_ctrl"]
    viewer_ctrl: MarkdownViewerController = env["viewer_ctrl"]
    coord: ReviewWorkspaceSyncCoordinator = env["coordinator"]

    doc = editor_ctrl._get_document()
    navigated_positions = []
    editor_ctrl.requestNavigateToPosition.connect(navigated_positions.append)

    # Node 0: Title
    coord.report_node_clicked(0)
    assert navigated_positions[-1] == 0

    # Node 1: Tab-indented line
    # Pandoc advances column over tab; caret offset translator translates back to UTF-16 code units
    tab_line_pos = doc.findBlockByLineNumber(2).position()
    coord.report_node_clicked(1)
    # The caret lands within line 3 (either at col 1 or col 5 translated -> offset 1)
    assert navigated_positions[-1] in (tab_line_pos, tab_line_pos + 1)

    # Node 2: Regular line
    reg_line_pos = doc.findBlockByLineNumber(4).position()
    coord.report_node_clicked(2)
    assert navigated_positions[-1] == reg_line_pos

    # Cursor movement inside tab line
    editor_ctrl.updateCursorPosition(tab_line_pos + 3)
    assert viewer_ctrl.selectedNodeIndex == 1

    # Cursor movement inside regular line
    editor_ctrl.updateCursorPosition(reg_line_pos + 5)
    assert viewer_ctrl.selectedNodeIndex == 2
