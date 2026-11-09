# TICK-ADE-02: Allowlisted Tool Registry, Typed Tool Contracts & Risk Governance Policy

- **Ticket ID:** `TICK-ADE-02`
- **Milestone:** Milestone A (Agent Foundations)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Blocked By:** `TICK-ADE-01`
- **Authoritative ADR:** ADR-007 (Unified Agent Orchestration & Tool Governance)

---

## 1. Objective

Implement the typed, allowlisted `ToolRegistry` and deterministic `ToolGovernancePolicy` in `application/services/tool_registry.py` and `core/policies/tool_governance_policy.py`.

---

## 2. User Value

Ensures that the AI assistant can only execute explicitly vetted, authorized internal capabilities. Prevents arbitrary shell commands, unexpected network calls, or unauthorized file mutations.

---

## 3. Scope

- Define `ToolRiskLevel` enum in `core/policies/tool_governance_policy.py`:
  - `READ_ONLY`: Autonomous execution allowed within task plan.
  - `ISOLATED_MUTATION`: Autonomous execution inside scratchpad `AgentWorkingCopy`.
  - `EXTERNAL_TRANSMISSION`: Gated on pre-transmission consent modal disclosing exact payload.
  - `CONSEQUENTIAL_SYSTEM`: Gated on explicit user confirmation modal.
  - `CANONICAL_ADOPTION`: Gated on explicit final review ("Apply Changes").
- Define abstract application port `ITool` in `application/ports/tool_port.py`:
  - `name: str`, `description: str`, `risk_level: ToolRiskLevel`.
  - `validate_arguments(args: Dict[str, Any]) -> Tuple[bool, Optional[str]]`.
  - `execute(context: ToolExecutionContext, args: Dict[str, Any]) -> ToolExecutionResult`.
- Implement `ToolRegistry` in `application/services/tool_registry.py`:
  - Thread-safe registry dictionary mapping tool name $\to$ `ITool`.
  - `register_tool(tool: ITool) -> None`.
  - `get_tool(name: str) -> Optional[ITool]`.
  - `list_tools() -> Sequence[ToolDescriptor]`.
  - `execute_tool(name: str, context: ToolExecutionContext, args: Dict[str, Any], auth_token: Optional[str]) -> ToolExecutionResult`.
- Implement initial read-only inspection tools:
  - `ReadDocumentStructureTool`: Queries CommonMark AST headers and block count.
  - `InspectMathFormulasTool`: Scans for MathJax inline and display formulas.
  - `InspectDiagramsTool`: Scans for TikZ/PGF/CircuitTikZ raw blocks.

---

## 4. Explicit Non-Goals

- No unrestricted shell tool (`bash`, `cmd`, `sh` are strictly prohibited).
- No arbitrary filesystem read/write tools.
- No direct database SQL execution tools.

---

## 5. Codebase References

- `application/ports/ai_executor.py` (AI execution boundary).
- `core/markdown/ast.py` (CommonMark AST parser).
- `application/services/latex_repair_service.py` (Pattern for structured argument validation).

---

## 6. Proposed Files & Components

- Create: `core/policies/tool_governance_policy.py`
- Create: `application/ports/tool_port.py`
- Create: `application/services/tool_registry.py`
- Create: `application/tools/document_inspection_tools.py`
- Create: `tests/unit/test_tool_registry.py`

---

## 7. Domain & Architectural Constraints

- **Deterministic Enforcement:** Permissions must be evaluated by Python policy code; the AI model's assertions cannot grant permissions.
- **Clean Architecture:** `ToolRegistry` has zero imports of PySide6/Qt or concrete database drivers.

---

## 8. Detailed Implementation Requirements

1. Attempting to execute an unknown tool name returns `ToolExecutionResult(status="ERROR", error="Tool 'xyz' not registered in allowlist")`.
2. Attempting to execute an `EXTERNAL_TRANSMISSION` tool without an active `ConsentToken` returns `ToolExecutionResult(status="AWAITING_CONSENT")`.
3. Tool arguments exceeding 64KB are rejected to prevent memory exhaustion.
4. Tool outputs are validated against machine-readable JSON schemas before return.

---

## 9. Acceptance Criteria

- [ ] Unregistered tool names cannot be invoked.
- [ ] Attempting to invoke a high-risk tool without authorization fails closed.
- [ ] `ReadDocumentStructureTool` accurately extracts headings and region counts from Markdown.
- [ ] Registered tools can be serialized into a provider-neutral prompt catalog description.
- [ ] Zero imports of `subprocess` in `application/services/tool_registry.py`.

---

## 10. Required Tests

- Unit tests in `tests/unit/test_tool_registry.py` verifying:
  - Registration and discovery of tools.
  - Argument validation failures reject execution.
  - Policy enforcement intercepts unauthorized high-risk tools.
  - Inspection tools return expected AST metrics.

---

## 11. Security Requirements

- Disallow registering any tool whose name contains shell execution primitives (`exec`, `system`, `shell`, `spawn`).

---

## 12. Completion Evidence & Follow-up

- Unit test execution evidence passing 100%.
- Unblocks: `TICK-ADE-04`.
