# 03: QML Presentation Model Roles & Visual Math Error Delegates

**What to build:** Extend `MarkdownDocumentModel` with dedicated QML roles for math error states (`mathHasError`, `mathErrorCategory`, `mathErrorMessage`) and implement user-visible fallback delegates: `MathErrorCard.qml` for display equations and interactive tooltip-enabled inline segments in `MarkdownInlineFlow.qml`.

**Blocked by:** 01 (`01-port-dto-schema-extensions.md`)

**Status:** planned

## Scope

- Update `MarkdownDocumentModel` in `interfaces/desktop/models/markdown_document_model.py`:
  - Add roles:
    - `MathHasErrorRole = Qt.ItemDataRole.UserRole + 28` (`b"mathHasError"`)
    - `MathErrorCategoryRole = Qt.ItemDataRole.UserRole + 29` (`b"mathErrorCategory"`)
    - `MathErrorMessageRole = Qt.ItemDataRole.UserRole + 30` (`b"mathErrorMessage"`)
  - Expose roles in `roleNames()` and `data()`.
  - Update `_node_dto_to_item()`:
    - Map `mathHasError`, `mathErrorCategory`, `mathErrorMessage` on root node items.
    - Propagate `hasError`, `errorCategory`, `errorMessage` in segment dictionaries (`segments`, `listItemSegments`, `tableCellSegments`, `quoteChildren`).
- Create `interfaces/desktop/qml/components/MathErrorCard.qml`:
  - Error block card presentation for failed display math.
  - Distinct category badges with semantic colors:
    - Amber (`#f59e0b`): `syntax`
    - Red (`#ef4444`): `timeout`, `crash`
    - Orange (`#f97316`): `buffer_limit`
    - Purple (`#8b5cf6`): `circuit_breaker`
    - Gray (`#6b7280`): `degraded`
  - Selectable monospace raw LaTeX block.
  - Diagnostic error message subtext.
  - "Copy LaTeX" button invoking `controller.copyToClipboard(rawTex)`.
- Update `interfaces/desktop/qml/components/MarkdownNodeDelegate.qml`:
  - In `mathBlockComponent`:
    - Switch conditionally between `MathErrorCard` (when `model.mathHasError == true`) and normal SVG `Image` (when `model.mathHasError == false`).
- Update `interfaces/desktop/qml/components/MarkdownInlineFlow.qml`:
  - Expand segment routing: route to flow layout when segments contain either images or failed inline math formulas (`hasComplexSegments`).
  - Add inline error delegate:
    - Monospace font with subtle red/amber background tint and dotted underline.
    - Native Qt Quick `ToolTip` via `HoverHandler` or `MouseArea` displaying `modelData.errorMessage`.
    - Click interaction to copy raw formula via `controller.copyToClipboard(modelData.mathTex)`.
- Add unit tests in `tests/unit/test_markdown_document_model_math_roles.py`.

## Non-Goals

- Controller quiescence timers or consecutive timeout tracking (deferred to Ticket 04).
- Application service logic or math parsing.

## Files Likely Impacted

- Modify: `interfaces/desktop/models/markdown_document_model.py`
- Create: `interfaces/desktop/qml/components/MathErrorCard.qml`
- Modify: `interfaces/desktop/qml/components/MarkdownNodeDelegate.qml`
- Modify: `interfaces/desktop/qml/components/MarkdownInlineFlow.qml`
- Create: `tests/unit/test_markdown_document_model_math_roles.py`

## Public Interfaces / Contracts

### `MarkdownDocumentModel` Roles

```python
MathHasErrorRole = Qt.ItemDataRole.UserRole + 28      # b"mathHasError"
MathErrorCategoryRole = Qt.ItemDataRole.UserRole + 29 # b"mathErrorCategory"
MathErrorMessageRole = Qt.ItemDataRole.UserRole + 30  # b"mathErrorMessage"
```

### `MathErrorCard.qml` Contract

```qml
Item {
    id: errorCardRoot
    property string rawTex: ""
    property string errorCategory: ""
    property string errorMessage: ""
    property real scaleFactor: 1.0
    property var controller: null
    ...
}
```

## Architectural Invariants

- **Presentation Boundary:** Models and QML delegates do NOT make business decisions; they strictly project DTO state.
- **Null-Safe Controller Bridge:** QML actions (such as "Copy LaTeX") must safely guard against `controller === null`.
- **High-Performance Virtualization:** Delegates recycle cleanly without memory leaks or stale binding issues in virtualized `ListView`s.

## Acceptance Criteria

- [ ] `MarkdownDocumentModel` exports `b"mathHasError"`, `b"mathErrorCategory"`, `b"mathErrorMessage"` in `roleNames()`.
- [ ] Model returns correct values for block nodes and inline segment dictionaries.
- [ ] `MathErrorCard.qml` displays category badge, raw LaTeX, error subtext, and copy button.
- [ ] `MarkdownNodeDelegate.qml` renders `MathErrorCard` when `model.mathHasError` is true, and normal image when false.
- [ ] `MarkdownInlineFlow.qml` displays inline math errors with dotted underline, monospace font, and hover tooltip.
- [ ] Clicking copy button or inline error triggers `controller.copyToClipboard(...)`.
- [ ] All new and existing model unit tests pass.

## Verification / Tests Required

- `test_model_math_error_roles()`: verifies `data()` returns boolean, string category, and string message for error nodes.
- `test_model_inline_segment_error_propagation()`: verifies inline segment dictionaries carry `hasError`, `errorCategory`, `errorMessage`.
- `test_model_reconcile_preserves_math_error_state()`: verifies non-destructive reconciliation updates math error roles via `dataChanged`.

## Commit Boundary

Single commit: `feat(desktop): add math error roles to document model and visual fallback delegates`

## Rollback

Revert commit cleanly.
