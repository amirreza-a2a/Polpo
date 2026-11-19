# TICK-ADE-04: Provider-Neutral Agent Orchestration Service & JSON Plan Generator

- **Ticket ID:** `TICK-ADE-04`
- **Title:** Provider-Neutral Agent Orchestration Service & JSON Plan Generator
- **Milestone:** Milestone A (Agent Foundations)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Implement the central `AgentOrchestrationService` in `application/services/agent_orchestration_service.py`, responsible for intent understanding, machine-readable JSON plan generation, step dispatching, cooperative cancellation, and unknown network outcome handling.
- **Explicit Non-Goals:**
  - No PySide6/Qt signals inside the application service (signals are adapted in `AgentController` in Milestone F).
  - No direct canonical document overwrites (modifications stay on `AgentWorkingCopy`).
  - No automated retry loops when external network outcome is unknown.
- **Prerequisites:** `TICK-ADE-02` and `TICK-ADE-03` completed; ADR-007 approved.
- **Dependencies (Blocked By):** `TICK-ADE-02`, `TICK-ADE-03`.
- **Unblocks:** `TICK-ADE-11`.

---

## 1. Existing Source-Code Integration Points

- `application/ports/ai_executor.py`: `IAIExecutionService` text execution with fallback.
- `infrastructure/ai/executor_service.py`: `RateLimitedAIExecutor` for outbound HTTPS provider communication.
- `core/policies/agent_task_policy.py`: Deterministic task lifecycle transitions.
- `application/services/tool_registry.py`: Allowlisted tool discovery and execution.
- `application/ports/agent_task_repository.py`: Task state and allowlisted journal persistence.

---

## 2. Proposed Application Interfaces & DTOs

```python
@dataclass(frozen=True)
class StartTaskCommand:
    job_id: int
    user_prompt: str
    context_overrides: Optional[Dict[str, Any]] = None

@dataclass(frozen=True)
class PlanApprovalCommand:
    task_id: str
    approved: bool
    rejection_reason: Optional[str] = None

@dataclass(frozen=True)
class StepProgressEvent:
    task_id: str
    step_index: int
    total_steps: int
    tool_name: str
    status: str
    duration_ms: int

class AgentOrchestrationService:
    def __init__(
        self,
        ai_executor: IAIExecutionService,
        tool_registry: ToolRegistry,
        task_repo: IAgentTaskRepository,
        policy: AgentTaskPolicy,
    ):
        ...

    def start_task(self, command: StartTaskCommand) -> AgentTask:
        ...

    def approve_plan(self, command: PlanApprovalCommand) -> None:
        ...

    def pause_task(self, task_id: str) -> None:
        ...

    def resume_task(self, task_id: str) -> None:
        ...

    def cancel_task(self, task_id: str) -> None:
        ...

    def grant_consent(self, task_id: str, consent_token: str) -> None:
        ...

    def approve_action(self, task_id: str, approval_token: str) -> None:
        ...
```

---

## 3. Detailed Implementation Requirements

1. **Structured JSON Plan Generation & Intent Classification (Finding A, Finding D):**
   - Deterministically classifies `command.user_prompt` (ephemeral) into a closed `TaskCategory` enum (`PDF_EXPORT_REPAIR`, `STYLE_PROFILE_APPLY`, `DOCUMENT_ANALYSIS`), persisting exclusively the allowlisted category token in `agent_tasks.task_goal`. A user-facing display label may be derived in RAM; arbitrary user prose is strictly prohibited from SQLite persistence.
   - The complete `AgentPlan` (including step rationales) remains strictly in memory; `agent_tasks.plan_json` persists strictly NULL or bounded step metadata (step index, allowlisted tool names, risk tiers, max 16 KB).
   - Dispatches structured prompt using `prompts/agent/planning_v1.txt`.
   - Strips Markdown code blocks (````json ... ````) before parsing.
   - Validates plan structure against schema version 1.
   - Transitions task from `CREATED` $\to$ `CONTEXT_GATHERING` $\to$ `PLANNING` $\to$ `AWAITING_PLAN_APPROVAL`.
   - Execution halts until `approve_plan()` is called.
2. **Step Execution Loop & Token Invalidation (Finding B):**
   - Iterates through `plan.steps` sequentially.
   - Evaluates governance via `ToolRegistry.execute_tool()`.
   - If `AWAITING_CONSENT` or `AWAITING_ACTION_APPROVAL`, transitions task and yields control.
   - Authorization tokens are strictly single-use and in-memory; invalid on process restart.
   - Checks cancellation flag between steps (`task.is_cancelled()`).
