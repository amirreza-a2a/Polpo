"""Unit tests for column-aware ReviewWorkspaceSyncCoordinator (TICK-011A).

Verifies:
- Exposing sourceStartCol and sourceEndCol roles on MarkdownDocumentModel.
- columnAtNodeIndex() and nodeIndexAtPosition().
- Node click navigation translating Pandoc column coordinates into exact Qt UTF-16 caret offsets.
- Multi-block line / column interval matching when editor cursor moves.
- Surrogate pair (emoji) and tab stop translation during node click navigation.
"""

from __future__ import annotations

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtGui import QGuiApplication

from application.dto.markdown_dto import MarkdownDocumentDTO, MarkdownNodeDTO
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
def sync_setup(qapp: QGuiApplication):
    model = MarkdownDocumentModel()
    viewer_ctrl = MarkdownViewerController(viewer_service=None)
    viewer_ctrl._model = model

    editor_ctrl = MarkdownEditorController(editor_service=None)

    coordinator = ReviewWorkspaceSyncCoordinator(
        editor_controller=editor_ctrl,
        viewer_controller=viewer_ctrl,
        debounce_source_ms=0,
        debounce_preview_ms=0,
    )
    coordinator.setDualPaneActive(True)

    return {
        "model": model,
        "viewer_ctrl": viewer_ctrl,
        "editor_ctrl": editor_ctrl,
        "coordinator": coordinator,
    }


def test_model_exposes_column_roles_and_methods(sync_setup):
    """MarkdownDocumentModel exposes sourceStartCol and sourceEndCol roles and helper methods."""
    model: MarkdownDocumentModel = sync_setup["model"]

    node1 = MarkdownNodeDTO(
        node_id="p_1",
        node_type="paragraph",
        content="Hello world",
        source_start_line=1,
        source_end_line=1,
        source_start_col=5,
        source_end_col=15,
    )
    dto = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node1,), region_to_occurrences={})
    model.set_document(dto)

    assert model.rowCount() == 1
    idx = model.index(0, 0)
    assert model.data(idx, model.SourceStartColRole) == 5
    assert model.data(idx, model.SourceEndColRole) == 15
    assert model.columnAtNodeIndex(0) == 5
    assert model.lineAtNodeIndex(0) == 1


def test_node_click_navigates_to_exact_column_offset(sync_setup):
    """Clicking a node translates Pandoc column to Qt UTF-16 offset and emits navigation signal."""
    model: MarkdownDocumentModel = sync_setup["model"]
    editor_ctrl: MarkdownEditorController = sync_setup["editor_ctrl"]
    coordinator: ReviewWorkspaceSyncCoordinator = sync_setup["coordinator"]

    # Setup editor text
    editor_ctrl.set_source_text("Line 1: ABCDEF\nLine 2: GHIJKLMNOP\nLine 3: QRSTUVWXYZ")

    # Node points to Line 2, column 5 (Pandoc 1-indexed -> 'K')
    node = MarkdownNodeDTO(
        node_id="h_1",
        node_type="heading",
        content="Heading text",
        source_start_line=2,
        source_end_line=2,
        source_start_col=5,  # 1-indexed column 5 -> offset 4 within line 2
        source_end_col=15,
    )
    dto = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
    model.set_document(dto)

    navigated_positions = []
    editor_ctrl.requestNavigateToPosition.connect(navigated_positions.append)

    # Click preview node 0
    coordinator.report_node_clicked(0)

    # Line 2 start position is 15 ("Line 1: ABCDEF\n" has 15 chars).
    # Column 5 has offset 4. Target position = 15 + 4 = 19.
    assert len(navigated_positions) == 1
    assert navigated_positions[0] == 19


def test_node_click_with_tabs_and_surrogate_pairs(sync_setup):
    """Caret navigation correctly accounts for literal tabs and supplementary Unicode emojis."""
    model: MarkdownDocumentModel = sync_setup["model"]
    editor_ctrl: MarkdownEditorController = sync_setup["editor_ctrl"]
    coordinator: ReviewWorkspaceSyncCoordinator = sync_setup["coordinator"]

    # Line contains tab (advances to col 5) and emoji (2 UTF-16 code units)
    # "\t" (col 1..4, 1 UTF-16), "🎉" (col 5, 2 UTF-16 units), "text" (col 6..)
    line_content = "\t🎉text"
    editor_ctrl.set_source_text(line_content)

    # Target column 6 (character 't' right after emoji)
    # Tab advances to col 5 (1 code unit)
    # Emoji is at col 5, takes 1 col, 2 UTF-16 units -> next offset is 1 + 2 = 3
    node = MarkdownNodeDTO(
        node_id="p_1",
        node_type="paragraph",
        content="Tab and emoji",
        source_start_line=1,
        source_end_line=1,
        source_start_col=6,
        source_end_col=10,
    )
    dto = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node,), region_to_occurrences={})
    model.set_document(dto)

    navigated_positions = []
    editor_ctrl.requestNavigateToPosition.connect(navigated_positions.append)

    coordinator.report_node_clicked(0)

    assert len(navigated_positions) == 1
    assert navigated_positions[0] == 3


def test_cursor_movement_matches_column_intervals(sync_setup):
    """Editor cursor position changes select node using line and column intervals."""
    model: MarkdownDocumentModel = sync_setup["model"]
    viewer_ctrl: MarkdownViewerController = sync_setup["viewer_ctrl"]
    editor_ctrl: MarkdownEditorController = sync_setup["editor_ctrl"]
    _coordinator: ReviewWorkspaceSyncCoordinator = sync_setup["coordinator"]

    # Two nodes on the same line (e.g. inline equation span vs surrounding text)
    node1 = MarkdownNodeDTO(
        node_id="n_1",
        node_type="paragraph",
        source_start_line=1,
        source_end_line=1,
        source_start_col=1,
        source_end_col=10,
    )
    node2 = MarkdownNodeDTO(
        node_id="n_2",
        node_type="math_block",
        source_start_line=1,
        source_end_line=1,
        source_start_col=11,
        source_end_col=20,
    )
    dto = MarkdownDocumentDTO(job_id=1, version=1, nodes=(node1, node2), region_to_occurrences={})
    model.set_document(dto)

    # Set document text in editor controller
    editor_ctrl.set_source_text("abcdefghijklmnopqrstuvwxyz\n")

    # Move cursor to pos 4 (line 1, col 5) -> should select node 0
    editor_ctrl.updateCursorPosition(4)
    assert viewer_ctrl.selectedNodeIndex == 0

    # Move cursor to pos 14 (line 1, col 15) -> should select node 1
    editor_ctrl.updateCursorPosition(14)
    assert viewer_ctrl.selectedNodeIndex == 1
