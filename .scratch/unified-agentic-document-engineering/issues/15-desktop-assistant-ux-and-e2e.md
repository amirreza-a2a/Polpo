# TICK-ADE-15: Desktop Assistant Review Dock QML, Split Candidate Preview & E2E Suite

- **Ticket ID:** `TICK-ADE-15`
- **Milestone:** Milestone F (Desktop Assistant UX & Capabilities)
- **Priority:** P1 (Capstone Ticket)
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Blocked By:** `TICK-ADE-07`, `TICK-ADE-12`, `TICK-ADE-14`
- **Authoritative ADR:** ADR-007, ADR-008, ADR-009, ADR-010

---

## 1. Objective

Implement the native Qt/QML presentation layer (`AgentController`, `AssistantDock.qml`, diff viewer, split candidate PDF preview) and the comprehensive end-to-end integration and verification suite for the Polpo Unified Agentic Document Engineering System.

---

## 2. User Value

Unifies all document intelligence capabilities into an elegant, reactive desktop experience inside the Review Workspace. Authors can submit document requests, review transparent plans, inspect side-by-side candidate diffs, preview compiled PDFs, and apply verified changes with a single click.

---

## 3. Scope

- Implement `AgentController` in `interfaces/desktop/controllers/agent_controller.py`:
  - QObject presentation controller managing `QThreadPool` worker dispatching.
  - Exposes reactive properties: `isBusy: bool`, `taskStatus: str`, `planSteps: QVariantList`, `currentStepIndex: int`, `hasActiveProposal: bool`, `candidatePdfPath: str`, `unifiedDiff: str`, `validationPassed: bool`.
  - Exposes QML slots:
    - `@Slot(int, str)` `startTask(jobId, prompt)`
    - `@Slot(str)` `approvePlan(taskId)`
    - `@Slot(str, str)` `rejectPlan(taskId, reason)`
    - `@Slot(str)` `pauseTask(taskId)`
    - `@Slot(str)` `resumeTask(taskId)`
    - `@Slot(str)` `cancelTask(taskId)`
    - `@Slot(str, str)` `grantConsent(taskId, token)`
    - `@Slot(str)` `applyProposal(taskId)`
    - `@Slot(str)` `rejectProposal(taskId)`
  - Signals: `taskStateChanged`, `planReady`, `consentRequired`, `actionApprovalRequired`, `proposalReady`, `taskCompleted`, `taskFailed`.
- Implement native QML components under `interfaces/desktop/qml/components/assistant/`:
  - `AssistantDock.qml`: Collapsible drawer docked in `ReviewWorkspaceView.qml`.
  - `PlanReviewCard.qml`: Displays ordered plan steps with status badges and "Approve Plan" / "Reject Plan" buttons.
  - `ExecutionTimelineView.qml`: Streaming step progress with pause/cancel controls.
  - `DiffInspectorView.qml`: Side-by-side visual diff of proposed Markdown changes.
  - `CandidatePdfPreview.qml`: Split-view raster preview comparing original PDF with candidate PDF.
- Build E2E test suite in `tests/acceptance/test_agentic_document_engineering_e2e.py`:
  - Tests complete workflow: Request $\to$ Plan $\to$ Tool Execution $\to$ Candidate Recompilation $\to$ Editor Buffer Adoption $\to$ OCC Save.

---

## 4. Explicit Non-Goals

- No business logic or SQL inside QML or Qt controllers.
- No remote network calls outside configured AI providers.

---

## 5. Codebase References

- `interfaces/desktop/qml/views/ReviewWorkspaceView.qml` (Primary workspace container).
- `interfaces/desktop/controllers/export_controller.py` (Presentation controller patterns).
- `interfaces/desktop/controllers/markdown_editor_controller.py` (Editor integration).

---

## 6. Proposed Files & Components

- Create: `interfaces/desktop/controllers/agent_controller.py`
- Create: `interfaces/desktop/qml/components/assistant/AssistantDock.qml`
- Create: `interfaces/desktop/qml/components/assistant/PlanReviewCard.qml`
- Create: `interfaces/desktop/qml/components/assistant/ExecutionTimelineView.qml`
- Create: `interfaces/desktop/qml/components/assistant/DiffInspectorView.qml`
- Create: `interfaces/desktop/qml/components/assistant/CandidatePdfPreview.qml`
- Modify: `interfaces/desktop/qml/views/ReviewWorkspaceView.qml` (Add AssistantDock)
- Create: `tests/acceptance/test_agentic_document_engineering_e2e.py`

---

## 7. Domain & Architectural Constraints

- **Rule 6.3 Compliance:** `AgentController` must not directly import `sqlite3`, `keyring`, or vendor SDKs; it interacts strictly with application services.
- **Rule 6.4 Compliance:** QML interacts exclusively with explicitly registered QObject controllers.
- **Rule 12 Compliance:** Heavy operations (Quarto compilation, AI HTTP requests, PDF rasterization) must never execute on the Qt GUI thread.

---

## 8. Detailed Implementation Requirements

1. Background workers execute via `QThreadPool.globalInstance()`. Communication back to `AgentController` uses Qt signals with queued connections (`Qt.QueuedConnection`).
2. When `planReady` is emitted, `AssistantDock.qml` automatically expands the plan review card with smooth animation.
3. The diff inspector highlights additions in green (`theme.success`) and deletions in red (`theme.error`), using semantic palette tokens.
4. Clean shutdown: `AgentController.shutdown()` terminates running workers cooperatively and disconnects signals.

---

## 9. Acceptance Criteria

- [ ] Assistant dock opens and closes smoothly inside `ReviewWorkspaceView.qml`.
- [ ] User can submit a request; plan steps appear reactively in `PlanReviewCard.qml`.
- [ ] Clicking "Approve Plan" begins step execution with live progress updates.
- [ ] Clicking "Pause" pauses execution; clicking "Resume" resumes it cleanly.
- [ ] Pre-transmission consent modal appears before external AI requests.
- [ ] Candidate PDF preview renders side-by-side with original document pages.
- [ ] Clicking "Apply Changes" updates the editor buffer, marks it dirty, and allows standard `Ctrl+Z` undo.
- [ ] End-to-end acceptance test passes offscreen via `QQuickView.grabWindow()`.

---

## 10. Required Tests

- Acceptance & integration tests in `tests/acceptance/test_agentic_document_engineering_e2e.py` verifying:
  - Full happy path E2E execution with mock AI executor and headless Qt quick view.
  - User plan rejection workflow.
  - Cooperative cancellation between steps.
  - Full candidate PDF preview generation.
  - Editor buffer OCC save integrity after applying changes.

---

## 11. Security Requirements

- Disallow HTML injection in QML text elements; use plain text format or escape strings before rendering in delegates.

---

## 12. Completion Evidence & Follow-up

- E2E acceptance test suite passing 100%.
- Completes Milestone F; system ready for final maintainer review and release qualification.
