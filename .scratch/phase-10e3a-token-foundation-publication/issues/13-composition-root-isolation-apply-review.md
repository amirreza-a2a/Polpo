# 13: Composition Root Isolation & Quarantine Docstring Precision for ApplyReviewService

**What to build:** Eliminate dead instantiation of `ApplyReviewService` in `DesktopAppContainer` and align code documentation with the actual runtime architecture, ensuring that quarantined legacy code is not needlessly instantiated during desktop startup and that static invariant tests enforce this isolation.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

## Scope & Boundary

- In `interfaces/desktop/composition.py:157-161`, remove `self.apply_review_service = ApplyReviewService(...)` and remove `from application.services.apply_review_service import ApplyReviewService` from `DesktopAppContainer`.
- In `application/services/apply_review_service.py:32-36`, correct the class docstring from *"disconnected at the desktop composition level"* to *"omitted from active desktop presentation wiring in app.py"*.
- In `tests/unit/test_phase10e3a_architecture_invariants.py`, re-tighten `test_ast_sole_active_runtime_publisher()` to assert `DesktopAppContainer` does not instantiate `ApplyReviewService`.

## Non-Goals

- Deleting `ApplyReviewService` entirely (formal removal scheduled for Phase 10E.3b).
- Modifying `DocumentViewerController.__init__` (it already accepts `apply_review_service: Optional[ApplyReviewService] = None` and defaults safely).

## Files Likely Impacted

- Modify: `interfaces/desktop/composition.py` — remove unused `ApplyReviewService` instantiation and import.
- Modify: `application/services/apply_review_service.py` — correct docstring description of quarantine point.
- Modify: `tests/unit/test_phase10e3a_architecture_invariants.py` — tighten AST assertion against composition root instantiation.

## Acceptance Criteria

- [ ] `DesktopAppContainer` in `composition.py` does not instantiate or retain `ApplyReviewService`.
- [ ] `ApplyReviewService` class docstring accurately documents that quarantine disconnection happens at presentation controller wiring in `app.py:237`.
- [ ] Static AST invariant tests pass with the tightened assertion verifying zero `ApplyReviewService` instantiation in `composition.py`.
- [ ] All desktop bootstrap and container tests pass cleanly.

## Tests Required

- `test_ast_composition_container_omits_quarantined_apply_review_service`: AST test inspecting `composition.py` to confirm zero `ApplyReviewService` call nodes exist in `DesktopAppContainer.__init__`.
