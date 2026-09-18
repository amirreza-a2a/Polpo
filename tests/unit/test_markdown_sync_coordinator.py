# ============================================================
#  tests/unit/test_markdown_sync_coordinator.py
#  Unit Tests for Source ↔ Preview Synchronized Cursor & Scroll
# ============================================================

import pytest
from core.markdown.ast import (
    BlockType,
    CodeBlock,
    HeadingBlock,
    MarkdownDocument,
    ParagraphBlock,
    ThematicBreakBlock,
)
from infrastructure.markdown.markdown_it_parser import MarkdownItParser
from application.services.markdown_viewer_service import MarkdownViewerService
from interfaces.desktop.models.markdown_document_model import MarkdownDocumentModel
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController


class TestTask1ASTLineMappingAndDTOPropagation:
    """
    Tests for Task 1: Source line provenance in Core AST, parser token map extraction,
    and presentation DTO propagation.
    """

    def test_t_sync_01_heading_and_paragraph_line_mapping(self):
        parser = MarkdownItParser()
        text = "# Heading 1\n\nParagraph line 1\nParagraph line 2\n"
        doc = parser.parse(text)

        assert len(doc.blocks) == 2
        h1 = doc.blocks[0]
        assert isinstance(h1, HeadingBlock)
        assert h1.source_start_line == 1
        assert h1.source_end_line == 1

        p1 = doc.blocks[1]
        assert isinstance(p1, ParagraphBlock)
        assert p1.source_start_line == 3
        assert p1.source_end_line == 4

    def test_t_sync_02_fenced_code_block_line_mapping(self):
        parser = MarkdownItParser()
        text = "```python\ndef foo():\n    return 42\n```\n"
        doc = parser.parse(text)

        assert len(doc.blocks) == 1
        cb = doc.blocks[0]
        assert isinstance(cb, CodeBlock)
        assert cb.source_start_line == 1
        assert cb.source_end_line == 4

    def test_t_sync_03_empty_document_line_mapping(self):
        parser = MarkdownItParser()
        doc = parser.parse("")
        assert doc.blocks == ()

    def test_t_sync_21_unclosed_code_fence_line_mapping(self):
        parser = MarkdownItParser()
        text = "```python\ndef foo():\n"
        doc = parser.parse(text)

        assert len(doc.blocks) == 1
        cb = doc.blocks[0]
        assert isinstance(cb, CodeBlock)
        assert cb.source_start_line == 1
        assert cb.source_end_line == 2

    def test_dto_line_propagation_in_viewer_service(self):
        parser = MarkdownItParser()
        service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)
        text = "# Title\n\nSome text.\n"
        dto = service.render_text(raw_text=text, active_regions=(), job_id=1, version=1)

        assert len(dto.nodes) == 2
        assert dto.nodes[0].source_start_line == 1
        assert dto.nodes[0].source_end_line == 1
        assert dto.nodes[1].source_start_line == 3
        assert dto.nodes[1].source_end_line == 3


class TestTask2PresentationModelLineMappingAndGeneration:
    """
    Tests for Task 2: Presentation model source line roles, nodeIndexAtLine(),
    lineAtNodeIndex(), and model_generation snapshot identity.
    """

    def test_t_sync_04_empty_model_returns_negative_one(self):
        model = MarkdownDocumentModel()
        assert model.rowCount() == 0
        assert model.nodeIndexAtLine(1) == -1
        assert model.lineAtNodeIndex(0) == 1

    def test_t_sync_05_leading_blank_lines(self):
        parser = MarkdownItParser()
        service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)
        # Leading blank lines: H1 starts at line 3
        text = "\n\n# Heading at line 3\n\nParagraph at line 5\n"
        dto = service.render_text(raw_text=text, active_regions=(), job_id=1, version=1)

        model = MarkdownDocumentModel()
        model.set_document(dto)

        # Lines 1 and 2 are before the first block -> clamp to block 0
        assert model.nodeIndexAtLine(1) == 0
        assert model.nodeIndexAtLine(2) == 0
        # Line 3 is inside block 0
        assert model.nodeIndexAtLine(3) == 0

    def test_t_sync_06_blank_lines_between_blocks(self):
        parser = MarkdownItParser()
        service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)
        # Block 0 on line 1, Block 1 on line 4
        text = "# H1\n\n\nParagraph on line 4\n"
        dto = service.render_text(raw_text=text, active_regions=(), job_id=1, version=1)

        model = MarkdownDocumentModel()
        model.set_document(dto)

        # Line 2 and 3 are blank between block 0 and block 1 -> map to preceding block 0
        assert model.nodeIndexAtLine(2) == 0
        assert model.nodeIndexAtLine(3) == 0
        # Line 4 is block 1
        assert model.nodeIndexAtLine(4) == 1

    def test_t_sync_07_trailing_blank_lines_and_eof(self):
        parser = MarkdownItParser()
        service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)
        text = "# H1\n\nParagraph\n\n\n\n"
        dto = service.render_text(raw_text=text, active_regions=(), job_id=1, version=1)

        model = MarkdownDocumentModel()
        model.set_document(dto)

        # Far past end of document -> clamps to last block (block 1)
        assert model.nodeIndexAtLine(100) == 1

    def test_t_sync_08_line_at_node_index(self):
        parser = MarkdownItParser()
        service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)
        text = "# H1\n\nParagraph on line 3\n"
        dto = service.render_text(raw_text=text, active_regions=(), job_id=1, version=1)

        model = MarkdownDocumentModel()
        model.set_document(dto)

        assert model.lineAtNodeIndex(0) == 1
        assert model.lineAtNodeIndex(1) == 3
        # Out-of-bounds fallback
        assert model.lineAtNodeIndex(99) == 1

    def test_t_sync_09_model_generation_increments_monotonically(self):
        parser = MarkdownItParser()
        service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)
        dto1 = service.render_text(raw_text="# Doc 1", active_regions=(), job_id=1, version=1)
        dto2 = service.render_text(raw_text="# Doc 2", active_regions=(), job_id=1, version=1)

        model = MarkdownDocumentModel()
        assert model.model_generation == 0
        assert model.modelGeneration() == 0

        model.set_document(dto1)
        gen1 = model.model_generation
        assert gen1 == 1

        # Even if content is identical or structural, new installation increments generation
        model.apply_transient_preview(dto2)
        gen2 = model.model_generation
        assert gen2 == 2

        model.reconcile_document(dto1)
        gen3 = model.model_generation
        assert gen3 == 3

        model.clear()
        gen4 = model.model_generation
        assert gen4 == 4


