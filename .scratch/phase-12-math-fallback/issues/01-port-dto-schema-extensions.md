# 01: Math Port Batch Isolation & Presentation DTO Schema Extensions

**What to build:** Extend the mathematical equation rendering port `IMathRenderer` and its concrete client `MathJaxClient` with an isolated batch rendering capability (`render_batch_isolated`) where individual formula errors do not halt batch evaluation. Extend the presentation DTOs (`MarkdownDocumentDTO`, `MarkdownNodeDTO`, `InlineSegmentDTO`) with typed math error state attributes and timeout tracking.

**Blocked by:** None (unblocked first step).

**Status:** planned

## Scope

- Extend `IMathRenderer` port in `application/ports/math_renderer.py` with abstract method:
  `render_batch_isolated(requests: Sequence[MathRenderRequest]) -> Mapping[str, Union[MathRenderResult, MathRenderError]]`
- Implement `render_batch_isolated` in `infrastructure/math/mathjax_client.py`:
  - Executes canonical 5-stage evaluation order per request.
  - Catches `MathRenderError` instances per formula and maps `hash -> MathRenderError`.
  - Maps successful renders as `hash -> MathRenderResult`.
  - Guarantees fatal `MathSupervisorShutdownError` is NEVER swallowed and immediately propagates.
- Extend `MarkdownDocumentDTO` in `application/dto/markdown_dto.py`:
  - Add field: `had_math_timeout: bool = False` (indicates whether any formula in the document encountered a timeout).
- Extend `MarkdownNodeDTO` in `application/dto/markdown_dto.py`:
  - Add fields:
    - `has_error: bool = False`
    - `error_category: str = ""`
    - `error_message: str = ""`
- Extend `InlineSegmentDTO` in `application/dto/markdown_dto.py`:
  - Add fields:
    - `has_error: bool = False`
    - `error_category: str = ""`
    - `error_message: str = ""`
- Add comprehensive unit tests in `tests/unit/test_mathjax_client.py` and `tests/unit/test_markdown_dto.py`.

## Non-Goals

- `MarkdownViewerService` fallback projection logic (deferred to Ticket 02).
- QML models or visual delegates (deferred to Ticket 03).
- Controller quiescence timers or degradation state machines (deferred to Ticket 04).
- Altering the existing synchronous `render` or fail-fast `render_batch` contracts.

## Files Likely Impacted

- Modify: `application/ports/math_renderer.py`
- Modify: `infrastructure/math/mathjax_client.py`
- Modify: `application/dto/markdown_dto.py`
- Modify: `tests/unit/test_mathjax_client.py`
- Modify/Create: `tests/unit/test_markdown_dto.py`

## Public Interfaces / Contracts

### `IMathRenderer` Port Extension

```python
class IMathRenderer(ABC):
    # ... existing methods: render, render_batch ...

    @abstractmethod
    def render_batch_isolated(
        self, requests: Sequence[MathRenderRequest]
    ) -> Mapping[str, Union[MathRenderResult, MathRenderError]]:
        """Render multiple TeX math expressions with per-formula failure isolation.

        Unlike render_batch(), an individual formula syntax error, timeout, or crash
        does not abort processing for subsequent requests in the batch.

        Args:
            requests: Sequence of formula rendering specifications.

        Returns:
            Mapping of formula SHA-256 hash to either MathRenderResult (on success)
            or MathRenderError (on failure).

        Raises:
            MathSupervisorShutdownError: If the supervisor is shut down during processing.
        """
        raise NotImplementedError
```

### Presentation DTO Extensions

```python
@dataclass(frozen=True)
class InlineSegmentDTO:
    segment_type: str
    text_html: str = ""
    image_ref: Optional[VisualRegionRefDTO] = None
    math_tex: Optional[str] = None
    math_hash: Optional[str] = None
    has_error: bool = False
    error_category: str = ""
    error_message: str = ""

@dataclass(frozen=True)
class MarkdownNodeDTO:
    node_id: str
    node_type: str
    content: str = ""
    raw_markdown: str = ""
    # ... existing fields ...
    math_tex: Optional[str] = None
    math_hash: Optional[str] = None
    has_error: bool = False
    error_category: str = ""
    error_message: str = ""

@dataclass(frozen=True)
class MarkdownDocumentDTO:
    job_id: int
    version: int
    nodes: Tuple[MarkdownNodeDTO, ...]
    region_to_occurrences: Mapping[str, Tuple[RegionOccurrenceRef, ...]]
    had_math_timeout: bool = False
```

## Architectural Invariants

- **Clean Architecture:** `application/ports/` and `application/dto/` remain 100% free of PySide6/Qt, SQLite, or external service dependencies.
- **Backward Compatibility:** All new DTO fields provide default values (`False`, `""`), ensuring existing call sites, serialization, and test fixtures remain valid without modifications.
- **Fatal Shutdown Propagation:** `MathSupervisorShutdownError` MUST propagate out of `render_batch_isolated` immediately; it must never be masked as an isolated formula error.
- **Immutability:** All DTOs remain `frozen=True` dataclasses.

## Acceptance Criteria

- [ ] `IMathRenderer` defines `render_batch_isolated` with exact signature and docstrings.
- [ ] `MathJaxClient.render_batch_isolated` executes each request through the canonical 5-stage evaluation order.
- [ ] When a formula fails in `render_batch_isolated`, its exception is mapped to its hash, and subsequent formulas are processed normally.
- [ ] Positive cache (`MathSvgCache`) and negative memo (`NegativeFailureMemo`) are populated according to ADR-002 stage rules.
- [ ] If `MathSupervisorShutdownError` is raised, `render_batch_isolated` allows it to bubble up immediately.
- [ ] `MarkdownDocumentDTO.had_math_timeout` defaults to `False`.
- [ ] `MarkdownNodeDTO` and `InlineSegmentDTO` expose `has_error`, `error_category`, and `error_message` defaulting to `False`, `""`, and `""`.
- [ ] All existing and new tests pass cleanly with `pytest`.

## Verification / Tests Required

- `test_render_batch_isolated_success()`: all valid formulas return `MathRenderResult`.
- `test_render_batch_isolated_partial_failure()`: mixed batch of valid formulas and failing formulas (`MathSyntaxError`, `MathRenderTimeoutError`); verifies valid formulas succeed and failing formulas return their respective `MathRenderError`.
- `test_render_batch_isolated_propagates_shutdown()`: asserts `MathSupervisorShutdownError` is raised immediately.
- `test_markdown_dto_fields_defaults_and_backward_compatibility()`: asserts DTOs instantiate with default values and support immutability and equality.

## Commit Boundary

Single commit: `feat(math): add render_batch_isolated to math renderer and extend markdown DTO error fields`

## Rollback

Revert commit cleanly. No schema migration or persistent state affected.
