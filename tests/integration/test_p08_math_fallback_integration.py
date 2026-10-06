"""End-to-End Fallback Integration & Problem Register P08 Regression Suite."""

from pathlib import Path
from typing import Any, Callable, List, Optional
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtQml import QQmlComponent, QQmlEngine

from application.ports.math_renderer import (
    IMathRenderer,
    MathBufferLimitExceededError,
    MathCircuitBreakerOpenError,
    MathDegradedError,
    MathRenderError,
    MathRenderRequest,
    MathRenderTimeoutError,
    MathSyntaxError,
    MathWorkerCrashedError,
)
from application.services.markdown_viewer_service import MarkdownViewerService
from infrastructure.markdown.pandoc_parser import PandocParser
from infrastructure.math import (
    MathJaxClient,
    MathJaxProcessSupervisor,
    MathSvgCache,
)
from infrastructure.math.negative_memo import NegativeFailureMemo
from interfaces.desktop.controllers.markdown_viewer_controller import MarkdownViewerController
from interfaces.desktop.models.markdown_document_model import MarkdownDocumentModel
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    """Headless Qt application for presentation model and controller integration tests."""
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


class SignalSpy:
    """Records Qt signal emissions and argument payloads."""

    def __init__(self, signal):
        self.count = 0
        self.emitted_args: List[tuple] = []
        signal.connect(self._slot)

    def _slot(self, *args):
        self.count += 1
        self.emitted_args.append(args)


def wait_for_signal(signal: Any, timeout_ms: int = 3000) -> bool:
    """
    Pumps the Qt event loop until the specified signal fires or timeout_ms expires.
    Provides deterministic signal-driven synchronization across background worker threads.
    """
    loop = QEventLoop()
    fired = [False]

    def _on_signal(*args, **kwargs):
        fired[0] = True
        loop.quit()

    signal.connect(_on_signal)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)

    loop.exec()

    try:
        signal.disconnect(_on_signal)
    except (RuntimeError, TypeError):
        pass

    return fired[0]


class _FakeUnitOfWork:
    """Minimal UnitOfWork fake providing Job and VisualRegion repository lookups."""

    def __init__(self, job: Any):
        self.jobs = MagicMock()
        self.jobs.get_by_id.return_value = job
        self.visual_regions = MagicMock()
        self.visual_regions.get_by_job_id.return_value = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class _FakeUowFactory:
    """Factory creating UnitOfWork instances without deep mock chaining."""

    def __init__(self, job_id: int, output_path: str):
        self._job = MagicMock(id=job_id, output_path=output_path, active_markdown_version=1)

    def create(self):
        return _FakeUnitOfWork(self._job)