3. **Durable Write-Ahead Dispatch & Unknown Network Outcome Invariant (Finding B, Section 4):**
   - Before dispatching an external AI request or executing a non-idempotent consequential tool, the orchestrator persists a minimal `DISPATCH_INTENT` journal event under `BEGIN IMMEDIATE`.
   - If an external AI request times out, disconnects, or the process terminates before a response is received, the step outcome is marked `REQUEST_OUTCOME_UNKNOWN` (or `TOOL_ACTION_OUTCOME_UNKNOWN`).
   - The orchestrator NEVER automatically repeats the request and NEVER silently advances the provider fallback chain.
   - It transitions task to `INTERRUPTED` or `PAUSED`, records disposition code safely without storing prompt/response text, and requires explicit user instruction with potential duplicate billing disclosure.
   - User Restart creates a **brand-new `AgentTask`** with fresh `task_id`, fresh editor snapshot, and fresh authorization, preserving the interrupted task for audit.
4. **Journal Audit Logging (Finding A, Finding D, Finding E):**
   - Appends entries to `agent_task_journal` via `task_repo` using only allowlisted fields.
   - Replaces plain content hashes with opaque `step_correlation_id` (UUID4).
   - Constrains `summary` to allowlisted `JournalEventCode` enum values (e.g. `TASK_CREATED`, `PLAN_APPROVAL_REQUIRED`, `DISPATCH_INTENT`, `REQUEST_OUTCOME_UNKNOWN`, `TOOL_ACTION_OUTCOME_UNKNOWN`, `TASK_INTERRUPTED`, `TASK_DISMISSED`); arbitrary prose is strictly prohibited.

---

## 4. Acceptance Criteria

- [ ] `start_task()` successfully prompts the AI executor and parses a valid `AgentPlan`.
- [ ] Task halts deterministically in `AWAITING_PLAN_APPROVAL` without executing any tool step.
- [ ] Calling `approve_plan(approved=True)` executes plan steps sequentially.
- [ ] Calling `cancel_task()` terminates execution cooperatively and marks task `CANCELLED`.
- [ ] `task_goal` persists exclusively allowlisted `TaskCategory` enum tokens; `plan_json` is NULL or metadata-only.
- [ ] Step outputs and state transitions are recorded in the SQLite journal using allowlisted keys and typed event codes.
- [ ] Unknown network outcomes do not trigger automated request replays.
- [ ] AST check confirms zero imports of `PySide6`, `Qt`, or vendor AI SDKs in `application/services/agent_orchestration_service.py`.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_agent_orchestration_service.py`:
  - `test_orchestrator_planning_and_approval()`: Full happy path through plan approval.
  - `test_plan_rejection_halts_task()`: User rejection transitions to `REJECTED`.
  - `test_cooperative_cancellation_between_steps()`: Cancelling halts before next step.
  - `test_consent_gate_pauses_execution()`: External transmission tool halts in `AWAITING_CONSENT`.
  - `test_unknown_network_outcome_handling()`: Network timeout halts without automated retry and flags `REQUEST_OUTCOME_UNKNOWN`.
  - `test_write_ahead_dispatch_intent_persisted_under_begin_immediate()`: Verifies `DISPATCH_INTENT` journal row is written and committed prior to external dispatch.
  - `test_restart_task_creates_fresh_task_aggregate()`: Asserts that restarting an `INTERRUPTED` task creates a brand-new `AgentTask` aggregate with fresh ID, snapshot, and authorization.
  - `test_dismiss_interrupted_task_transitions_to_cancelled()`: Asserts that dismissing an `INTERRUPTED` task cleanly transitions it to `CANCELLED`.
  - `test_task_goal_persists_only_allowlisted_category()`: Verifies `task_goal` accepts only `TaskCategory` members.
  - `test_task_goal_rejects_arbitrary_user_sentence()`: Asserts arbitrary user prompt text in `task_goal` raises error.
  - `test_plan_rationale_never_persisted()`: Asserts natural-language plan rationales are kept in memory and never persisted in SQLite.
  - `test_journal_summary_accepts_only_allowlisted_event_code()`: Asserts `summary` accepts only `JournalEventCode` tokens.

### 5.2 Negative & Security Tests
- `test_reject_plans_with_unregistered_tools()`: Rejects model-generated plans requesting nonexistent tools.

### 5.3 Concurrency & Persistence Considerations
- Safe multi-step execution using worker-scoped UoW sessions.

---

## 6. Error and Recovery Behavior

- Tool execution errors trigger at most 1 replan attempt. If replanning fails or step fails again, task transitions to `FAILED`.
- Application shutdown during execution reconciles task to `INTERRUPTED`.

---

## 7. Observability & Audit Requirements

- Emits transport-neutral step progress events (`StepProgressEvent`).
- Appends allowlisted execution records to SQLite journal.

---

## 8. Documentation Updates

- Document orchestrator protocol in ADR-007.

---

## 9. Required Project Skills

- `codebase-design`: Clean service contracts.
- `tdd`: Test-driven orchestrator implementation with mock executors.

---

## 10. Verification Commands

```bash
pytest tests/unit/test_agent_orchestration_service.py -v
python3 -c "import ast; tree = ast.parse(open('application/services/agent_orchestration_service.py').read()); assert not any('PySide6' in ast.dump(n) or 'google' in ast.dump(n) for n in ast.walk(tree))"
```

---

## 11. Definition of Done

- All unit tests pass with 100% coverage.
- Provider-neutral plan generator and step loop verified.
