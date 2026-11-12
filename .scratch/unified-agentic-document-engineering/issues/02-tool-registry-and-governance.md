# TICK-ADE-02: Allowlisted Tool Registry, Typed Tool Contracts & Risk Governance Policy

- **Ticket ID:** `TICK-ADE-02`
- **Title:** Allowlisted Tool Registry, Typed Tool Contracts & Risk Governance Policy
- **Milestone:** Milestone A (Agent Foundations)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Implement the typed, allowlisted `ToolRegistry` and deterministic `ToolGovernancePolicy` in `application/services/tool_registry.py` and `core/policies/tool_governance_policy.py`.
- **Explicit Non-Goals:**
  - No unrestricted shell execution tool (`bash`, `sh`, `cmd`, `powershell` are strictly prohibited).
  - No arbitrary filesystem read/write tools.
  - No direct SQL execution tools.
- **Prerequisites:** `TICK-ADE-01` completed; ADR-007 approved.
- **Dependencies (Blocked By):** `TICK-ADE-01`.
- **Unblocks:** `TICK-ADE-04`.

---

## 1. Existing Source-Code Integration Points

- `application/ports/ai_executor.py`: AI execution boundary.
- `core/markdown/ast.py`: CommonMark AST parser used for document inspection.
- `application/services/latex_repair_service.py`: Architectural precedent for structured argument validation and pre-transmission consent gating.

---

## 2. Proposed Domain Interfaces & DTOs

```python
class ToolRiskLevel(Enum):
    READ_ONLY = "READ_ONLY"                    # Autonomous execution within approved plan
    ISOLATED_MUTATION = "ISOLATED_MUTATION"    # Autonomous execution within scratchpad RAM / staging
    EXTERNAL_TRANSMISSION = "EXTERNAL_TRANSMISSION"  # Gated on pre-transmission consent modal
    CONSEQUENTIAL_SYSTEM = "CONSEQUENTIAL_SYSTEM"    # Gated on explicit user confirmation modal
    CANONICAL_ADOPTION = "CANONICAL_ADOPTION"        # Gated on final user review ("Apply Changes")

@dataclass(frozen=True)
class ToolExecutionContext:
    task_id: str
    job_id: int
    working_copy_id: Optional[str] = None
    consent_token: Optional[str] = None
    approval_token: Optional[str] = None

@dataclass(frozen=True)
class ToolExecutionResult:
    status: str                                # "SUCCESS", "ERROR", "AWAITING_CONSENT", "AWAITING_APPROVAL"
    output_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    duration_ms: int = 0

class ITool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        pass

    @property
    @abstractmethod
    def risk_level(self) -> ToolRiskLevel:
        pass

    @abstractmethod
    def validate_arguments(self, args: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        pass

    @abstractmethod
    def execute(self, context: ToolExecutionContext, args: Dict[str, Any]) -> ToolExecutionResult:
        pass
```

---

## 3. Detailed Implementation Requirements

1. **Tool Registry Implementation (`application/services/tool_registry.py`):**
   - Thread-safe registry dictionary mapping tool name $\to$ `ITool`.
   - `register_tool(tool: ITool) -> None`: Validates tool name does not contain forbidden words (`exec`, `system`, `shell`, `spawn`).
   - `get_tool(name: str) -> Optional[ITool]`.
   - `list_tools() -> Sequence[ToolDescriptor]`.
   - `execute_tool(name: str, context: ToolExecutionContext, args: Dict[str, Any]) -> ToolExecutionResult`: Evaluates governance before dispatching execution.
2. **Initial Allowlisted Read-Only Tools:**
   - `ReadDocumentStructureTool`: Queries CommonMark AST headers and block count.
   - `InspectMathFormulasTool`: Scans document for MathJax inline and display formulas.
   - `InspectDiagramsTool`: Scans document for TikZ, PGFPlots, and CircuitTikZ raw blocks.
