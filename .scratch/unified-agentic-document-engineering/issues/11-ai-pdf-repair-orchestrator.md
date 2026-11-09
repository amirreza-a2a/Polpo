# TICK-ADE-11: AI PDF Repair Orchestration Service & Bounded Self-Correction Loop

- **Ticket ID:** `TICK-ADE-11`
- **Milestone:** Milestone D (AI-Assisted PDF Repair)
- **Priority:** P1 (Blocker for Milestone D)
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Blocked By:** `TICK-ADE-04`, `TICK-ADE-06`, `TICK-ADE-10`
- **Authoritative ADR:** ADR-009 (Quarto PDF Failure Diagnostics & AI PDF Repair)

---

## 1. Objective

Implement `PdfRepairService` in `application/services/pdf_repair_service.py` to orchestrate end-to-end AI-assisted PDF repair, including consent disclosure, candidate patch generation across appropriate scopes, isolated scratchpad recompilation, and bounded self-correction.

---

## 2. User Value

Enables authors to repair complex whole-document PDF compilation failures with a single guided workflow. The assistant proposes minimal, verified fixes and proves they compile before asking the author to apply them.

---

## 3. Scope

- Implement `PdfRepairService` in `application/services/pdf_repair_service.py`:
  - `diagnose_and_repair(request: PdfRepairRequest) -> PdfRepairProposal`:
    1. Gathers `DiagnosticBundle` from `PdfDiagnosticService`.
    2. Builds minimal prompt context (failing snippet + sanitized diagnostic, respecting data minimization).
    3. Prompts AI provider via `RateLimitedAIExecutor` using `prompts/repair/quarto_repair_v1.txt`.
    4. Parses structured machine-readable JSON repair candidate (`repaired_snippet`, `patch_scope`, `rationale`).
    5. Applies patch candidate to an `AgentWorkingCopy`.
    6. Recompiles the full candidate document via `QuartoPdfAdapter` in isolated staging.
    7. Validates candidate PDF via `IPdfInspectionEngine` (TICK-ADE-12).
    8. If compilation fails, executes bounded retry (`max_retries = 1`) incorporating new compiler error.
    9. Returns verified `PdfRepairProposal` containing unified diff, candidate PDF path, and verification evidence.

---

## 4. Explicit Non-Goals

- No unbounded retry loops (strictly at most 2 total AI generation attempts per user request).
- No silent modification of canonical files on disk.

---

## 5. Codebase References

- `application/services/latex_repair_service.py` (Pattern for bounded retries and same-pipeline validation).
- `application/services/quarto_pdf_export_service.py` (Quarto export execution).
- `application/services/document_patch_service.py` (Patch application).

---

## 6. Proposed Files & Components

- Create: `prompts/repair/quarto_repair_v1.txt`
- Create: `application/services/pdf_repair_service.py`
- Create: `application/dto/pdf_repair_dto.py`
- Create: `tests/unit/test_pdf_repair_service.py`

---

## 7. Domain & Architectural Constraints

- **D-USER-02 & D-USER-03 Compliance:** Pre-transmission consent must be obtained; candidate validation must use the full Quarto toolchain; scopes must not conflate document and product fixes.
- **Rule 6.2 Compliance:** `application/services/pdf_repair_service.py` remains free of Qt and vendor SDK imports.

---

## 8. Detailed Implementation Requirements

1. The prompt payload must be disclosed to the caller via `PdfRepairConsentPayload` so the UI can present it before network dispatch.
2. If `error_category == TEMPLATE_CONFLICT` (e.g. `soul` in RTL), the service instructs the model to emit a publication style override (loading `ulem`) rather than modifying the author's Markdown text.
3. Candidate recompilation must enforce the 60-second execution timeout and `-no-shell-escape`.

---

## 9. Acceptance Criteria

- [ ] AI prompt context is minimized to failing fragment and sanitized compiler error.
- [ ] Candidate patch is applied to `AgentWorkingCopy` and compiled through actual Quarto/XeLaTeX.
- [ ] If first candidate fails compilation, second attempt receives the new compiler error.
- [ ] If second attempt fails, returns `PdfRepairProposal(validation_passed=False)` without infinite loop.
- [ ] Recompilation success produces valid candidate PDF and unified diff.

---

## 10. Required Tests

- Unit tests in `tests/unit/test_pdf_repair_service.py` verifying:
  - Successful candidate repair on first attempt.
  - Successful candidate repair on second attempt after self-correction.
  - Terminal failure handling after 2 failed attempts.
  - Data minimization (entire document is not sent when fragment suffices).

---

## 11. Security Requirements

- Verify candidate output for forbidden commands (`\write18`, `\catcode`, path traversals) before compiling.

---

## 12. Completion Evidence & Follow-up

- Unit test execution evidence passing 100%.
- Unblocks: `TICK-ADE-12`, `TICK-ADE-15`.
