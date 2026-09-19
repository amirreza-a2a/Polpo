# 10: Static Invariant Audit & 10E.3a Verification Suite

**What to build:** An automated static AST architecture audit and complete end-to-end verification suite enforcing all 9 architectural invariants of Phase 10E.3a. This ticket validates that no competing active runtime Markdown publishers exist, that `DocumentPublicationService` has zero coupling to artifact watermarks or compatibility shims, that `ApplyReviewService` is strictly isolated as a quarantined legacy source exception, and that the entire regression suite and scoped whitespace checks pass cleanly.

**Blocked by:** 02: Lossless Character-Level Micro-Lexer (FSM), 07: Desktop Bootstrap Integration (Backfill + Reconciliation Wiring), 08: Writer Migration — Pipeline 1 Initial Document, 09: Writer Migration — MarkdownEditorService.

**Status:** ready-for-agent

## Scope

- Create static architecture verification test `tests/unit/test_phase10e3a_architecture_invariants.py`:
  - AST inspection: `DocumentPublicationService` contains zero references to `output_artifact_version_watermark` or `active_markdown_version`.
  - AST inspection: No active runtime component in `interfaces/desktop/composition.py` or `interfaces/desktop/app.py` wires a direct canonical Markdown write path other than `DocumentPublicationService`.
  - AST inspection: `DocumentViewerController` is instantiated with `apply_review_service=None`.
  - Quarantined exception: `ApplyReviewService` source is verified as the sole permitted legacy exception for direct Markdown writes; any other class attempting direct canonical writes fails the test.
- End-to-end multi-writer OCC verification test: concurrent `publish_version()` attempts trigger deterministic OCC / in-flight protection.
- Crash recovery end-to-end simulation across all 6 scenarios in an integrated bootstrap flow.
- Scoped whitespace validation verification: `git diff --check HEAD~10..HEAD` passes cleanly across all Phase 10E.3a commits without checking unmodified historical files.
- Full test suite pass (`pytest -q`).

## Non-Goals

- Implementing features or altering production code.
- Removing `ApplyReviewService` source code (scheduled for removal in Phase 10E.3b).
- Modifying historical prompt files in `prompts/`.

## Files Likely Impacted

- Create: `tests/unit/test_phase10e3a_architecture_invariants.py`
- Create / Update: `tests/integration/test_publication_authority_e2e.py`

## Invariants to Enforce

1. **Independent Dimensions:** Document version and visual artifact version are independent dimensions.
2. **Sole Version Authority:** `document_versions` is the sole authoritative document-version history.
3. **Pointer Decoupling:** `jobs.output_path` is only an active document pointer, not a version authority.
4. **Watermark Isolation:** `output_artifact_version_watermark` is never used by `DocumentPublicationService` for OCC or version calculation.
5. **Fail-Closed Integrity:** Missing or unreadable active canonical files are quarantined (`integrity_status = 'QUARANTINED'`, `sha256 = NULL`), never treated as version 0.
6. **No Fake Hashes:** `document_versions.sha256` contains only real SHA-256 hex digests or `NULL`. No sentinel strings.
7. **Empty Documents Valid:** 0-byte readable canonical Markdown is valid, receiving real SHA-256 and `VALID` status.
8. **No Intent Stealing:** Active publication intents are never stolen or expired based on elapsed time.
9. **Sole Active Publisher:** `DocumentPublicationService` is the sole active runtime canonical Markdown publisher. `ApplyReviewService` is disconnected at the composition level and remains the sole quarantined legacy exception in source code.

## Acceptance Criteria

- [ ] AST test passes: `DocumentPublicationService` contains zero references to `output_artifact_version_watermark`.
- [ ] AST test passes: `DocumentPublicationService` contains zero references to `active_markdown_version`.
- [ ] AST test passes: `DocumentViewerController` in `app.py` is constructed with `apply_review_service=None`.
- [ ] AST test passes: `ApplyReviewService` is the only source file outside `DocumentPublicationService` containing direct canonical Markdown writes, tagged as quarantined legacy exception.
- [ ] E2E tests pass: initial publication, sequential version increments, OCC collision rejection, and all 6 crash recovery scenarios.
- [ ] `git diff --check HEAD~10..HEAD` passes cleanly for all Phase 10E.3a commits.
- [ ] Full project test suite passes with zero failures.

## Tests Required

- `test_ast_publication_service_watermark_isolation()`
- `test_ast_publication_service_shim_isolation()`
- `test_ast_sole_active_runtime_publisher()`
- `test_ast_apply_review_quarantined_exception()`
- `test_e2e_publication_authority_lifecycle()`
- `test_e2e_recovery_after_simulated_crash()`

## Commit Boundary

Single commit: `test(architecture): enforce publication invariants with quarantined ApplyReviewService exception`

## Rollback

Reverting removes the static audit tests.

## Out of Scope

- Implementing Phase 10E.3b (ReviewWorkspaceSession, visual region insertion/recrop commands, UI).
- Implementing Phase 10E.4 (Component-aware 3-way semantic diff3 merge).
- Implementing Phase 10E.5 (Modern Prompt Architecture).