def test_mixed_document_isolates_partial_math_failures(qapp, tmp_path):
    """
    Scenario 1: End-to-end integration across PandocParser -> MarkdownViewerService ->
    MathJaxClient -> MarkdownDocumentDTO -> MarkdownDocumentModel.

    Verifies:
      - Valid display and inline equations render to SVG in cache and presentation model.
      - Broken display equation produces MathErrorCard state (hasError=True, syntax category).
      - Broken inline equation produces inline error segment with tooltip message and styled fallback.
      - MathJax client and supervisor remain responsive for subsequent renders.
      - MarkdownDocumentModel roles project error attributes accurately via public data() API.
    """
    mixed_doc_text = (
        "# Mixed Math Document\n\n"
        "Here is valid inline math $x + y = z$ and timeout inline math $\\int_0^\\infty f(x) dx$.\n\n"
        "$$\nE = mc^2\n$$\n\n"
        "$$\n\\begin{matrix} 1 & 2\n$$\n"
    )

    parser = PandocParser()
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.is_alive = True
    mock_supervisor.ping.return_value = True

    def fake_render(tex: str, display: bool = False, em: float = 16.0, ex: float = 8.0) -> dict:
        if tex == r"\begin{matrix} 1 & 2":
            raise MathSyntaxError(-32602, r"Missing \end{matrix}")
        elif tex == r"\int_0^\infty f(x) dx":
            raise MathRenderTimeoutError(-32001, "MathJax rendering timed out after 3.0s")
        elif tex == "E = mc^2":
            return {
                "svg_xml": "<svg class='math-display-emc2'><defs/><g>E=mc2</g></svg>",
                "width": "8ex",
                "height": "2ex",
                "vertical_align": "0ex",
            }
        elif tex == "x + y = z":
            return {
                "svg_xml": "<svg class='math-inline-xyz'><defs/><g>x+y=z</g></svg>",
                "width": "6ex",
                "height": "1.5ex",
                "vertical_align": "0ex",
            }
        elif tex == "subsequent_formula":
            return {
                "svg_xml": "<svg class='math-subsequent'><defs/><g>ok</g></svg>",
                "width": "4ex",
                "height": "1ex",
                "vertical_align": "0ex",
            }
        return {
            "svg_xml": f"<svg><g>{tex}</g></svg>",
            "width": "5ex",
            "height": "1ex",
            "vertical_align": "0ex",
        }

    mock_supervisor.render.side_effect = fake_render

    svg_cache = MathSvgCache(capacity=100)
    negative_memo = NegativeFailureMemo(capacity=100)
    math_client = MathJaxClient(
        supervisor=mock_supervisor,
        cache=svg_cache,
        negative_memo=negative_memo,
    )

    uow_factory = _FakeUowFactory(job_id=101, output_path=str(tmp_path / "job_101.md"))
    mock_storage = MagicMock()
    viewer_service = MarkdownViewerService(
        parser=parser,
        uow_factory=uow_factory,
        storage=mock_storage,
        math_renderer=math_client,
    )

    doc_dto = viewer_service.render_text(
        raw_text=mixed_doc_text,
        active_regions=[],
        job_id=101,
        version=1,
    )

    assert doc_dto is not None
    assert doc_dto.had_math_timeout is True

    valid_hash = MathRenderRequest(tex="E = mc^2", display=True).compute_hash()
    inline_valid_hash = MathRenderRequest(tex="x + y = z", display=False).compute_hash()
    timeout_hash = MathRenderRequest(tex=r"\int_0^\infty f(x) dx", display=False).compute_hash()

    assert svg_cache.get(valid_hash) is not None
    assert "math-display-emc2" in svg_cache.get(valid_hash).svg_xml
    assert svg_cache.get(inline_valid_hash) is not None
    assert negative_memo.get(timeout_hash) is not None

    model = MarkdownDocumentModel()
    model.set_document(doc_dto)

    # Paragraph at row 1 contains inline math segments
    para_idx = model.index(1, 0)
    segments = model.data(para_idx, MarkdownDocumentModel.SegmentsRole)
    assert segments is not None
    math_segments = [s for s in segments if s.get("segmentType") == "math"]
    assert len(math_segments) == 2

    valid_inline = math_segments[0]
    assert valid_inline["mathTex"] == "x + y = z"
    assert valid_inline["hasError"] is False
    assert valid_inline["errorCategory"] == ""
    assert valid_inline["mathHash"] == inline_valid_hash

    timeout_inline = math_segments[1]
    assert timeout_inline["mathTex"] == r"\int_0^\infty f(x) dx"
    assert timeout_inline["hasError"] is True
    assert timeout_inline["errorCategory"] == "timeout"
    assert "MathJax rendering timed out after 3.0s" in timeout_inline["errorMessage"]
    assert "color:#f87171" in timeout_inline["textHtml"]
    assert timeout_inline["mathHash"] == timeout_hash

    # Display MathBlock at row 2 (valid)
    valid_idx = model.index(2, 0)
    assert model.data(valid_idx, MarkdownDocumentModel.MathHasErrorRole) is False
    assert model.data(valid_idx, MarkdownDocumentModel.MathErrorCategoryRole) == ""
    assert model.data(valid_idx, MarkdownDocumentModel.MathErrorMessageRole) == ""
    assert model.data(valid_idx, MarkdownDocumentModel.MathTexRole) == "E = mc^2"
    assert model.data(valid_idx, MarkdownDocumentModel.MathHashRole) == valid_hash

    # Display MathBlock at row 3 (syntax error card contract)
    error_idx = model.index(3, 0)
    assert model.data(error_idx, MarkdownDocumentModel.MathHasErrorRole) is True
    assert model.data(error_idx, MarkdownDocumentModel.MathErrorCategoryRole) == "syntax"
    assert r"Missing \end{matrix}" in model.data(error_idx, MarkdownDocumentModel.MathErrorMessageRole)
    assert model.data(error_idx, MarkdownDocumentModel.MathTexRole) == r"\begin{matrix} 1 & 2"

    ctrl = MarkdownViewerController(viewer_service=viewer_service)
    ctrl.copyToClipboard(model.data(error_idx, MarkdownDocumentModel.MathTexRole))
    assert QGuiApplication.clipboard().text() == r"\begin{matrix} 1 & 2"
    ctrl.shutdown()

    subsequent_req = MathRenderRequest(tex="subsequent_formula", display=False)
    subsequent_res = math_client.render(subsequent_req)
    assert subsequent_res is not None
    assert "math-subsequent" in subsequent_res.svg_xml
    assert mock_supervisor.ping() is True


