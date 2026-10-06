# ============================================================
#  tests/unit/test_markdown_viewer_controller_quiescence.py
#  Unit Tests for MarkdownViewerController Quiescence & Degradation State Machine
# ============================================================

from unittest.mock import MagicMock, call
import pytest

from application.dto.markdown_dto import MarkdownDocumentDTO, MarkdownNodeDTO
from application.services.markdown_viewer_service import MarkdownViewerService
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


def _make_doc_dto(job_id: int = 42, version: int = 1, had_math_timeout: bool = False) -> MarkdownDocumentDTO:
    node = MarkdownNodeDTO(
        node_id="n1",
        node_type="paragraph",
        content="Test content",
    )
    return MarkdownDocumentDTO(
        job_id=job_id,
        version=version,
        nodes=(node,),
        region_to_occurrences={},
        had_math_timeout=had_math_timeout,
    )


class SignalSpy:
    """Helper to record Qt signal emissions."""
    def __init__(self, signal):
        self.count = 0
        self.emitted_args = []
        signal.connect(self._slot)

    def _slot(self, *args):
        self.count += 1
        self.emitted_args.append(args)


def test_controller_initial_math_degraded_state(qapp):
    """Initial state: isMathDegraded is False, consecutive timeouts is 0."""
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)

    assert ctrl.isMathDegraded is False
    assert ctrl.is_math_degraded() is False
    assert ctrl._consecutive_math_timeouts == 0
    assert ctrl._math_quiescence_timer.isSingleShot() is True
    assert ctrl._math_quiescence_timer.interval() == 2000
    assert ctrl._math_quiescence_timer.isActive() is False


def test_controller_enters_degraded_mode_after_two_timeouts(qapp):
    """
    Simulates two preview completions where document_dto.had_math_timeout is True.
    Asserts degraded mode is entered exactly on the second timeout and signal is emitted once.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42
    ctrl._draft_revision = 1

    spy = SignalSpy(ctrl.isMathDegradedChanged)

    # First timeout: count becomes 1, not degraded
    dto_timeout_1 = _make_doc_dto(job_id=42, had_math_timeout=True)
    ctrl._on_internal_preview_loaded(42, 1, dto_timeout_1)

    assert ctrl._consecutive_math_timeouts == 1
    assert ctrl.isMathDegraded is False
    assert spy.count == 0

    # Advance revision for next preview
    ctrl._draft_revision = 2
    dto_timeout_2 = _make_doc_dto(job_id=42, had_math_timeout=True)
    ctrl._on_internal_preview_loaded(42, 2, dto_timeout_2)

    assert ctrl._consecutive_math_timeouts == 2
    assert ctrl.isMathDegraded is True
    assert spy.count == 1


def test_controller_repeated_timeouts_while_degraded_do_not_reemit(qapp):
    """
    While in degraded mode, subsequent timeouts increment the counter but do not
    repeatedly emit isMathDegradedChanged.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42

    spy = SignalSpy(ctrl.isMathDegradedChanged)

    # Trigger degraded mode (2 timeouts)
    ctrl._draft_revision = 1
    ctrl._on_internal_preview_loaded(42, 1, _make_doc_dto(job_id=42, had_math_timeout=True))
    ctrl._draft_revision = 2
    ctrl._on_internal_preview_loaded(42, 2, _make_doc_dto(job_id=42, had_math_timeout=True))
    assert ctrl.isMathDegraded is True
    assert spy.count == 1

    # Third timeout
    ctrl._draft_revision = 3
    ctrl._on_internal_preview_loaded(42, 3, _make_doc_dto(job_id=42, had_math_timeout=True))
    assert ctrl._consecutive_math_timeouts == 3
    assert ctrl.isMathDegraded is True
    assert spy.count == 1  # Still 1, no duplicate emission


