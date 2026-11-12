# TICK-ADE-11: AI PDF Repair Orchestration Service & Bounded Self-Correction Loop

- **Ticket ID:** `TICK-ADE-11`
- **Title:** AI PDF Repair Orchestration Service & Bounded Self-Correction Loop
- **Milestone:** Milestone D (AI-Assisted PDF Repair)
- **Priority:** P1 (Blocker for Milestone D)
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Implement `PdfRepairService` in `application/services/pdf_repair_service.py` to orchestrate end-to-end AI-assisted PDF repair, including pre-transmission consent disclosure, candidate patch generation across appropriate scopes, isolated scratchpad recompilation, candidate inspection via `IPdfInspectionEngine`, and bounded self-correction.
- **Explicit Non-Goals:**
  - No unbounded retry loops (strictly at most 2 total AI generation attempts per user request).
  - No silent modification of canonical files on disk.
  - No generating arbitrary raw LaTeX macros for preamble injection (Finding A, Finding F).
- **Prerequisites:** `TICK-ADE-04`, `TICK-ADE-06`, and `TICK-ADE-10` completed; ADR-009 approved.
- **Dependencies (Blocked By):** `TICK-ADE-04`, `TICK-ADE-06`, `TICK-ADE-10`.
- **Unblocks:** `TICK-ADE-15`.

---

## 1. Existing Source-Code Integration Points

- `application/services/latex_repair_service.py`: Reference for bounded retries and same-pipeline candidate validation.
- `application/services/quarto_pdf_export_service.py`: Quarto compilation execution.
- `application/services/document_patch_service.py`: Line-based patch application with stable anchors.
- `application/ports/pdf_validator_port.py`: Abstract foundation contract `IPdfInspectionEngine` (defined in `TICK-ADE-10`, Finding B).

---

## 2. Proposed Application Interfaces & DTOs

```python
@dataclass(frozen=True)
class PdfRepairRequest:
    task_id: str
    job_id: int
    working_copy_id: str
    failure_id: str
    diagnostic_bundle: DiagnosticBundle

@dataclass(frozen=True)
class PdfRepairConsentPayload:
    task_id: str
    failing_snippet: str
    mapped_markdown_line: Optional[int]
    sanitized_error_excerpt: str
    target_scope: Optional[RemediationScope]

@dataclass(frozen=True)
class PdfRepairProposal:
    task_id: str
    working_copy_id: str
    patch_set: PatchSet
    validation_passed: bool
    candidate_pdf_path: Optional[str]
    unified_diff: str
    rationale: str
    attempts_used: int
```

---

## 3. Detailed Implementation Requirements

1. **Inspection Port Ownership (Finding B):**
   - `PdfRepairService` accepts `IPdfInspectionEngine` via dependency injection.
   - It depends ONLY on the already-defined abstract contract in `application/ports/pdf_validator_port.py`. It does NOT depend on a later ticket or concrete PyMuPDF adapter. Unit tests inject a test double/mock.
2. **Minimal Prompt Context & Pre-Transmission Consent (Finding G):**
   - Discloses `PdfRepairConsentPayload` before dispatching external model requests.
   - Builds prompt using `prompts/repair/quarto_repair_v1.txt`.
3. **Zero Arbitrary Macro Injection & Fail-Closed Unknowns (Finding A, Finding C, Finding D):**
   - If `error_category == TEMPLATE_CONFLICT` (e.g. `soul` in RTL), the service instructs the model to emit a publication style token override (`strikethrough_engine = "ulem"`), governed by maintainer proposal `D-PROP-08` (Status: `PROPOSED — AWAITING EXPLICIT MAINTAINER APPROVAL`), NOT arbitrary `\newcommand` or `\usepackage` lines. If D-PROP-08 is unapproved, routes to product defect reporting (`suggested_action = SuggestedAction.REPORT_PRODUCT_DEFECT`).
   - If `error_category == UNKNOWN` or `remediation_scope is None`, the repair orchestrator immediately halts automated patch generation (`proposal_generation_allowed = False`, `automatic_patch_application_allowed = False`), sets `validation_passed = False`, and yields raw diagnostic evidence to the user for human inspection with `suggested_action = SuggestedAction.HUMAN_INSPECTION_REQUIRED`.
