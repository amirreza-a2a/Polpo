# 05: End-to-End Fallback Integration & Problem Register P08 Regression Suite

**What to build:** Build an end-to-end integration test suite and Problem Register P08 regression suite validating isolated partial formula failure, live typing quiescence resilience, all failure categorization mappings, negative memo lifecycle clearing, and clean architecture invariant enforcement.

**Blocked by:** 01 (`01-port-dto-schema-extensions.md`), 02 (`02-service-batch-isolation-and-fallback.md`), 03 (`03-qml-model-roles-and-error-delegates.md`), 04 (`04-viewer-quiescence-controller-and-clipboard.md`)

**Status:** planned

## Scope

- Create `tests/integration/test_p08_math_fallback_integration.py`:
  - Test 1: Isolated Partial Failure in Mixed Documents:
    - Document containing valid display math, broken syntax display math, valid inline math, and timeout inline math.
    - Asserts valid equations render to SVG in cache and presentation model.
    - Asserts broken display equation produces `MathErrorCard` state (`mathHasError=True`, `mathErrorCategory="syntax"`, monospace TeX).
    - Asserts broken inline equation produces inline error segment with tooltip message.
    - Asserts MathJax supervisor and client remain responsive for subsequent renders.
  - Test 2: Live Typing Degradation & Quiescence Restoration:
    - Simulates rapid typing in editor with incomplete / slow formulas.
    - Asserts consecutive timeouts >= 2 transitions controller to `isMathDegraded = True`.
    - Asserts ongoing typing uses 250ms debounce preview with `degraded_math=True` (warm cache hits render, new equations project non-blocking degraded fallback without invoking worker).
    - Asserts 2.0s pause triggers quiescence timer, resets degraded mode to `False`, and dispatches full render restoring all equations to SVG.
  - Test 3: Failure Categorization & Color-Code Mapping:
    - Verifies each `MathRenderError` subclass maps to its expected category string token across the entire stack:
      - `MathSyntaxError` -> `"syntax"`
      - `MathRenderTimeoutError` -> `"timeout"`
      - `MathWorkerCrashedError` -> `"crash"`
      - `MathBufferLimitExceededError` -> `"buffer_limit"`
      - `MathCircuitBreakerOpenError` -> `"circuit_breaker"`
      - Typing degradation -> `"degraded"`
  - Test 4: Negative Memo Lifecycle & Clearing:
    - Formula times out and is recorded in `NegativeFailureMemo`.
    - Subsequent evaluation hits memo without worker RPC.
    - Document reload (F5) or save operation calls `clear_math_negative_memo()`.
    - Next evaluation retries supervisor render.
  - Test 5: Architectural Boundary Verification:
    - AST inspection asserting `application/services/markdown_viewer_service.py` and `infrastructure/math/` contain zero PySide6/Qt imports.
    - AST inspection asserting `interfaces/desktop/controllers/` contains zero direct imports of `infrastructure/math/` and zero direct SQL executions.

## Non-Goals

- Production code modifications (unless test discovery uncovers a defect).
- Testing legacy Telegram transport.

## Files Likely Impacted

- Create: `tests/integration/test_p08_math_fallback_integration.py`
- Modify: `tests/unit/test_phase10e3a_architecture_invariants.py` (if expanding standing AST checks)

## Public Interfaces / Contracts

None (pure test and verification suite).

## Architectural Invariants

- **Zero Web Servers:** All tests execute against embedded local components.
- **Deterministic Concurrency:** Worker pools and timers in integration tests must join cleanly within bounded timeouts with zero leaked threads.
- **Complete Test Isolation:** Temporary files and session state are isolated per test fixture.

## Acceptance Criteria

- [ ] All 5 integration scenarios execute and pass deterministically.
- [ ] No race conditions, deadlocks, or leaked threads during timer/quiescence testing.
- [ ] AST static analysis confirms 100% architectural boundary compliance.
- [ ] The full test suite (`pytest`) runs cleanly with zero regressions across the codebase.

## Verification / Tests Required

- Execute:
  ```bash
  .venv/bin/pytest tests/integration/test_p08_math_fallback_integration.py -v
  .venv/bin/pytest tests/unit/test_markdown_viewer_service_math* tests/unit/test_mathjax_* -v
  ```

## Commit Boundary

Single commit: `test(math): add end-to-end integration and P08 regression test suite`

## Rollback

Revert commit cleanly.
