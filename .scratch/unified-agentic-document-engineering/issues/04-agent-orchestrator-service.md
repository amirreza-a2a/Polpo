# TICK-ADE-04: Provider-Neutral Agent Orchestration Service & JSON Plan Generator

- **Ticket ID:** `TICK-ADE-04`
- **Milestone:** Milestone A (Agent Foundations)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Blocked By:** `TICK-ADE-02`, `TICK-ADE-03`
- **Authoritative ADR:** ADR-007 (Unified Agent Orchestration & Tool Governance)

---

## 1. Objective

Implement the central `AgentOrchestrationService` in `application/services/agent_orchestration_service.py`, responsible for intent understanding, machine-readable JSON plan generation, step dispatching, and cooperative cancellation.

---

## 2. User Value

Provides the "brain" of the AI assistant. Authors receive clear, structured execution plans answering their document requests, and can pause, resume, or cancel tasks at any time.

---

## 3. Scope

- Implement `AgentOrchestrationService` in `application/services/agent_orchestration_service.py`:
  - `start_task(job_id: int, user_prompt: str, context_overrides: Optional[Dict[str, Any]] = None) -> AgentTask`: Initializes task, gathers document context, requests plan from AI, transitions to `AWAITING_PLAN_APPROVAL`.
  - `approve_plan(task_id: str) -> None`: Advances task to `RUNNING` and begins step execution loop.
  - `reject_plan(task_id: str, reason: str) -> None`: Transitions task to `REJECTED`.
  - `pause_task(task_id: str) -> None`: Sets cooperative pause flag; active worker pauses before next step.
  - `resume_task(task_id: str) -> None`: Resumes execution of pending steps.
  - `cancel_task(task_id: str) -> None`: Flags cooperative cancellation token; terminates active tool and marks task `CANCELLED`.
  - `grant_consent(task_id: str, consent_token: str) -> None`: Unblocks step awaiting external transmission consent.
- Prompt template:
  - Create versioned system prompt template in `prompts/agent/planning_v1.txt` instructing the model to emit machine-readable JSON plans following schema version 1.
- Step execution loop:
  - Dispatches tools via `ToolRegistry`.
  - Records step start, step output, and task state changes in SQLite journal via `IAgentTaskRepository`.
  - Handles bounded self-correction (maximum 1 replan attempt if a step fails).

---

## 4. Explicit Non-Goals

- No PySide6/Qt signals inside the application service (adapted into Qt signals in `AgentController` in Milestone F).
- No direct canonical document overwrites (modifications stay on `AgentWorkingCopy`).

---

## 5. Codebase References

- `application/ports/ai_executor.py` (`IAIExecutionService` text execution with fallback).
- `infrastructure/ai/executor_service.py` (`RateLimitedAIExecutor`).
- `core/policies/agent_task_policy.py` (`AgentTaskPolicy` validation).

---

## 6. Proposed Files & Components

- Create: `prompts/agent/planning_v1.txt`
- Create: `application/services/agent_orchestration_service.py`
- Create: `application/dto/agent_orchestration_dto.py`
- Create: `tests/unit/test_agent_orchestration_service.py`

---

## 7. Domain & Architectural Constraints

- **Rule 6.2 Compliance:** `application/` has zero imports of Qt, PySide6, or concrete vendor AI SDKs.
- **Rule 14 Compliance:** Cancellation must be cooperative and checkable via `task.is_cancelled()`.

---

## 8. Detailed Implementation Requirements

1. When calling `IAIExecutionService.execute_text_with_fallback()`, the orchestrator passes the active `ApiSlot` chain from `uow.apis`.
2. Model responses must be parsed as JSON. If the response contains markdown code block fences (````json ... ````), they must be stripped cleanly.
3. If JSON parsing fails or schema validation fails, the orchestrator triggers at most 1 retry with diagnostic error feedback before failing the task.
4. Execution stops immediately if `task.is_cancelled()` evaluates to `True`.

---

## 9. Acceptance Criteria

- [ ] `start_task()` successfully prompts the AI executor and parses a valid `AgentPlan`.
- [ ] Task halts in `AWAITING_PLAN_APPROVAL` without executing any tool step.
- [ ] Calling `approve_plan()` executes plan steps sequentially.
- [ ] Calling `cancel_task()` terminates execution and transitions to `CANCELLED`.
- [ ] Step outputs and state transitions are recorded in the SQLite journal.

---

## 10. Required Tests

- Unit tests in `tests/unit/test_agent_orchestration_service.py` verifying:
  - Happy path: request $\to$ plan $\to$ approval $\to$ execution $\to$ completion.
  - User rejection halts execution in `REJECTED`.
  - Cooperative cancellation halts between steps.
  - Fallback progression through API slots when primary slot fails.

---

## 11. Security Requirements

- Disallow plans requesting unregistered tool names; reject malformed plan schemas immediately.

---

## 12. Completion Evidence & Follow-up

- Unit test execution evidence passing 100%.
- Completes Milestone A; unblocks Milestone D (`TICK-ADE-11`).