def test_controller_zero_timeout_resets_counter_and_exits_degraded(qapp):
    """
    When degraded, a clean preview (had_math_timeout=False) immediately resets the counter
    to 0, exits degraded mode, stops the quiescence timer, and emits isMathDegradedChanged.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42

    # Put into degraded mode
    ctrl._draft_revision = 1
    ctrl._on_internal_preview_loaded(42, 1, _make_doc_dto(job_id=42, had_math_timeout=True))
    ctrl._draft_revision = 2
    ctrl._on_internal_preview_loaded(42, 2, _make_doc_dto(job_id=42, had_math_timeout=True))
    assert ctrl.isMathDegraded is True

    # Start quiescence timer to verify it gets stopped
    ctrl._math_quiescence_timer.start(2000)
    assert ctrl._math_quiescence_timer.isActive() is True

    spy = SignalSpy(ctrl.isMathDegradedChanged)

    # Clean preview arrives
    ctrl._draft_revision = 3
    clean_dto = _make_doc_dto(job_id=42, had_math_timeout=False)
    ctrl._on_internal_preview_loaded(42, 3, clean_dto)

    assert ctrl._consecutive_math_timeouts == 0
    assert ctrl.isMathDegraded is False
    assert ctrl._math_quiescence_timer.isActive() is False
    assert spy.count == 1


def test_controller_clean_preview_while_normal_resets_single_timeout(qapp):
    """
    When in normal mode with 1 timeout, a clean preview resets the counter to 0
    without emitting isMathDegradedChanged.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42

    ctrl._draft_revision = 1
    ctrl._on_internal_preview_loaded(42, 1, _make_doc_dto(job_id=42, had_math_timeout=True))
    assert ctrl._consecutive_math_timeouts == 1

    spy = SignalSpy(ctrl.isMathDegradedChanged)

    ctrl._draft_revision = 2
    ctrl._on_internal_preview_loaded(42, 2, _make_doc_dto(job_id=42, had_math_timeout=False))
    assert ctrl._consecutive_math_timeouts == 0
    assert ctrl.isMathDegraded is False
    assert spy.count == 0


def test_controller_typing_resets_quiescence_timer(qapp):
    """
    Verifies that while degraded, every scheduleLivePreview call restarts
    the 2.0s quiescence timer.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42

    # Put into degraded mode
    ctrl._is_math_degraded = True
    assert ctrl._math_quiescence_timer.isActive() is False

    # First typing event
    ctrl.scheduleLivePreview(42, "formula typing 1", 1)
    assert ctrl._math_quiescence_timer.isActive() is True
    assert ctrl._math_quiescence_timer.interval() == 2000
    assert ctrl._live_preview_timer.isActive() is True

    # Second typing event resets/restarts the timer
    ctrl.scheduleLivePreview(42, "formula typing 2", 1)
    assert ctrl._math_quiescence_timer.isActive() is True
    assert ctrl._live_preview_timer.isActive() is True


def test_controller_normal_typing_does_not_start_quiescence_timer(qapp):
    """
    In normal mode, scheduleLivePreview starts the 250ms debounce timer,
    but does NOT start the 2.0s quiescence timer.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42

    assert ctrl.isMathDegraded is False
    ctrl.scheduleLivePreview(42, "normal typing", 1)

    assert ctrl._live_preview_timer.isActive() is True
    assert ctrl._math_quiescence_timer.isActive() is False


def test_controller_quiescence_timeout_triggers_full_rerender(qapp):
    """
    Verifies that when _on_math_quiescence_timeout fires:
    1. degraded mode is cleared (_is_math_degraded = False, timeouts = 0).
    2. isMathDegradedChanged is emitted.
    3. a full preview render is immediately dispatched with degraded_math=False.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    mock_service.render_preview.return_value = _make_doc_dto(job_id=42, had_math_timeout=False)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._executor.submit = lambda fn: fn()
    ctrl._active_job_id = 42

    # Put into degraded mode with pending draft
    ctrl._is_math_degraded = True
    ctrl._consecutive_math_timeouts = 3
    ctrl._has_active_draft = True
    ctrl._draft_revision = 5
    ctrl._pending_preview_job_id = 42
    ctrl._pending_preview_text = "$$complex equation$$"
    ctrl._pending_preview_base_version = 1

    spy = SignalSpy(ctrl.isMathDegradedChanged)

    # Fire quiescence timeout handler
    ctrl._on_math_quiescence_timeout()

    assert ctrl.isMathDegraded is False
    assert ctrl._consecutive_math_timeouts == 0
    assert spy.count == 1

    # Verify preview service was called with degraded_math=False
    mock_service.render_preview.assert_called_once_with(
        42,
        "$$complex equation$$",
        1,
        degraded_math=False,
    )
    ctrl.shutdown()


def test_controller_dispatch_pending_preview_passes_degraded_math_flag(qapp):
    """
    Verifies that _dispatch_pending_preview passes degraded_math=self._is_math_degraded.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    mock_service.render_preview.return_value = _make_doc_dto(job_id=42, had_math_timeout=False)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._executor.submit = lambda fn: fn()
    ctrl._active_job_id = 42
    ctrl._pending_preview_job_id = 42
    ctrl._pending_preview_text = "$$test$$"
    ctrl._pending_preview_base_version = 2
    ctrl._draft_revision = 1

    # Normal mode dispatch
    ctrl._is_math_degraded = False
    ctrl._dispatch_pending_preview()
    mock_service.render_preview.assert_called_with(
        42,
        "$$test$$",
        2,
        degraded_math=False,
    )

    mock_service.reset_mock()

    # Degraded mode dispatch
    ctrl._is_math_degraded = True
    ctrl._dispatch_pending_preview()
    mock_service.render_preview.assert_called_with(
        42,
        "$$test$$",
        2,
        degraded_math=True,
    )
    ctrl.shutdown()


