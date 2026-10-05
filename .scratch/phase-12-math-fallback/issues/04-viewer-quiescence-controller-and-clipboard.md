# 04: MarkdownViewerController Quiescence State Machine & Clipboard Slot

**What to build:** Implement the live typing quiescence state machine in `MarkdownViewerController` to protect editor responsiveness during active typing of complex or incomplete equations. Track consecutive math timeouts to enter degraded math mode, coordinate the 250ms debounce preview timer and 2.0s quiescence timer, add the UI clipboard slot, and manage the `NegativeFailureMemo` lifecycle across document operations.

**Blocked by:** 02 (`02-service-batch-isolation-and-fallback.md`), 03 (`03-qml-model-roles-and-error-delegates.md`)

**Status:** planned

## Scope

- Update `MarkdownViewerController` in `interfaces/desktop/controllers/markdown_viewer_controller.py`:
  - Add state variables:
    - `_is_math_degraded: bool = False`
    - `_consecutive_math_timeouts: int = 0`
  - Add Qt property and signal:
    - `isMathDegradedChanged = Signal()`
    - `isMathDegraded = Property(bool, is_math_degraded, notify=isMathDegradedChanged)`
  - Add 2.0s quiescence timer:
    - `_math_quiescence_timer = QTimer(self)`
    - Configured as single-shot with a 2,000 ms interval.
    - Connected to `_on_math_quiescence_timeout`.
  - Consecutive Timeout State Machine:
    - In `_on_internal_preview_loaded`:
      - If `document_dto.had_math_timeout` is `True`:
        - Increment `_consecutive_math_timeouts += 1`.
        - If `_consecutive_math_timeouts >= 2` and not `_is_math_degraded`:
          - Transition to degraded mode: `_is_math_degraded = True`.
          - Emit `isMathDegradedChanged`.
      - If `document_dto.had_math_timeout` is `False`:
        - Reset `_consecutive_math_timeouts = 0`.
        - If `_is_math_degraded`:
          - Exit degraded mode: `_is_math_degraded = False`.
          - Stop `_math_quiescence_timer`.
          - Emit `isMathDegradedChanged`.
  - Debounce vs Quiescence Timer Coordination:
    - In `scheduleLivePreview`:
      - If `_is_math_degraded`: restart `_math_quiescence_timer.start(2000)`.
    - In `_dispatch_pending_preview`:
      - Pass `degraded_math=self._is_math_degraded` to `self.viewer_service.render_preview(...)`.
  - Quiescence Recovery Handler:
    - In `_on_math_quiescence_timeout`:
      - Clear degraded mode: `_is_math_degraded = False`, `_consecutive_math_timeouts = 0`.
      - Emit `isMathDegradedChanged`.
      - Trigger an immediate full preview render (`degraded_math=False`) to seamlessly restore all formulas to SVG.
  - Implement Clipboard Slot:
    - `@Slot(str) def copyToClipboard(self, text: str) -> None`:
      - Sets system clipboard text via `QGuiApplication.clipboard().setText(text)`.
  - Manage Negative Failure Memo Lifecycle:
    - Clear `NegativeFailureMemo` on document switch (`open_document`), hard reload (F5 / `reload_document`), and successful document save (`resetActiveDraft` / save handler) via `self.viewer_service.clear_math_negative_memo()`.
- Add unit tests in `tests/unit/test_markdown_viewer_controller_quiescence.py`.

## Non-Goals

- Modifying `MarkdownEditorController` (editor controller remains strictly focused on text editing and cursor tracking).
- QML delegates or visual styling.

## Files Likely Impacted

- Modify: `interfaces/desktop/controllers/markdown_viewer_controller.py`
- Create: `tests/unit/test_markdown_viewer_controller_quiescence.py`

## Public Interfaces / Contracts

### Controller API Additions

```python
class MarkdownViewerController(QObject):
    isMathDegradedChanged = Signal()

    def is_math_degraded(self) -> bool:
        return self._is_math_degraded

    isMathDegraded = Property(bool, is_math_degraded, notify=isMathDegradedChanged)

    @Slot(str)
    def copyToClipboard(self, text: str) -> None:
        """Copies given text string to the system clipboard."""
        ...
```

## Architectural Invariants

- **Desktop Concurrency:** Document and preview rendering runs strictly off the Qt GUI thread in `ThreadPoolExecutor`.
- **Option B Invariant Preserved:** Live preview results must match the active draft revision (`draft_revision == self._draft_revision`); obsolete results are dropped.
- **Canonical Version Invariant:** Live preview and quiescence rendering MUST NEVER advance or mutate `_active_version`.
- **Clean Architecture:** Zero direct database access, raw SQL, or vendor SDK calls in the presentation controller.

## Acceptance Criteria

- [ ] Controller tracks consecutive timeouts from `document_dto.had_math_timeout`.
- [ ] Degraded mode triggers when `consecutive_timeouts >= 2` and emits `isMathDegradedChanged`.
- [ ] During degraded mode, 250ms preview timer dispatches `degraded_math=True`.
- [ ] Typing in degraded mode continuously resets the 2.0s quiescence timer.
- [ ] 2.0s after typing stops, quiescence timer fires, clears degraded mode, and re-renders full preview with `degraded_math=False`.
- [ ] A successful preview with 0 timeouts immediately resets consecutive timeouts to 0.
- [ ] `copyToClipboard(text)` copies text to system clipboard.
- [ ] `clear_math_negative_memo()` is called on document reload, save, and document switch.
- [ ] All unit tests pass.

## Verification / Tests Required

- `test_controller_enters_degraded_mode_after_two_timeouts()`: simulates 2 preview completions with `had_math_timeout=True`; asserts `isMathDegraded` becomes `True`.
- `test_controller_typing_resets_quiescence_timer()`: verifies typing during degraded mode restarts 2.0s timer.
- `test_controller_quiescence_timeout_triggers_full_rerender()`: verifies timer timeout clears degraded mode and dispatches `degraded_math=False`.
- `test_controller_zero_timeout_resets_counter()`: verifies clean preview resets consecutive timeout counter.
- `test_controller_copy_to_clipboard_slot()`: verifies clipboard text assignment.
- `test_controller_memo_cleared_on_reload_and_save()`: verifies negative memo clearing hook is invoked.

## Commit Boundary

Single commit: `feat(desktop): implement viewer controller live math quiescence and clipboard slot`

## Rollback

Revert commit cleanly.