class TestTask3EditorAndViewerPresentationAPIs:
    """
    Tests for Task 3: Editor controller viewport vs caret signals and line-to-position mapping,
    and Viewer controller scroll/click reporting slots.
    """

    def test_t_sync_10_character_position_of_line(self):
        editor = MarkdownEditorController(editor_service=None)
        text = "Line 1\nLine 2 is longer\n\nLine 4\n"
        editor.set_source_text(text)

        assert editor.characterPositionOfLine(0) == 0
        assert editor.characterPositionOfLine(1) == 0
        # Line 2 starts after "Line 1\n" (7 chars)
        assert editor.characterPositionOfLine(2) == 7
        # Line 3 is the empty line after "Line 2 is longer\n" (7 + 17 = 24 chars)
        assert editor.characterPositionOfLine(3) == 24
        # Line 4 starts after "\n" (25 chars)
        assert editor.characterPositionOfLine(4) == 25
        # Out of bounds clamps to document length
        assert editor.characterPositionOfLine(100) == len(text)

    def test_t_sync_11_scroll_viewport_to_line_emits_request_scroll(self):
        editor = MarkdownEditorController(editor_service=None)
        editor.set_source_text("First line\nSecond line\n")

        received_positions = []
        editor.requestScrollToPosition.connect(received_positions.append)

        editor.scrollViewportToLine(2)
        assert received_positions == [11]  # "First line\n" is 11 chars

    def test_t_sync_12_navigate_to_line_emits_request_navigate(self):
        editor = MarkdownEditorController(editor_service=None)
        editor.set_source_text("First line\nSecond line\n")

        received_positions = []
        editor.requestNavigateToPosition.connect(received_positions.append)

        editor.navigateToLine(2)
        assert received_positions == [11]

    def test_t_sync_13_report_user_scrolled_emits_signal(self):
        viewer = MarkdownViewerController(viewer_service=None)
        received = []
        viewer.userScrolledNode.connect(lambda idx, gen: received.append((idx, gen)))

        viewer.reportUserScrolled(3, 7)
        assert received == [(3, 7)]

    def test_t_sync_14_report_node_clicked_emits_signal_and_sets_selected_index(self):
        parser = MarkdownItParser()
        service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)
        dto = service.render_text(raw_text="# Title\n\nParagraph\n", active_regions=(), job_id=1, version=1)

        viewer = MarkdownViewerController(viewer_service=service)
        viewer.model.set_document(dto)

        received = []
        viewer.nodeClicked.connect(lambda idx, gen: received.append((idx, gen)))

        viewer.reportNodeClicked(1, viewer.model.model_generation)
        assert received == [(1, viewer.model.model_generation)]
        assert viewer.selectedNodeIndex == 1