def test_live_typing_degradation_and_quiescence_restoration(qapp, tmp_path):
    """
    Scenario 2: State machine integration across MarkdownViewerController,
    MarkdownViewerService, MathJaxClient, and MarkdownDocumentModel.

    Verifies:
      - Real 250ms debounce preview timer configuration and timer-driven dispatch.
      - Real ThreadPoolExecutor background execution without synchronous monkey-patching.
      - Two consecutive previews with had_math_timeout=True triggers degraded mode (isMathDegraded=True).
      - Entering degraded mode immediately arms the 2.0s quiescence timer.
      - Previews dispatched with degraded_math=True project warm cache hits as SVG and
        cache misses as non-blocking degraded errors without worker RPC.
      - Degraded preview completions (had_math_timeout=False) DO NOT exit degraded mode.
      - 2.0s quiescence timeout restores normal mode (isMathDegraded=False) and triggers
        full non-degraded preview render restoring rich SVG.
      - Cooperative clean shutdown leaves zero leaked threads or active timers.
    """
    parser = PandocParser()
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.is_alive = True

    def fake_render(tex: str, display: bool = False, em: float = 16.0, ex: float = 8.0) -> dict:
        if "slow" in tex:
            raise MathRenderTimeoutError(-32001, "MathJax worker timed out")
        return {
            "svg_xml": f"<svg class='math-{tex}'><g>{tex}</g></svg>",
            "width": "4ex",
            "height": "1ex",
            "vertical_align": "0ex",
        }

    mock_supervisor.render.side_effect = fake_render

    svg_cache = MathSvgCache(capacity=100)
    negative_memo = NegativeFailureMemo(capacity=100)
    math_client = MathJaxClient(
        supervisor=mock_supervisor,
        cache=svg_cache,
        negative_memo=negative_memo,
    )

    uow_factory = _FakeUowFactory(job_id=42, output_path=str(tmp_path / "job_42.md"))
    mock_storage = MagicMock()
    viewer_service = MarkdownViewerService(
        parser=parser,
        uow_factory=uow_factory,
        storage=mock_storage,
        math_renderer=math_client,
    )

    ctrl = MarkdownViewerController(viewer_service=viewer_service)
    ctrl._active_job_id = 42

    spy_degraded = SignalSpy(ctrl.isMathDegradedChanged)

    warm_req = MathRenderRequest(tex="warm_eq", display=False)
    math_client.render(warm_req)
    assert svg_cache.get(warm_req.compute_hash()) is not None

    # First typing event: verifies real 250ms debounce timer triggers background dispatch
    text_typing_1 = "First draft typing with $slow_1$ equation."
    ctrl.scheduleLivePreview(42, text_typing_1, 1)
    assert ctrl._live_preview_timer.isActive() is True
    assert ctrl._live_preview_timer.interval() == 250
    assert ctrl._math_quiescence_timer.isActive() is False

    assert wait_for_signal(ctrl.documentChanged, timeout_ms=3000) is True
    assert ctrl._consecutive_math_timeouts == 1
    assert ctrl.isMathDegraded is False
    assert ctrl._math_quiescence_timer.isActive() is False
    assert spy_degraded.count == 0

    # Second typing event: flush immediately to exercise explicit flush path
    text_typing_2 = "Second draft typing with $slow_2$ equation."
    ctrl.scheduleLivePreview(42, text_typing_2, 1)
    ctrl.flushLivePreview()

    assert wait_for_signal(ctrl.isMathDegradedChanged, timeout_ms=3000) is True
    assert ctrl._consecutive_math_timeouts == 2
    assert ctrl.isMathDegraded is True
    assert spy_degraded.count == 1
    assert ctrl._math_quiescence_timer.isActive() is True
    assert ctrl._math_quiescence_timer.interval() == 2000

    # Ongoing typing while in degraded mode
    text_typing_3 = "Draft typing: warm $warm_eq$ and new $cold_eq$ formula."
    supervisor_calls_before = mock_supervisor.render.call_count

    ctrl.scheduleLivePreview(42, text_typing_3, 1)
    assert ctrl._math_quiescence_timer.isActive() is True
    ctrl.flushLivePreview()

    assert wait_for_signal(ctrl.documentChanged, timeout_ms=3000) is True

    # TICK-P08B contract: supervisor was NOT called for cold_eq cache miss
    assert mock_supervisor.render.call_count == supervisor_calls_before

    # Model projection check via public data() API
    segments = ctrl.model.data(ctrl.model.index(0, 0), MarkdownDocumentModel.SegmentsRole)
    assert segments is not None
    degraded_segs = {s["mathTex"]: s for s in segments if s.get("segmentType") == "math"}
    assert degraded_segs["warm_eq"]["hasError"] is False
    assert degraded_segs["cold_eq"]["hasError"] is True
    assert degraded_segs["cold_eq"]["errorCategory"] == "degraded"

    # TICK-P08D regression check: degraded preview completion must NOT clear degraded mode
    assert ctrl.isMathDegraded is True
    assert ctrl._math_quiescence_timer.isActive() is True
    assert spy_degraded.count == 1

    # Quiescence timeout triggers after 2.0s pause in typing via Qt event loop
    assert wait_for_signal(ctrl.isMathDegradedChanged, timeout_ms=3500) is True
    assert ctrl.isMathDegraded is False
    assert ctrl._consecutive_math_timeouts == 0
    assert spy_degraded.count == 2

    # Wait for the recovery preview render triggered by quiescence timeout
    assert wait_for_signal(ctrl.documentChanged, timeout_ms=3000) is True
    assert mock_supervisor.render.call_count > supervisor_calls_before

    # Formulas restored to rich SVG in presentation model
    recovered_segs = ctrl.model.data(ctrl.model.index(0, 0), MarkdownDocumentModel.SegmentsRole)
    for seg in recovered_segs:
        if seg.get("segmentType") == "math":
            assert seg["hasError"] is False
            assert seg["errorCategory"] == ""

    ctrl.shutdown()
    assert ctrl.is_shutdown is True
    assert ctrl._math_quiescence_timer.isActive() is False
    assert ctrl._live_preview_timer.isActive() is False