4. **Candidate Recompilation & Bounded Loop (Finding F):**
   - Applies candidate patch to `AgentWorkingCopy`.
   - Recompiles full candidate PDF in `.pdf_staging/`.
   - Inspects candidate PDF via `IPdfInspectionEngine.inspect_pdf()` using bounded `expected_text_anchors: Tuple[str, ...]` and typed `expected_diagrams: Tuple[DiagramExpectation, ...]` (without requiring full `source_markdown`). Text-only PDFs pass with empty expectations.
   - If compilation fails, executes at most one bounded retry (`max_retries = 1`) passing the new compiler error.
   - If second attempt fails, returns `PdfRepairProposal(validation_passed=False)`.

---

## 4. Acceptance Criteria

- [ ] AI prompt context is minimized to failing fragment and sanitized compiler error.
- [ ] Candidate patch is applied to `AgentWorkingCopy` and recompiled through actual Quarto/XeLaTeX.
- [ ] If first candidate fails compilation, second attempt receives the new compiler error.
- [ ] If second attempt fails, returns `PdfRepairProposal(validation_passed=False)` without infinite loop.
- [ ] Candidate PDF is verified via `IPdfInspectionEngine` using typed `expected_diagrams: Tuple[DiagramExpectation, ...]`.
- [ ] Unknown or unmapped errors halt patch generation fail-closed.
- [ ] No raw LaTeX macros are injected into preambles.
- [ ] AST check confirms zero imports of `PySide6`, `Qt`, `sqlite3`, or vendor AI SDKs in `application/services/pdf_repair_service.py`.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_pdf_repair_service.py`:
  - `test_repair_success_first_attempt()`: First candidate compiles and passes mock inspection.
  - `test_repair_success_second_attempt()`: First candidate fails, second attempt self-corrects and succeeds.
  - `test_repair_terminal_failure_after_two_attempts()`: Verifies bounded exit after 2 failed attempts.
  - `test_template_conflict_uses_typed_style_override()`: Confirms `soul` conflict emits typed token override rather than macro injection.
  - `test_data_minimization_disclosure()`: Confirms consent payload exposes minimal excerpt.

### 5.2 Negative & Security Tests
- `test_reject_arbitrary_preamble_macro_injection()`: Ensures repair proposals containing `\newcommand` or `\usepackage` are rejected by safety policy before compilation.
- `test_unknown_error_cannot_generate_patch()`: Asserts that when error is classified as `UNKNOWN` or has `remediation_scope = None`, no AI patch generation is dispatched.
- `test_patch_proposal_does_not_authorize_adoption()`: Asserts candidate proposal generation in scratchpad does not authorize editor buffer mutation or canonical OCC publication.

### 5.3 Concurrency & Persistence Considerations
- Recompilation executed within isolated staging directories under timeout governance (60s).

---

## 6. Error and Recovery Behavior

- Recompilation timeouts or subprocess crashes are handled gracefully, recording the failure in candidate status.

---

## 7. Observability & Audit Requirements

- Emits step progress events tracking repair generation, compilation duration, and validation metrics.

---

## 8. Documentation Updates

- Document repair orchestration and scope enforcement in ADR-009.

---

## 9. Required Project Skills

- `codebase-design`: Clean dependency injection of inspection port.
- `tdd`: Test-driven repair loop with mock AI executor and mock inspection engine.

---

## 10. Verification Commands

```bash
pytest tests/unit/test_pdf_repair_service.py -v
python3 -c "import ast; tree = ast.parse(open('application/services/pdf_repair_service.py').read()); assert not any('PySide6' in ast.dump(n) or 'fitz' in ast.dump(n) for n in ast.walk(tree))"
```

---

## 11. Definition of Done

- All unit tests pass with 100% coverage.
- Bounded self-correction loop and macro safety verified.