class TestTask4ReviewWorkspaceSyncCoordinator:
    """
    Tests for Task 4: ReviewWorkspaceSyncCoordinator bidirectional synchronization,
    viewport vs caret separation, snapshot validation, and directional lock.
    """

    def _setup_env(self, text: str = "# Title\n\nParagraph 1\n\nParagraph 2\n"):
        editor = MarkdownEditorController(editor_service=None)
        editor.set_source_text(text)

        parser = MarkdownItParser()
        viewer_service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)
        dto = viewer_service.render_text(raw_text=text, active_regions=(), job_id=1, version=1)

        viewer = MarkdownViewerController(viewer_service=viewer_service)
        viewer.model.set_document(dto)

        from interfaces.desktop.coordinators.review_workspace_sync_coordinator import (
            ReviewWorkspaceSyncCoordinator,
            SyncOrigin,
        )

        coord = ReviewWorkspaceSyncCoordinator(
            editor_controller=editor,
            viewer_controller=viewer,
            debounce_source_ms=0,
            debounce_preview_ms=0,
            lock_release_ms=100,
        )
        return editor, viewer, coord, SyncOrigin

    def test_t_sync_15_source_cursor_syncs_to_preview_scroll(self):
        editor, viewer, coord, _ = self._setup_env()
        coord.setDualPaneActive(True)

        scroll_nodes = []
        viewer.requestScrollToNode.connect(scroll_nodes.append)

        # Move editor cursor to Paragraph 2 (starts at line 5)
        pos = editor.characterPositionOfLine(5)
        editor.updateCursorPosition(pos)
        assert editor.cursorLine == 5

        assert scroll_nodes == [2]
        assert viewer.selectedNodeIndex == 2

    def test_t_sync_16_preview_user_scroll_syncs_source_viewport_only(self):
        editor, viewer, coord, _ = self._setup_env()
        coord.setDualPaneActive(True)

        viewport_scrolls = []
        nav_positions = []
        editor.requestScrollToPosition.connect(viewport_scrolls.append)
        editor.requestNavigateToPosition.connect(nav_positions.append)

        # User scrolls to node 1 (Paragraph 1 at line 3)
        gen = viewer.model.model_generation
        viewer.reportUserScrolled(1, gen)

        expected_pos = editor.characterPositionOfLine(3)
        assert viewport_scrolls == [expected_pos]
        # Crucial invariant: Caret / navigation must NOT be triggered on user scroll
        assert nav_positions == []

    def test_t_sync_17_preview_node_click_navigates_source_caret(self):
        editor, viewer, coord, _ = self._setup_env()
        coord.setDualPaneActive(True)

        viewport_scrolls = []
        nav_positions = []
        editor.requestScrollToPosition.connect(viewport_scrolls.append)
        editor.requestNavigateToPosition.connect(nav_positions.append)

        # User explicitly clicks node 2 (Paragraph 2 at line 5)
        gen = viewer.model.model_generation
        viewer.reportNodeClicked(2, gen)

        expected_pos = editor.characterPositionOfLine(5)
        assert nav_positions == [expected_pos]

    def test_t_sync_18_stale_model_generation_dropped(self):
        editor, viewer, coord, _ = self._setup_env()
        coord.setDualPaneActive(True)

        viewport_scrolls = []
        nav_positions = []
        editor.requestScrollToPosition.connect(viewport_scrolls.append)
        editor.requestNavigateToPosition.connect(nav_positions.append)

        # Send event with stale generation (e.g. 999 != 1)
        viewer.reportUserScrolled(1, 999)
        viewer.reportNodeClicked(1, 999)

        assert viewport_scrolls == []
        assert nav_positions == []

    def test_t_sync_19_directional_feedback_suppression(self):
        editor, viewer, coord, SyncOrigin = self._setup_env()
        coord.setDualPaneActive(True)

        # 1. Source initiates sync -> origin becomes SOURCE_USER
        pos = editor.characterPositionOfLine(3)
        editor.updateCursorPosition(pos)

        assert coord.sync_origin == SyncOrigin.SOURCE_USER

        # While SOURCE is active, incoming preview scroll reporting is dropped
        viewport_scrolls = []
        editor.requestScrollToPosition.connect(viewport_scrolls.append)
        viewer.reportUserScrolled(2, viewer.model.model_generation)
        assert viewport_scrolls == []

    def test_t_sync_20_model_reconciliation_reanchors_preview(self):
        editor, viewer, coord, _ = self._setup_env()
        coord.setDualPaneActive(True)

        # Position cursor on line 5 (node 2)
        editor.updateCursorPosition(editor.characterPositionOfLine(5))
        assert viewer.selectedNodeIndex == 2

        # Deselect to test re-anchoring
        viewer.setSelectedNodeIndex(0)

        scroll_nodes = []
        viewer.requestScrollToNode.connect(scroll_nodes.append)

        # Trigger model reconciliation with updated DTO
        parser = MarkdownItParser()
        service = MarkdownViewerService(parser=parser, uow_factory=None, storage=None)
        new_text = "# Title\n\nParagraph 1\n\nParagraph 2 updated\n"
        new_dto = service.render_text(raw_text=new_text, active_regions=(), job_id=1, version=1)

        viewer.model.reconcile_document(new_dto)

        # Coordinator should automatically re-anchor preview to node at cursor line 5
        assert viewer.selectedNodeIndex == 2
        assert 2 in scroll_nodes

    def test_sync_disabled_when_not_dual_pane(self):
        editor, viewer, coord, _ = self._setup_env()
        coord.setDualPaneActive(False)

        scroll_nodes = []
        viewport_scrolls = []
        viewer.requestScrollToNode.connect(scroll_nodes.append)
        editor.requestScrollToPosition.connect(viewport_scrolls.append)

        # Cursor change does not sync
        editor.updateCursorPosition(editor.characterPositionOfLine(5))
        assert scroll_nodes == []

        # Preview scroll does not sync
        viewer.reportUserScrolled(1, viewer.model.model_generation)
        assert viewport_scrolls == []
