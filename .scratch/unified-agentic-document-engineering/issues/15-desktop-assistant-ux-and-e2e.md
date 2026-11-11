# TICK-ADE-15: Desktop Assistant Review Dock QML, Split Candidate Preview & E2E Suite

- **Ticket ID:** `TICK-ADE-15`
- **Title:** Desktop Assistant Review Dock QML, Split Candidate Preview & E2E Suite
- **Milestone:** Milestone F (Desktop Assistant UX & Capabilities)
- **Priority:** P1 (Capstone Integration Ticket)
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Implement the native Qt/QML presentation layer (`AgentController`, `AssistantDock.qml`, plan review card, execution timeline, diff viewer, split candidate PDF preview) and the comprehensive end-to-end integration and verification suite for the Polpo Unified Agentic Document Engineering System.
- **Explicit Non-Goals:**
  - No business logic, persistence, or SQL inside QML or Qt controllers.
  - No remote network calls outside configured AI providers.
  - No GUI thread blocking during Quarto compilation or PDF rasterization.
- **Prerequisites:** `TICK-ADE-07`, `TICK-ADE-11`, `TICK-ADE-12`, and `TICK-ADE-14` completed; all ADRs approved.
- **Dependencies (Blocked By):** `TICK-ADE-07`, `TICK-ADE-11`, `TICK-ADE-12`, `TICK-ADE-14`.
- **Unblocks:** None (Capstone ticket completing the system).

---

## 1. Existing Source-Code Integration Points

- `interfaces/desktop/qml/views/ReviewWorkspaceView.qml`: Primary review workspace hosting viewer, editor, and the new assistant dock.
- `interfaces/desktop/controllers/export_controller.py`: Presentation controller patterns and signal routing.
- `interfaces/desktop/controllers/markdown_editor_controller.py`: In-memory draft buffer and atomic patch application.
- `infrastructure/pdf/mupdf_validator.py`: Concrete PyMuPDF validator wired into `PdfRepairService` (Finding B).

---

## 2. Proposed Presentation Components & Signals

### 2.1 Controller Contract (`interfaces/desktop/controllers/agent_controller.py`)
```python
class AgentController(QObject):
    # Reactive Properties
    isBusyChanged = Signal(bool)
    taskStatusChanged = Signal(str)
    planReady = Signal()
    consentRequired = Signal(str, str)          # payload_summary, consent_token
    actionApprovalRequired = Signal(str, str)   # action_description, approval_token
    proposalReady = Signal(str, str, bool)      # unified_diff, candidate_pdf_path, validation_passed
    taskCompleted = Signal(str)
    taskFailed = Signal(str, str)

    # Slots
    @Slot(int, str)
    def startTask(self, job_id: int, user_prompt: str) -> None:
        ...

    @Slot(str)
    def approvePlan(self, task_id: str) -> None:
        ...

    @Slot(str, str)
    def rejectPlan(self, task_id: str, reason: str) -> None:
        ...

    @Slot(str)
    def pauseTask(self, task_id: str) -> None:
        ...

    @Slot(str)
    def resumeTask(self, task_id: str) -> None:
        ...

    @Slot(str)
    def cancelTask(self, task_id: str) -> None:
        ...

    @Slot(str, str)
    def grantConsent(self, task_id: str, consent_token: str) -> None:
        ...

    @Slot(str)
    def applyProposal(self, task_id: str) -> None:
        ...

    @Slot(str)
    def rejectProposal(self, task_id: str) -> None:
        ...
```

### 2.2 QML View Architecture
- `interfaces/desktop/qml/components/assistant/AssistantDock.qml`: Dockable drawer containing Chat, Plan, Timeline, and Proposals.
- `PlanReviewCard.qml`: Reactive view of `plan.steps` with "Approve Plan" / "Reject Plan" actions.
- `ExecutionTimelineView.qml`: Step-by-step progress indicator with pause/cancel controls.
- `DiffInspectorView.qml`: Side-by-side visual diff of proposed changes using semantic theme palette.
- `CandidatePdfPreview.qml`: Dual-canvas preview comparing original and candidate PDFs.

---

## 3. Detailed Implementation Requirements

1. **Worker Offloading & GUI Responsiveness:**
   - Background tasks (Quarto compilation, AI HTTP requests, PDF rasterization) execute on `QThreadPool.globalInstance()`.
   - Results are communicated back to `AgentController` via Qt signals with queued connections (`Qt.QueuedConnection`).
   - The GUI thread remains 100% responsive without frame drops.
2. **Dependency Wiring (Finding B):**
   - Composition root wires concrete `PyMuPdfValidator` (from `TICK-ADE-12`) into `PdfRepairService` (from `TICK-ADE-11`).
3. **End-to-End Acceptance Suite:**
   - Implements `tests/acceptance/test_agentic_document_engineering_e2e.py` testing the complete lifecycle:
     User request $\to$ Plan generation $\to$ Plan approval $\to$ Tool execution $\to$ Scratchpad candidate recompilation $\to$ PyMuPDF validation $\to$ Diff presentation $\to$ User adoption $\to$ Editor buffer update $\to$ OCC Save.

---

## 4. Acceptance Criteria

- [ ] Assistant dock opens and closes smoothly inside `ReviewWorkspaceView.qml`.
- [ ] User can submit a request; plan steps appear reactively in `PlanReviewCard.qml`.
- [ ] Clicking "Approve Plan" begins step execution with live progress updates.
- [ ] Clicking "Pause" pauses execution; clicking "Resume" resumes it cleanly.
- [ ] Pre-transmission consent modal appears before external AI requests.
- [ ] Candidate PDF preview renders side-by-side with original document pages.
- [ ] Clicking "Apply Changes" updates the editor buffer, marks it dirty, and allows standard `Ctrl+Z` undo.
- [ ] End-to-end acceptance test passes offscreen via `QQuickView.grabWindow()`.

---

## 5. Test Specifications

### 5.1 Acceptance Tests
- `tests/acceptance/test_agentic_document_engineering_e2e.py`:
  - `test_full_supervised_agent_e2e_workflow()`: Tests full cycle from prompt to OCC save.
  - `test_plan_rejection_e2e()`: Tests user rejection cleanly halts task.
  - `test_cooperative_cancellation_e2e()`: Tests user cancellation cleanly terminates background execution.
  - `test_split_candidate_preview_rendering()`: Verifies offscreen scene graph rendering.

### 5.2 Negative & Security Tests
- `test_prevent_html_injection_in_qml_diff()`: Asserts special characters (`<`, `>`, `&`) are escaped before QML rendering.

### 5.3 Concurrency & Threading Tests
- `test_gui_thread_not_blocked_during_quarto_compilation()`: Asserts Qt event loop processes events during background compilation.

---

## 6. Error and Recovery Behavior

- Controller handles service errors cleanly, emitting `taskFailed(taskId, errorMessage)` and restoring UI state without freezing.

---

## 7. Observability & Audit Requirements

- Controller logs UI interactions and signal dispatches.

---

## 8. Documentation Updates

- Document full user interaction workflow in user guide and ADR-007.

---

## 9. Required Project Skills

- `verification-before-completion`: Verifying complete E2E workflow.
- `tdd`: Acceptance test suite development with headless Qt.

---

## 10. Verification Commands

```bash
pytest tests/acceptance/test_agentic_document_engineering_e2e.py -v
```

---

## 11. Definition of Done

- All acceptance tests pass with 100% coverage.
- Full desktop assistant UI and end-to-end integration verified.