def test_math_failure_categories_and_color_mappings(qapp, tmp_path):
    """
    Scenario 3: Verification of all 7 structured MathRenderError failure categories
    across the entire stack, including presentation model roles and QML MathErrorCard
    semantic badge colors:
      1. MathSyntaxError -> 'syntax' (amber #9a3412)
      2. MathRenderTimeoutError -> 'timeout' (amber-brown #92400e)
      3. MathWorkerCrashedError -> 'crash' (dark red #991b1b)
      4. MathBufferLimitExceededError -> 'buffer_limit' (dark red #991b1b)
      5. MathCircuitBreakerOpenError -> 'circuit_breaker' (dark red #991b1b)
      6. MathDegradedError -> 'degraded' (amber-brown #92400e)
      7. MathRenderError (generic) -> 'unknown' (deep red #7f1d1d)
    """
    error_cases = [
        (MathSyntaxError(-32602, "Syntax error in formula"), "syntax", "Syntax Error", "#9a3412"),
        (MathRenderTimeoutError(-32001, "Timeout during rendering"), "timeout", "Timeout", "#92400e"),
        (MathWorkerCrashedError(-32002, "Worker process terminated abruptly"), "crash", "Worker Crash", "#991b1b"),
        (MathBufferLimitExceededError(-32600, "IPC buffer overflow"), "buffer_limit", "Buffer Limit", "#991b1b"),
        (MathCircuitBreakerOpenError(-32003, "Circuit breaker tripped OPEN"), "circuit_breaker", "Circuit Breaker", "#991b1b"),
        (MathDegradedError(-32099, "Math rendering degraded during typing"), "degraded", "Degraded", "#92400e"),
        (MathRenderError(-32000, "Unclassified math failure"), "unknown", "Unknown", "#7f1d1d"),
    ]

    parser = PandocParser()
    uow_factory = _FakeUowFactory(job_id=1, output_path=str(tmp_path / "job_1.md"))
    mock_storage = MagicMock()

    # Load production QML MathErrorCard component to verify semantic color contract
    engine = QQmlEngine()
    qml_file = Path(__file__).resolve().parent.parent.parent / "interfaces" / "desktop" / "qml" / "components" / "MathErrorCard.qml"
    component = QQmlComponent(engine, str(qml_file))
    assert not component.isError(), f"MathErrorCard.qml loading errors: {component.errors()}"
    qml_card = component.create()
    assert qml_card is not None

    for exc_instance, expected_category, expected_label, expected_hex_color in error_cases:
        mock_renderer = MagicMock(spec=IMathRenderer)
        mock_renderer.cache = None
        mock_renderer.negative_memo = None

        def make_isolated_batch(requests):
            return {req.compute_hash(): exc_instance for req in requests}

        mock_renderer.render_batch_isolated.side_effect = make_isolated_batch

        service = MarkdownViewerService(
            parser=parser,
            uow_factory=uow_factory,
            storage=mock_storage,
            math_renderer=mock_renderer,
        )

        test_text = (
            f"Here is inline math $a^2 + b^2 = c^2$.\n\n"
            f"$$\n\\sum_{{k=1}}^n k\n$$\n"
        )

        dto = service.render_text(raw_text=test_text, active_regions=[], job_id=1)

        model = MarkdownDocumentModel()
        model.set_document(dto)

        # 1. Display math block node at row 1
        block_idx = model.index(1, 0)
        assert model.data(block_idx, MarkdownDocumentModel.MathHasErrorRole) is True
        assert model.data(block_idx, MarkdownDocumentModel.MathErrorCategoryRole) == expected_category
        assert model.data(block_idx, MarkdownDocumentModel.MathErrorMessageRole) == exc_instance.message
        assert model.data(block_idx, MarkdownDocumentModel.MathTexRole) == r"\sum_{k=1}^n k"

        # 2. Inline math segment within paragraph node at row 0
        para_idx = model.index(0, 0)
        segments = model.data(para_idx, MarkdownDocumentModel.SegmentsRole)
        math_segs = [s for s in segments if s.get("segmentType") == "math"]
        assert len(math_segs) == 1
        inline_seg = math_segs[0]
        assert inline_seg["hasError"] is True
        assert inline_seg["errorCategory"] == expected_category
        assert inline_seg["errorMessage"] == exc_instance.message
        assert inline_seg["mathTex"] == "a^2 + b^2 = c^2"
        assert "color:#f87171" in inline_seg["textHtml"]

        # 3. QML MathErrorCard semantic presentation contract
        qml_card.setProperty("category", expected_category)
        assert qml_card.property("categoryLabel") == expected_label
        assert qml_card.property("categoryBadgeColor").name().lower() == expected_hex_color.lower()