def test_controller_stale_preview_result_does_not_mutate_degraded_state(qapp):
    """
    A preview result with mismatched draft_revision or job_id must be dropped
    and must not update consecutive timeout counters or degraded state.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42
    ctrl._draft_revision = 10

    spy = SignalSpy(ctrl.isMathDegradedChanged)

    # Stale draft revision with timeout
    ctrl._on_internal_preview_loaded(42, 9, _make_doc_dto(job_id=42, had_math_timeout=True))
    assert ctrl._consecutive_math_timeouts == 0
    assert ctrl.isMathDegraded is False

    # Stale job ID with timeout
    ctrl._on_internal_preview_loaded(99, 10, _make_doc_dto(job_id=99, had_math_timeout=True))
    assert ctrl._consecutive_math_timeouts == 0
    assert ctrl.isMathDegraded is False

    assert spy.count == 0


def test_controller_active_version_never_mutated_by_quiescence_or_preview(qapp):
    """
    Canonical version invariant: Neither preview loading nor quiescence recovery
    ever mutates activeVersion.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42
    ctrl._active_version = 3
    ctrl._has_active_draft = True
    ctrl._pending_preview_job_id = 42
    ctrl._pending_preview_text = "$$test$$"

    version_spy = SignalSpy(ctrl.activeVersionChanged)

    # Complete previews with timeouts
    ctrl._draft_revision = 1
    ctrl._on_internal_preview_loaded(42, 1, _make_doc_dto(job_id=42, version=5, had_math_timeout=True))
    ctrl._draft_revision = 2
    ctrl._on_internal_preview_loaded(42, 2, _make_doc_dto(job_id=42, version=5, had_math_timeout=True))

    assert ctrl._active_version == 3
    assert version_spy.count == 0

    # Quiescence timeout
    ctrl._on_math_quiescence_timeout()
    assert ctrl._active_version == 3
    assert version_spy.count == 0
    ctrl.shutdown()


def test_controller_copy_to_clipboard_slot(qapp):
    """
    Verifies that copyToClipboard slot sets the system clipboard text.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)

    raw_tex = r"\int_{-\infty}^{\infty} e^{-x^2} dx = \sqrt{\pi}"
    ctrl.copyToClipboard(raw_tex)

    assert QGuiApplication.clipboard().text() == raw_tex


def test_controller_memo_cleared_on_reload_and_save(qapp):
    """
    Verifies viewer_service.clear_math_negative_memo() is called on:
    - document switch (loadDocument with new job_id / openDocument)
    - hard reload (reload / reloadDocument)
    - successful save / active draft reset (resetActiveDraft)
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42

    # 1. Document switch via loadDocument
    ctrl.loadDocument(job_id=99)
    assert mock_service.clear_math_negative_memo.call_count == 1

    # 2. Hard reload via reload
    ctrl._active_job_id = 99
    ctrl.reload()
    assert mock_service.clear_math_negative_memo.call_count == 2

    # 3. Successful save / draft reset via resetActiveDraft
    ctrl.resetActiveDraft()
    assert mock_service.clear_math_negative_memo.call_count == 3
    ctrl.shutdown()