3. **Governance Policy, Token Invalidation & Durable Intent (Finding B, Section 4):**
   - If risk is `EXTERNAL_TRANSMISSION` and `context.consent_token is None`, intercept with `ToolExecutionResult(status="AWAITING_CONSENT")`.
   - If risk is `CONSEQUENTIAL_SYSTEM` and `context.approval_token is None`, intercept with `ToolExecutionResult(status="AWAITING_APPROVAL")`.
   - **Write-Ahead Intent Protocol:** Before dispatching an external transmission or executing a non-idempotent consequential action (e.g. package installation), persist a minimal `DISPATCH_INTENT` journal event under `BEGIN IMMEDIATE`.
   - **Process Termination Disposition:** If the process terminates during tool execution, startup recovery marks the tool outcome as `TOOL_ACTION_OUTCOME_UNKNOWN` or `REQUEST_OUTCOME_UNKNOWN`. Automated replay is strictly forbidden; environment reconciliation is required.
   - **Token Scope & Expiration (Finding B):** Consent and approval tokens are single-use ephemeral UUID4 strings stored strictly in-memory with a 10-minute maximum TTL. Tokens expire immediately on application termination; persisted token references in the journal are non-reusable audit records.
   - **Payload Minimization & Ephemeral Diagnostic Boundary (Finding A, Finding D):** Tools accepting source or diagnostic inputs enforce bounded payloads (maximum 64 KB total; diagnostic excerpts capped at 10 KB / 100 lines). Crucially, this 10 KB excerpt allowance is strictly ephemeral in RAM (`ToolExecutionContext` / `DiagnosticBundle`) for approved tool input; it NEVER authorizes persisting compiler output or raw tool arguments into SQLite records. Attempts to pass full documents or plaintext API keys are rejected.

---

## 4. Acceptance Criteria

- [ ] Unregistered tool names cannot be invoked; returns structured error.
- [ ] Attempting to invoke an `EXTERNAL_TRANSMISSION` tool without a valid consent token returns `AWAITING_CONSENT`.
- [ ] `ReadDocumentStructureTool` accurately extracts headings and region counts from Markdown.
- [ ] Tool catalog serializes cleanly to JSON for inclusion in system planning prompts.
- [ ] Bounded 10 KB diagnostic excerpts remain strictly ephemeral in RAM and are never persisted in SQLite.
- [ ] AST check confirms zero imports of `subprocess` in `application/services/tool_registry.py`.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_tool_registry.py`:
  - `test_tool_registration_and_lookup()`: Verifies tool allowlist operations.
  - `test_argument_validation()`: Verifies invalid arguments fail before execution.
  - `test_governance_consent_gating()`: Verifies `EXTERNAL_TRANSMISSION` stops at consent gate.
  - `test_governance_action_approval_gating()`: Verifies `CONSEQUENTIAL_SYSTEM` stops at approval gate.
  - `test_write_ahead_dispatch_intent_logged_before_execution()`: Asserts `DISPATCH_INTENT` journal event is committed before invoking external HTTP or consequential actions.
  - `test_termination_records_tool_action_outcome_unknown()`: Asserts that process interruption during tool execution records `TOOL_ACTION_OUTCOME_UNKNOWN` without automated replay.
  - `test_ephemeral_token_expiration_on_termination()`: Asserts consent and approval tokens cannot survive process boundaries.
  - `test_read_document_structure_tool()`: Validates extraction of headings on CommonMark fixtures.

### 5.2 Negative & Security Tests
- `test_reject_shell_tool_registration()`: Ensures tools named `shell_exec` or `run_command` are rejected at registration.
- `test_sanitized_diagnostic_excerpt_remains_ephemeral()`: Asserts that 10 KB diagnostic excerpts are maintained strictly in memory and are never serialized or routed to database persistence.
- `test_tool_arguments_never_persisted()`: Asserts that raw tool arguments containing source fragments or compiler diagnostics are not persisted in task or journal tables.

### 5.3 Concurrency & Persistence Considerations
- Tool execution is stateless or scoped to `ToolExecutionContext`.

---

## 6. Error and Recovery Behavior

- Exceptions thrown inside `tool.execute()` are caught and converted to `ToolExecutionResult(status="ERROR", error_message=str(e))` to prevent orchestrator thread crashes.

---

## 7. Observability & Audit Requirements

- Records tool execution duration in milliseconds.
- Outputs opaque step correlation IDs and allowlisted metadata for the journal, strictly prohibiting plain content hashes of tool arguments or outputs.

---

## 8. Documentation Updates

- Update tool catalog table in ADR-007.

---

## 9. Required Project Skills

- `codebase-design`: Clean interface boundary for `ITool`.
- `tdd`: Test-first implementation of tool validation and governance policies.

---

## 10. Verification Commands

```bash
pytest tests/unit/test_tool_registry.py -v
python3 -c "import ast; tree = ast.parse(open('application/services/tool_registry.py').read()); assert not any('subprocess' in ast.dump(n) for n in ast.walk(tree))"
```

---

## 11. Definition of Done

- All code and unit tests pass with 100% coverage.
- Allowlisted tool catalog operational and verified.
