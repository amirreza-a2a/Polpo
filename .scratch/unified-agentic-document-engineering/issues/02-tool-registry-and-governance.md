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
3. **Governance Policy & Token Invalidation (`core/policies/tool_governance_policy.py`):**
   - If risk is `EXTERNAL_TRANSMISSION` and `context.consent_token is None`, intercept with `ToolExecutionResult(status="AWAITING_CONSENT")`.
   - If risk is `CONSEQUENTIAL_SYSTEM` and `context.approval_token is None`, intercept with `ToolExecutionResult(status="AWAITING_APPROVAL")`.
   - **Token Scope & Expiration (Finding B):** Consent and approval tokens are single-use ephemeral UUID4 strings stored strictly in-memory with a 10-minute maximum TTL. Tokens expire immediately on application termination; persisted token references in the journal are non-reusable audit records.
   - **Payload Minimization (Finding A):** Tools accepting source or diagnostic inputs enforce bounded payloads (maximum 64 KB total; diagnostic excerpts capped at 10 KB / 100 lines); attempts to pass full documents or plaintext API keys are rejected.

---

## 4. Acceptance Criteria

- [ ] Unregistered tool names cannot be invoked; returns structured error.
- [ ] Attempting to invoke an `EXTERNAL_TRANSMISSION` tool without a valid consent token returns `AWAITING_CONSENT`.
- [ ] `ReadDocumentStructureTool` accurately extracts headings and region counts from Markdown.
- [ ] Tool catalog serializes cleanly to JSON for inclusion in system planning prompts.
- [ ] AST check confirms zero imports of `subprocess` in `application/services/tool_registry.py`.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_tool_registry.py`:
  - `test_tool_registration_and_lookup()`: Verifies tool allowlist operations.
  - `test_argument_validation()`: Verifies invalid arguments fail before execution.
  - `test_governance_consent_gating()`: Verifies `EXTERNAL_TRANSMISSION` stops at consent gate.
  - `test_read_document_structure_tool()`: Validates extraction of headings on CommonMark fixtures.

### 5.2 Negative & Security Tests
- `test_reject_shell_tool_registration()`: Ensures tools named `shell_exec` or `run_command` are rejected at registration.

### 5.3 Concurrency & Persistence Considerations
- Tool execution is stateless or scoped to `ToolExecutionContext`.

---

## 6. Error and Recovery Behavior

- Exceptions thrown inside `tool.execute()` are caught and converted to `ToolExecutionResult(status="ERROR", error_message=str(e))` to prevent orchestrator thread crashes.

---

## 7. Observability & Audit Requirements

- Records tool execution duration in milliseconds.
- Outputs structured hashes of tool arguments and results for the journal.

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