def test_controller_quiescence_followed_by_resumed_typing(qapp):
    """
    Verifies flow:
    DEGRADED -> quiescence timeout -> NORMAL -> typing resumed -> normal debounce scheduling.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    mock_service.render_preview.return_value = _make_doc_dto(job_id=42, had_math_timeout=False)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._executor.submit = lambda fn: fn()
    ctrl._active_job_id = 42

    # Degraded
    ctrl._is_math_degraded = True
    ctrl._consecutive_math_timeouts = 2
    ctrl._has_active_draft = True
    ctrl._draft_revision = 1
    ctrl._pending_preview_job_id = 42
    ctrl._pending_preview_text = "$$math$$"

    # Fire quiescence
    ctrl._on_math_quiescence_timeout()
    assert ctrl.isMathDegraded is False

    # Resume typing
    ctrl.scheduleLivePreview(42, "$$math$$ + 1", 1)
    assert ctrl.isMathDegraded is False
    assert ctrl._live_preview_timer.isActive() is True
    assert ctrl._math_quiescence_timer.isActive() is False

    ctrl.shutdown()


def test_controller_clear_and_shutdown_resets_degraded_state(qapp):
    """
    clear() and shutdown() stop the quiescence timer, reset degraded state,
    and clear the negative failure memo.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42
    ctrl._is_math_degraded = True
    ctrl._consecutive_math_timeouts = 2
    ctrl._math_quiescence_timer.start(2000)

    spy = SignalSpy(ctrl.isMathDegradedChanged)

    ctrl.clear()
    assert ctrl.isMathDegraded is False
    assert ctrl._consecutive_math_timeouts == 0
    assert ctrl._math_quiescence_timer.isActive() is False
    assert spy.count == 1

    # Shutdown
    ctrl._is_math_degraded = True
    ctrl._consecutive_math_timeouts = 2
    ctrl._math_quiescence_timer.start(2000)
    ctrl.shutdown()
    assert ctrl.is_math_degraded() is False
    assert ctrl._consecutive_math_timeouts == 0
    assert ctrl._math_quiescence_timer.isActive() is False


def test_controller_degraded_preview_does_not_exit_degraded_mode(qapp):
    """
    Test 1: Degraded preview (dispatched with degraded_math=True, returning had_math_timeout=False)
    must NEVER exit degraded mode, must not reset consecutive_timeouts, must keep the quiescence
    timer active, and must not emit isMathDegradedChanged.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42

    # Enter degraded mode via 2 consecutive timeouts
    ctrl._draft_revision = 1
    ctrl._on_internal_preview_loaded(42, 1, _make_doc_dto(job_id=42, had_math_timeout=True), is_degraded=False)
    ctrl._draft_revision = 2
    ctrl._on_internal_preview_loaded(42, 2, _make_doc_dto(job_id=42, had_math_timeout=True), is_degraded=False)

    assert ctrl.isMathDegraded is True
    assert ctrl._consecutive_math_timeouts == 2

    # Arm quiescence timer explicitly if not already armed
    if not ctrl._math_quiescence_timer.isActive():
        ctrl._math_quiescence_timer.start(2000)
    assert ctrl._math_quiescence_timer.isActive() is True

    spy = SignalSpy(ctrl.isMathDegradedChanged)

    # Degraded preview arrives with had_math_timeout=False
    ctrl._draft_revision = 3
    degraded_dto = _make_doc_dto(job_id=42, had_math_timeout=False)
    ctrl._on_internal_preview_loaded(42, 3, degraded_dto, is_degraded=True)

    # MUST REMAIN DEGRADED!
    assert ctrl.isMathDegraded is True
    assert ctrl._consecutive_math_timeouts == 2
    assert ctrl._math_quiescence_timer.isActive() is True
    assert spy.count == 0  # No exit signal emitted!


def test_controller_degraded_transition_arms_quiescence_timer(qapp):
    """
    Test 2: When the second timeout causes NORMAL -> DEGRADED transition,
    the 2.0s quiescence timer MUST be armed immediately inside _on_internal_preview_loaded(),
    even if user does not type another character.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42

    # First timeout
    ctrl._draft_revision = 1
    ctrl._on_internal_preview_loaded(42, 1, _make_doc_dto(job_id=42, had_math_timeout=True), is_degraded=False)
    assert ctrl.isMathDegraded is False
    assert ctrl._math_quiescence_timer.isActive() is False

    # Second timeout triggers DEGRADED
    ctrl._draft_revision = 2
    ctrl._on_internal_preview_loaded(42, 2, _make_doc_dto(job_id=42, had_math_timeout=True), is_degraded=False)

    assert ctrl.isMathDegraded is True
    assert ctrl._math_quiescence_timer.isActive() is True
    assert ctrl._math_quiescence_timer.interval() == 2000


