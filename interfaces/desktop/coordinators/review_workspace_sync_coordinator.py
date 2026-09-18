# ============================================================
#  interfaces/desktop/coordinators/review_workspace_sync_coordinator.py
#  Phase 10F.4 — Source ↔ Preview Synchronized Cursor & Scroll
# ============================================================

from enum import Enum, auto
from typing import Optional

from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.qt_compat import QObject, Property, Signal, Slot, QTimer


class SyncOrigin(Enum):
    """Origin of a navigation/scroll synchronization action."""
    IDLE = auto()
    SOURCE_USER = auto()
    SOURCE_SYNC = auto()
    PREVIEW_USER = auto()
    PREVIEW_SYNC = auto()


class ReviewWorkspaceSyncCoordinator(QObject):
    """
    Presentation coordinator managing synchronized navigation between the native
    Markdown source editor and the virtualized rendered Markdown preview.

    Key Architectural Invariants:
      1. Presentation-Only & Ephemeral: Coordinates transient UI viewports without mutating
         canonical versions, database state, or OCC revision tokens.
      2. Caret vs. Viewport Separation: Preview scrolling adjusts the editor viewport only
         (preserving caret position and active selection). Explicit preview block clicks
         navigate the editor caret.
      3. Directional Feedback Suppression: Directional lock prevents cyclic feedback loops
         between editor and preview.
      4. Snapshot Identity Validation: All preview-driven events validate model_generation
         against current presentation model to prevent stale layout jumps.
      5. Self-Healing Re-Anchoring: When model updates/reconciles during editing, the preview
         automatically re-anchors to the active editor cursor line.
    """

    dualPaneActiveChanged = Signal(bool)
    syncOriginChanged = Signal()

    def __init__(
        self,
        editor_controller: MarkdownEditorController,
        viewer_controller: MarkdownViewerController,
        debounce_source_ms: int = 40,
        debounce_preview_ms: int = 60,
        lock_release_ms: int = 100,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.editor_controller = editor_controller
        self.viewer_controller = viewer_controller

        self._is_dual_pane: bool = False
        self._sync_origin: SyncOrigin = SyncOrigin.IDLE
        self._is_shutdown: bool = False

        self._last_synced_line: int = -1
        self._last_synced_node_index: int = -1
        self._pending_preview_node: int = -1

        self._debounce_source_ms = debounce_source_ms
        self._debounce_preview_ms = debounce_preview_ms
        self._lock_release_ms = lock_release_ms

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
    # Source -> Preview Synchronization
    # -----------------------------------------------------------------------

    def _on_editor_cursor_metrics_changed(self) -> None:
        if not self._is_dual_pane or self._is_shutdown:
            return
        # If preview is driving, suppress feedback
        if self._sync_origin in (SyncOrigin.PREVIEW_USER, SyncOrigin.PREVIEW_SYNC):
            return

        line = self.editor_controller.cursorLine
        if line == self._last_synced_line:
            return

        if self._debounce_source_ms <= 0:
            self._on_source_scroll_timer_fired()
        else:
            self._source_scroll_timer.start(self._debounce_source_ms)

    def _on_source_scroll_timer_fired(self) -> None:
        if not self._is_dual_pane or self._is_shutdown:
            return
        if self._sync_origin in (SyncOrigin.PREVIEW_USER, SyncOrigin.PREVIEW_SYNC):
            return

        line = self.editor_controller.cursorLine
        self._set_sync_origin(SyncOrigin.SOURCE_USER)

        model = self.viewer_controller.model
        node_idx = model.nodeIndexAtLine(line)
        if node_idx >= 0 and node_idx != self.viewer_controller.selectedNodeIndex:
            self.viewer_controller.setSelectedNodeIndex(node_idx)
            self.viewer_controller.requestScrollToNode.emit(node_idx)
            self._last_synced_node_index = node_idx
            self._last_synced_line = line

        self._start_lock_release_timer()

    # -----------------------------------------------------------------------
    # Preview -> Source Viewport Synchronization (Caret Preserved)
    # -----------------------------------------------------------------------

    def _on_viewer_user_scrolled_node(self, node_index: int, model_generation: int) -> None:
        if not self._is_dual_pane or self._is_shutdown:
            return
        # Validate snapshot generation
        current_gen = self.viewer_controller.model.model_generation
        if model_generation != current_gen:
            return
        # If source is driving, suppress feedback
        if self._sync_origin in (SyncOrigin.SOURCE_USER, SyncOrigin.SOURCE_SYNC):
            return

        self._pending_preview_node = node_index

        if self._debounce_preview_ms <= 0:
            self._on_preview_scroll_timer_fired()
        else:
            self._preview_scroll_timer.start(self._debounce_preview_ms)

    def _on_preview_scroll_timer_fired(self) -> None:
        if not self._is_dual_pane or self._is_shutdown:
            return
        if self._sync_origin in (SyncOrigin.SOURCE_USER, SyncOrigin.SOURCE_SYNC):
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

    def _on_viewer_node_clicked(self, node_index: int, model_generation: int) -> None:
        if not self._is_dual_pane or self._is_shutdown:
            return
        # Validate snapshot generation
        current_gen = self.viewer_controller.model.model_generation
        if model_generation != current_gen:
            return
        if self._sync_origin in (SyncOrigin.SOURCE_USER, SyncOrigin.SOURCE_SYNC):
            return

        if self._preview_scroll_timer.isActive():
            self._preview_scroll_timer.stop()

        model = self.viewer_controller.model
        target_line = model.lineAtNodeIndex(node_index)
        if target_line <= 0:
            return

        self._set_sync_origin(SyncOrigin.PREVIEW_USER)
        # Explicit block click navigates editor caret and scrolls into view
        self.editor_controller.navigateToLine(target_line)
        self._last_synced_line = target_line
        self._last_synced_node_index = node_index

        self._start_lock_release_timer()

    # -----------------------------------------------------------------------
    # Self-Healing Re-Anchoring on Model Reconciled
    # -----------------------------------------------------------------------

    def _on_viewer_model_reconciled(self, generation: int) -> None:
        if not self._is_dual_pane or self._is_shutdown:
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

    def shutdown(self) -> None:
        self._is_shutdown = True
        self._stop_timers()