def test_negative_failure_memo_lifecycle_and_clearing(qapp, tmp_path):
    """
    Scenario 4: Verification of NegativeFailureMemo lifecycle boundaries.

    Verifies:
      - Formula timeout is recorded in NegativeFailureMemo.
      - Subsequent render hits memo immediately with 0 supervisor calls.
      - Lifecycle events (service clear, controller reload, controller document load,
        controller reset active draft) clear the memo.
      - Following clearing, subsequent render retries the supervisor.
    """
    mock_supervisor = MagicMock(spec=MathJaxProcessSupervisor)
    mock_supervisor.is_alive = True

    formula_tex = r"\int_0^1 x dx"
    req = MathRenderRequest(tex=formula_tex, display=True)
    req_hash = req.compute_hash()

    mock_supervisor.render.side_effect = MathRenderTimeoutError(-32001, "Timeout in worker")

    svg_cache = MathSvgCache(capacity=50)
    negative_memo = NegativeFailureMemo(capacity=50)
    client = MathJaxClient(
        supervisor=mock_supervisor,
        cache=svg_cache,
        negative_memo=negative_memo,
    )

    parser = PandocParser()
    uow_factory = _FakeUowFactory(job_id=50, output_path=str(tmp_path / "job_50.md"))
    mock_storage = MagicMock()
    service = MarkdownViewerService(
        parser=parser,
        uow_factory=uow_factory,
        storage=mock_storage,
        math_renderer=client,
    )

    ctrl = MarkdownViewerController(viewer_service=service)
    ctrl._active_job_id = 50

    # Initial render: times out and records in negative memo
    with pytest.raises(MathRenderTimeoutError):
        client.render(req)

    assert mock_supervisor.render.call_count == 1
    assert negative_memo.get(req_hash) is not None

    # Second evaluation hits negative memo directly (0 supervisor calls)
    with pytest.raises(MathRenderTimeoutError):
        client.render(req)

    assert mock_supervisor.render.call_count == 1

    # Verify lifecycle boundaries clear the memo
    def assert_memo_cleared_by(action: Callable[[], None]) -> None:
        negative_memo.put(req_hash, MathRenderTimeoutError(-32001, "Timeout"))
        assert negative_memo.get(req_hash) is not None
        action()
        assert negative_memo.get(req_hash) is None

    service.clear_math_negative_memo()
    assert negative_memo.get(req_hash) is None

    assert_memo_cleared_by(ctrl.reload)
    assert_memo_cleared_by(lambda: ctrl.loadDocument(job_id=51))
    assert_memo_cleared_by(ctrl.resetActiveDraft)

    # Retry evaluation after clearing succeeds when supervisor recovers
    mock_supervisor.render.side_effect = None
    mock_supervisor.render.return_value = {
        "svg_xml": "<svg class='recovered'><g>recovered</g></svg>",
        "width": "5ex",
        "height": "1.5ex",
        "vertical_align": "0ex",
    }

    result = client.render(req)
    assert mock_supervisor.render.call_count == 2
    assert result.hash == req_hash
    assert svg_cache.get(req_hash) is not None
    assert "recovered" in result.svg_xml

    ctrl.shutdown()
