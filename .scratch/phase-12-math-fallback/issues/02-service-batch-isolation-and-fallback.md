# 02: MarkdownViewerService Batch Isolation & Fallback Projection

**What to build:** Update `MarkdownViewerService` (`render_preview` and `render_text`) to use isolated batch math rendering, project individual formula failures into presentation DTOs with typed categories, track math timeouts on `MarkdownDocumentDTO.had_math_timeout`, and support typing degradation mode (`degraded_math: bool = False`) where warm cache hits render while cache misses receive non-blocking placeholders.

**Blocked by:** 01 (`01-port-dto-schema-extensions.md`)

**Status:** planned

## Scope

- Update `render_preview` and `render_text` signatures in `application/services/markdown_viewer_service.py`:
  - Retain existing canonical method names `render_preview` and `render_text` (do NOT create or rename to `project_draft_text`).
  - Add optional keyword argument `degraded_math: bool = False`.
- Integrate `IMathRenderer.render_batch_isolated`:
  - Collect all display and inline math requests from parsed AST blocks.
  - When `degraded_math=False`:
    - Dispatch batch through `math_renderer.render_batch_isolated(requests)`.
    - Track whether any formula resulted in `MathRenderTimeoutError`.
  - When `degraded_math=True`:
    - Check if formula is already warm in positive cache (if available via `math_renderer.cache.get()`).
    - Warm formulas render normally as rich SVGs.
    - Cache misses are NOT dispatched to supervisor or worker; projected immediately with `has_error=True`, `error_category="degraded"`, and descriptive message.
- Implement Failure Categorization:
  - Map exceptions to canonical category string tokens:
    - `MathSyntaxError` -> `"syntax"`
    - `MathRenderTimeoutError` -> `"timeout"`
    - `MathWorkerCrashedError` -> `"crash"`
    - `MathBufferLimitExceededError` -> `"buffer_limit"`
    - `MathCircuitBreakerOpenError` -> `"circuit_breaker"`
    - Degraded cache miss -> `"degraded"`
    - Unknown `MathRenderError` -> `"unknown"`
- Implement Display Math Fallback Projection:
  - On `MathBlock`:
    - Succeeded: `has_error=False`, `error_category=""`, `error_message=""`, `math_tex=tex`, `math_hash=hash`.
    - Failed: `has_error=True`, `error_category=category`, `error_message=str(exc)`, `math_tex=tex`, `math_hash=hash`.
- Implement Inline Math Fallback Projection:
  - On `InlineSpan(span_type=InlineType.MATH)`:
    - Succeeded: `InlineSegmentDTO(segment_type="math", text_html='<img src="image://math/{hash}" align="middle"/>', math_tex=tex, math_hash=hash, has_error=False)`.
    - Failed: `InlineSegmentDTO(segment_type="math", text_html='<span class="math-error">${escaped_tex}$</span>', math_tex=tex, math_hash=hash, has_error=True, error_category=category, error_message=str(exc))`.
- Track Document Math Timeout:
  - Set `had_math_timeout = True` on the returned `MarkdownDocumentDTO` if any formula encountered a timeout.
- Implement Memo Clearing Helper:
  - Add `clear_math_negative_memo(self) -> None` on `MarkdownViewerService` to clear `self.math_renderer.negative_memo` if available.
- Unit tests in `tests/unit/test_markdown_viewer_service_math_fallback.py` and `tests/unit/test_markdown_viewer_service_mathjax.py`.

## Non-Goals

- QML presentation model roles or delegates (deferred to Ticket 03).
- Controller timers or consecutive-timeout state machines (deferred to Ticket 04).
- Altering visual region resolution or document publication workflows.

## Files Likely Impacted

- Modify: `application/services/markdown_viewer_service.py`
- Modify: `tests/unit/test_markdown_viewer_service_mathjax.py`
- Create: `tests/unit/test_markdown_viewer_service_math_fallback.py`

## Public Interfaces / Contracts

### Method Signatures on `MarkdownViewerService`

```python
class MarkdownViewerService:
    def render_preview(
        self,
        job_id: int,
        raw_text: str,
        base_version: int = 1,
        *,
        degraded_math: bool = False,
    ) -> MarkdownDocumentDTO:
        ...

    def render_text(
        self,
        raw_text: str,
        active_regions: Sequence[VisualRegion],
        job_id: int,
        version: int = 1,
        base_dir: Optional[str] = None,
        *,
        degraded_math: bool = False,
    ) -> MarkdownDocumentDTO:
        ...

    def clear_math_negative_memo(self) -> None:
        """Clears the session-scoped negative failure memo in the underlying math renderer."""
        ...
```

## Architectural Invariants

- **Clean Architecture:** `MarkdownViewerService` remains 100% free of Qt, PySide6, and SQLite.
- **Non-Halting Evaluation:** A failure in equation 1 MUST NEVER prevent valid equation 2 from rendering or appearing in the document.
- **Cache Preservation in Degraded Mode:** Warm equations in the SVG cache must continue rendering as rich SVGs even when typing degradation is active.
- **Pure Projection:** `render_preview` and `render_text` perform zero database mutations, zero file writes, and zero OCC advancement.

## Acceptance Criteria

- [ ] `render_preview` and `render_text` accept optional `degraded_math: bool = False`.
- [ ] Document with mixed valid and invalid formulas renders valid formulas as SVG images and flags failed formulas with `has_error=True`.
- [ ] Error categories (`syntax`, `timeout`, `crash`, `buffer_limit`, `circuit_breaker`) are correctly mapped and populated in `MarkdownNodeDTO` and `InlineSegmentDTO`.
- [ ] When a `MathRenderTimeoutError` occurs, `MarkdownDocumentDTO.had_math_timeout` is `True`.
- [ ] When no timeouts occur, `MarkdownDocumentDTO.had_math_timeout` is `False`.
- [ ] In degraded mode (`degraded_math=True`), warm cached formulas produce valid SVG references, while cache misses receive `error_category="degraded"` without invoking the worker.
- [ ] `clear_math_negative_memo()` safely calls `negative_memo.clear()` when available on the renderer.
- [ ] All new and existing unit tests pass.

## Verification / Tests Required

- `test_render_text_isolated_partial_failures()`: document with 1 valid math block, 1 broken syntax math block, 1 valid inline math, and 1 timeout inline math; asserts valid formulas have `has_error=False`, broken have `has_error=True` with respective categories.
- `test_render_text_had_math_timeout_flag()`: asserts `had_math_timeout` is `True` when timeout happens, and `False` when only syntax errors happen.
- `test_render_text_degraded_math_mode()`: asserts warm cache items render as SVGs, new items are tagged as `degraded` without worker RPCs.
- `test_clear_math_negative_memo()`: asserts negative memo is cleared when helper is called.

## Commit Boundary

Single commit: `feat(markdown): implement batch math isolation and fallback projection in viewer service`

## Rollback

Revert commit cleanly.
