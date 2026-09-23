# ============================================================
#  interfaces/desktop/coordinators/review_workspace_sync_coordinator.py
#  Phase 10F.4 — Source ↔ Preview Synchronized Cursor & Scroll
# ============================================================

from enum import Enum, auto
from typing import Optional

from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.coordinators.caret_offset_translator import unicode_col_to_qt_utf16_offset
from interfaces.desktop.qt_compat import QObject, Property, Signal, Slot, QTimer


class SyncOrigin(Enum):
    """Origin of a navigation/scroll synchronization action."""
    IDLE = auto()
    SOURCE_USER = auto()
    PREVIEW_USER = auto()


class ReviewWorkspaceSyncCoordinator(QObject):
    """
    Presentation coordinator managing synchronized navigation between the native
    Markdown source editor and the virtualized rendered Markdown preview.

    Key Architectural Invariants:
      1. Presentation-Only & Ephemeral: Coordinates transient UI viewports without mutating
         canonical versions, database state, or OCC revision tokens.
      2. Continuous Viewport Tracking: Bidirectional proportional progress tracking provides
         fluid 60fps scrolling across wheel and scrollbar interactions.
      3. Caret vs. Viewport Separation: Preview scrolling adjusts the editor viewport only
         (preserving caret position and active selection). Explicit preview block clicks
         navigate the editor caret.
      4. Directional Feedback Suppression: Directional lock prevents cyclic feedback loops
         between editor and preview.
      5. Snapshot Identity Validation: All preview-driven events validate model_generation
         against current presentation model to prevent stale layout jumps.
      6. Self-Healing Re-Anchoring: When model updates/reconciles during editing, the preview
         automatically re-anchors to the active editor cursor line without disturbing active typing.
    """

    dualPaneActiveChanged = Signal(bool)
    syncOriginChanged = Signal()

    requestScrollPreviewToProgress = Signal(float)  # (progress: 0.0 .. 1.0)
    requestScrollSourceToProgress = Signal(float)   # (progress: 0.0 .. 1.0)

    def __init__(
        self,
        editor_controller: MarkdownEditorController,
        viewer_controller: MarkdownViewerController,
        debounce_source_ms: int = 40,
        debounce_preview_ms: int = 60,
        lock_release_ms: int = 100,
        throttle_ms: int = 16,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.editor_controller = editor_controller
        self.viewer_controller = viewer_controller

        self._is_dual_pane: bool = False
        self._sync_origin: SyncOrigin = SyncOrigin.IDLE
        self._is_shutdown: bool = False

        self._last_synced_line: int = -1
        self._last_synced_col: int = -1
        self._last_synced_node_index: int = -1
        self._pending_preview_node: int = -1

        self._debounce_source_ms = debounce_source_ms
        self._debounce_preview_ms = debounce_preview_ms
        self._lock_release_ms = lock_release_ms
        self._throttle_ms = throttle_ms

        self._pending_preview_progress: Optional[float] = None
        self._pending_source_progress: Optional[float] = None

        # Timers
        self._source_scroll_timer = QTimer(self)
        self._source_scroll_timer.setSingleShot(True)
        self._source_scroll_timer.timeout.connect(self._on_source_scroll_timer_fired)

        self._preview_scroll_timer = QTimer(self)
        self._preview_scroll_timer.setSingleShot(True)
        self._preview_scroll_timer.timeout.connect(self._on_preview_scroll_timer_fired)

        self._lock_release_timer = QTimer(self)
        self._lock_release_timer.setSingleShot(True)
        self._lock_release_timer.timeout.connect(self._on_lock_release_timer_fired)

        self._source_progress_timer = QTimer(self)
        self._source_progress_timer.setSingleShot(True)
        self._source_progress_timer.timeout.connect(self._on_source_progress_timer_fired)

        self._preview_progress_timer = QTimer(self)
        self._preview_progress_timer.setSingleShot(True)
        self._preview_progress_timer.timeout.connect(self._on_preview_progress_timer_fired)

        # Wire controller signals
        self.editor_controller.cursorMetricsChanged.connect(self._on_editor_cursor_metrics_changed)
        self.viewer_controller.userScrolledNode.connect(self._on_viewer_user_scrolled_node)
        self.viewer_controller.nodeClicked.connect(self._on_viewer_node_clicked)
        self.viewer_controller.modelReconciled.connect(self._on_viewer_model_reconciled)

    # -----------------------------------------------------------------------
    # Dual Pane Active State
    # -----------------------------------------------------------------------

    def is_dual_pane(self) -> bool:
        return self._is_dual_pane

    @Slot(bool)
    def setDualPaneActive(self, active: bool) -> None:
        if self._is_dual_pane != active:
            self._is_dual_pane = active
            if not active:
                self._stop_timers()
                self._set_sync_origin(SyncOrigin.IDLE)
            self.dualPaneActiveChanged.emit(self._is_dual_pane)

    set_dual_pane_active = setDualPaneActive
    isDualPane = Property(bool, is_dual_pane, setDualPaneActive, notify=dualPaneActiveChanged)

    @property
    def sync_origin(self) -> SyncOrigin:
        return self._sync_origin

    # -----------------------------------------------------------------------
    # Continuous Proportional Viewport Synchronization
    # -----------------------------------------------------------------------

    @Slot(float)
    def reportSourceScrollProgress(self, progress: float) -> None:
        """
        Reports continuous scroll progress (0.0 .. 1.0) from the Markdown source editor.
        Synchronously acquires SOURCE_USER lock and drives the rendered preview viewport.
        """
        if not self._is_dual_pane or self._is_shutdown:
            return
        if getattr(self.viewer_controller, "previewPaused", False):
            return
        if self._sync_origin == SyncOrigin.PREVIEW_USER:
            return

        self._set_sync_origin(SyncOrigin.SOURCE_USER)
        self._start_lock_release_timer()

        progress = max(0.0, min(1.0, float(progress)))

        if self._throttle_ms <= 0:
            self.requestScrollPreviewToProgress.emit(progress)
            return

        self._pending_preview_progress = progress
        if not self._source_progress_timer.isActive():
            # Leading edge: immediate emission on first frame for responsive movement
            self.requestScrollPreviewToProgress.emit(progress)
            self._pending_preview_progress = None
            self._source_progress_timer.start(self._throttle_ms)

    def _on_source_progress_timer_fired(self) -> None:
        if not self._is_dual_pane or self._is_shutdown:
            return
        if self._sync_origin == SyncOrigin.PREVIEW_USER:
            return
        if self._pending_preview_progress is not None:
            prog = self._pending_preview_progress
            self._pending_preview_progress = None
            self.requestScrollPreviewToProgress.emit(prog)
            self._source_progress_timer.start(self._throttle_ms)

    @Slot(float)
    def reportPreviewScrollProgress(self, progress: float) -> None:
        """
        Reports continuous scroll progress (0.0 .. 1.0) from the rendered preview pane.
        Synchronously acquires PREVIEW_USER lock and drives the source editor viewport.
        """
        if not self._is_dual_pane or self._is_shutdown:
            return
        if self._sync_origin == SyncOrigin.SOURCE_USER:
            return

        self._set_sync_origin(SyncOrigin.PREVIEW_USER)
        self._start_lock_release_timer()

        progress = max(0.0, min(1.0, float(progress)))

        if self._throttle_ms <= 0:
            self.requestScrollSourceToProgress.emit(progress)
            return

        self._pending_source_progress = progress
        if not self._preview_progress_timer.isActive():
            # Leading edge: immediate emission on first frame
            self.requestScrollSourceToProgress.emit(progress)
            self._pending_source_progress = None
            self._preview_progress_timer.start(self._throttle_ms)

    def _on_preview_progress_timer_fired(self) -> None:
        if not self._is_dual_pane or self._is_shutdown:
            return
        if self._sync_origin == SyncOrigin.SOURCE_USER:
            return
        if self._pending_source_progress is not None:
            prog = self._pending_source_progress
            self._pending_source_progress = None
            self.requestScrollSourceToProgress.emit(prog)
            self._preview_progress_timer.start(self._throttle_ms)

    # -----------------------------------------------------------------------
    # Source Cursor -> Preview Synchronization
    # -----------------------------------------------------------------------

    def _on_editor_cursor_metrics_changed(self) -> None:
        if not self._is_dual_pane or self._is_shutdown:
            return
        if getattr(self.viewer_controller, "previewPaused", False):
            return
        # If preview is driving, suppress feedback
        if self._sync_origin == SyncOrigin.PREVIEW_USER:
            return

        line = self.editor_controller.cursorLine
        col = self.editor_controller.cursorColumn
        if line == self._last_synced_line and col == self._last_synced_col:
            return

        # Immediate lock acquisition
        self._set_sync_origin(SyncOrigin.SOURCE_USER)
        self._start_lock_release_timer()

        if self._debounce_source_ms <= 0:
            self._on_source_scroll_timer_fired()
        else:
            self._source_scroll_timer.start(self._debounce_source_ms)

    def _on_source_scroll_timer_fired(self) -> None:
        if not self._is_dual_pane or self._is_shutdown:
            return
        if self._sync_origin == SyncOrigin.PREVIEW_USER:
            return

        line = self.editor_controller.cursorLine
        col = self.editor_controller.cursorColumn
        model = self.viewer_controller.model
        if hasattr(model, "nodeIndexAtPosition"):
            node_idx = model.nodeIndexAtPosition(line, col)
        else:
            node_idx = model.nodeIndexAtLine(line)

        if node_idx >= 0 and node_idx != self.viewer_controller.selectedNodeIndex:
            self.viewer_controller.setSelectedNodeIndex(node_idx)
            self.viewer_controller.requestScrollToNode.emit(node_idx)
            self._last_synced_node_index = node_idx
            self._last_synced_line = line
            self._last_synced_col = col

        self._start_lock_release_timer()

    # -----------------------------------------------------------------------
    # Preview -> Source Viewport Synchronization (Caret Preserved)
    # -----------------------------------------------------------------------

    def _on_viewer_user_scrolled_node(self, node_index: int, model_generation: int) -> None:
        if not self._is_dual_pane or self._is_shutdown:
            return
        # Validate snapshot generation
        current_gen = self.viewer_controller.modelGeneration()
        if model_generation != current_gen:
            return
        # If source is driving, suppress feedback
        if self._sync_origin == SyncOrigin.SOURCE_USER:
            return

        self._set_sync_origin(SyncOrigin.PREVIEW_USER)
        self._start_lock_release_timer()

        self._pending_preview_node = node_index

        if self._debounce_preview_ms <= 0:
            self._on_preview_scroll_timer_fired()
        else:
            self._preview_scroll_timer.start(self._debounce_preview_ms)

    def _on_preview_scroll_timer_fired(self) -> None:
        if not self._is_dual_pane or self._is_shutdown:
            return
        if self._sync_origin == SyncOrigin.SOURCE_USER:
            return

        node_index = self._pending_preview_node
        if node_index < 0:
            return

        model = self.viewer_controller.model
        target_line = model.lineAtNodeIndex(node_index)
        if target_line <= 0 or target_line == self._last_synced_line:
            return

        self._set_sync_origin(SyncOrigin.PREVIEW_USER)
        # Caret vs Viewport separation: Viewport ONLY
        self.editor_controller.scrollViewportToLine(target_line)
        self._last_synced_line = target_line
        self._last_synced_node_index = node_index

        self._start_lock_release_timer()

    # -----------------------------------------------------------------------
    # Preview Block Click -> Source Caret Navigation (Caret Moved)
    # -----------------------------------------------------------------------

    @Slot(int)
    @Slot(int, int)
    def report_node_clicked(self, node_index: int, model_generation: Optional[int] = None) -> None:
        """
        Handles preview node click, reading sourceStartLine and sourceStartCol
        and navigating the editor caret to the exact UTF-16 offset.
        """
        self._on_viewer_node_clicked(node_index, model_generation)

    reportNodeClicked = report_node_clicked

    def _on_viewer_node_clicked(self, node_index: int, model_generation: Optional[int] = None) -> None:
        if not self._is_dual_pane or self._is_shutdown:
            return
        # Validate snapshot generation if provided
        if model_generation is not None:
            current_gen = self.viewer_controller.modelGeneration()
            if model_generation != current_gen:
                return
        if self._sync_origin == SyncOrigin.SOURCE_USER:
            return

        if self._preview_scroll_timer.isActive():
            self._preview_scroll_timer.stop()

        model = self.viewer_controller.model
        target_line = model.lineAtNodeIndex(node_index)
        target_col = getattr(model, "columnAtNodeIndex", lambda idx: 1)(node_index)
        if target_line <= 0:
            return

        self._set_sync_origin(SyncOrigin.PREVIEW_USER)

        # Read line string from editor document and translate coordinates
        doc = self.editor_controller._get_document()
        block = doc.findBlockByLineNumber(target_line - 1)
        line_text = block.text() if block.isValid() else ""
        line_start_pos = block.position() if block.isValid() else 0

        utf16_offset = (
            unicode_col_to_qt_utf16_offset(line_text, target_col)
            if target_col > 1
            else 0
        )
        target_pos = line_start_pos + utf16_offset

        # Explicit block click navigates editor caret to line + column and scrolls into view
        self.editor_controller.requestNavigateToPosition.emit(target_pos)
        self._last_synced_line = target_line
        self._last_synced_col = target_col
        self._last_synced_node_index = node_index

        self._start_lock_release_timer()

    # -----------------------------------------------------------------------
    # Self-Healing Re-Anchoring on Model Reconciled
    # -----------------------------------------------------------------------

    def _on_viewer_model_reconciled(self, generation: int) -> None:
        if not self._is_dual_pane or self._is_shutdown:
            return

        # Suppress preview snapping if viewer has an active typing draft.
        # Background draft compilation must not disrupt cursor focus or view position.
        if self.viewer_controller.has_active_draft:
            return

        line = self.editor_controller.cursorLine
        model = self.viewer_controller.model
        node_idx = model.nodeIndexAtLine(line)
        if node_idx >= 0:
            self.viewer_controller.setSelectedNodeIndex(node_idx)
            self.viewer_controller.requestScrollToNode.emit(node_idx)
            self._last_synced_node_index = node_idx
            self._last_synced_line = line

    # -----------------------------------------------------------------------
    # Lock State Management & Lifecycle
    # -----------------------------------------------------------------------

    def _set_sync_origin(self, origin: SyncOrigin) -> None:
        if self._sync_origin != origin:
            self._sync_origin = origin
            self.syncOriginChanged.emit()

    def _start_lock_release_timer(self) -> None:
        if self._lock_release_ms <= 0:
            self._on_lock_release_timer_fired()
        else:
            self._lock_release_timer.start(self._lock_release_ms)

    def _on_lock_release_timer_fired(self) -> None:
        self._set_sync_origin(SyncOrigin.IDLE)

    def _stop_timers(self) -> None:
        if self._source_scroll_timer.isActive():
            self._source_scroll_timer.stop()
        if self._preview_scroll_timer.isActive():
            self._preview_scroll_timer.stop()
        if self._lock_release_timer.isActive():
            self._lock_release_timer.stop()
        if self._source_progress_timer.isActive():
            self._source_progress_timer.stop()
        if self._preview_progress_timer.isActive():
            self._preview_progress_timer.stop()
        self._pending_preview_progress = None
        self._pending_source_progress = None

    def shutdown(self) -> None:
        self._is_shutdown = True
        self._stop_timers()