def test_controller_full_clean_preview_exits_degraded_mode(qapp):
    """
    Test 3: When degraded, only a FULL preview (is_degraded=False) with had_math_timeout=False
    resets consecutive timeouts, exits degraded mode, stops the timer, and emits isMathDegradedChanged.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42

    # Put into degraded mode
    ctrl._draft_revision = 1
    ctrl._on_internal_preview_loaded(42, 1, _make_doc_dto(job_id=42, had_math_timeout=True), is_degraded=False)
    ctrl._draft_revision = 2
    ctrl._on_internal_preview_loaded(42, 2, _make_doc_dto(job_id=42, had_math_timeout=True), is_degraded=False)
    assert ctrl.isMathDegraded is True

    if not ctrl._math_quiescence_timer.isActive():
        ctrl._math_quiescence_timer.start(2000)

    spy = SignalSpy(ctrl.isMathDegradedChanged)

    # Full clean preview arrives
    ctrl._draft_revision = 3
    clean_full_dto = _make_doc_dto(job_id=42, had_math_timeout=False)
    ctrl._on_internal_preview_loaded(42, 3, clean_full_dto, is_degraded=False)

    assert ctrl.isMathDegraded is False
    assert ctrl._consecutive_math_timeouts == 0
    assert ctrl._math_quiescence_timer.isActive() is False
    assert spy.count == 1


def test_controller_provenance_preserved_across_async_preview_completion(qapp):
    """
    Test 4: Preview provenance is preserved across asynchronous execution.
    Preview A is dispatched with degraded_math=True (is_degraded=True).
    Controller state or timer changes before A completes.
    When A completes with had_math_timeout=False, it is evaluated as degraded,
    so it does NOT exit degraded mode or stop the timer.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42

    # Setup degraded mode
    ctrl._is_math_degraded = True
    ctrl._consecutive_math_timeouts = 2
    ctrl._math_quiescence_timer.start(2000)
    ctrl._draft_revision = 5
    ctrl._pending_preview_job_id = 42
    ctrl._pending_preview_text = "$$math$$"
    ctrl._pending_preview_base_version = 1

    # Intercept executor task so we can control when it runs
    captured_tasks = []
    ctrl._executor.submit = lambda fn: captured_tasks.append(fn)

    # Dispatch preview while in degraded mode
    ctrl._dispatch_pending_preview()
    assert len(captured_tasks) == 1

    mock_service.render_preview.return_value = _make_doc_dto(job_id=42, had_math_timeout=False)
    spy = SignalSpy(ctrl.isMathDegradedChanged)

    # Run the background task
    captured_tasks[0]()

    # Verify preview service was invoked with degraded_math=True
    mock_service.render_preview.assert_called_once_with(
        42, "$$math$$", 1, degraded_math=True
    )

    # Must remain degraded despite had_math_timeout=False because it was dispatched degraded
    assert ctrl.isMathDegraded is True
    assert ctrl._consecutive_math_timeouts == 2
    assert ctrl._math_quiescence_timer.isActive() is True
    assert spy.count == 0
    ctrl.shutdown()


def test_controller_document_switch_emits_degraded_state_change(qapp):
    """
    Test 5: When switching documents while in degraded mode, _is_math_degraded
    is reset to False and isMathDegradedChanged is emitted exactly once.
    Switching while already non-degraded must NOT emit the signal.
    """
    mock_service = MagicMock(spec=MarkdownViewerService)
    ctrl = MarkdownViewerController(viewer_service=mock_service)
    ctrl._active_job_id = 42
    ctrl._is_math_degraded = True
    ctrl._consecutive_math_timeouts = 2
    ctrl._math_quiescence_timer.start(2000)

    spy = SignalSpy(ctrl.isMathDegradedChanged)

    # Switch to new job 99
    ctrl.loadDocument(job_id=99)

    assert ctrl.isMathDegraded is False
    assert ctrl._consecutive_math_timeouts == 0
    assert ctrl._math_quiescence_timer.isActive() is False
    assert spy.count == 1

    # Switch again while already non-degraded -> must NOT emit again
    ctrl.loadDocument(job_id=100)
    assert spy.count == 1
    ctrl.shutdown()
