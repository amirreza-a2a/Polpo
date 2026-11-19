# TICK-ADE-05: Agent Working Copy Aggregate, Editor Draft Snapshot & Scratchpad Lifecycle

- **Ticket ID:** `TICK-ADE-05`
- **Title:** Agent Working Copy Aggregate, Editor Draft Snapshot & Scratchpad Lifecycle
- **Milestone:** Milestone B (Working Copy & Patch Architecture)
- **Priority:** P1 (Blocker for Milestone B)
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Implement the `TaskInputSnapshot` baseline model, `AgentWorkingCopy` domain aggregate, and `AgentWorkingCopyService` in `core/entities/task_input_snapshot.py`, `core/entities/agent_working_copy.py`, and `application/services/agent_working_copy_service.py` to provide isolated document scratchpads.
- **Explicit Non-Goals:**
  - No direct disk writes to canonical document paths (`output_{job_id}_v{N}.md`).
  - No mutation of active editor buffers (handled in `TICK-ADE-07`).
  - No deriving task input solely from disk when editor draft is dirty.
- **Prerequisites:** `TICK-ADE-01` completed; ADR-008 approved.
- **Dependencies (Blocked By):** `TICK-ADE-01`.
- **Unblocks:** `TICK-ADE-06`, `TICK-ADE-13`.

---

## 1. Existing Source-Code Integration Points

- `core/entities/document_version.py`: Canonical version entity and watermark reference.
- `application/services/document_publication_service.py`: Source of canonical document text and OCC verification.
- `interfaces/desktop/controllers/markdown_editor_controller.py`: Active in-memory buffer source for `TaskInputSnapshot`.

---

## 2. Proposed Domain Interfaces & DTOs

```python
@dataclass(frozen=True)
class TaskInputSnapshot:
    """Immutable baseline capturing editor draft text, canonical version, and revisions at task initiation."""
    job_id: int
    base_canonical_version: int
    base_source_hash: str
    editor_draft_revision: int
    editor_draft_hash: str
    source_snapshot: str
    created_at: float

class WorkingCopyStatus(Enum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    STALE = "STALE"

@dataclass
class AgentWorkingCopy:
    working_copy_id: str                 # UUID4
    task_id: str                        # Parent AgentTask UUID4
    job_id: int                         # Target document job ID
    base_canonical_version: int         # Snapshot canonical version
    snapshot_draft_revision: int        # Snapshot editor draft revision
    snapshot_draft_hash: str            # Snapshot source SHA-256
    current_text: str                   # Active scratchpad Markdown text
    staged_patches: List[Any]           # Ordered sequence of applied candidate patches
    metadata_overrides: Dict[str, Any]  # Staged front-matter metadata changes
    style_profile_id: Optional[str]     # Assigned PublicationStyleProfile identifier
    status: WorkingCopyStatus
    created_at: float
    updated_at: float
```

---

## 3. Detailed Implementation Requirements

1. **Snapshot Integration (Finding C):**
   - `create_working_copy(task_id: str, snapshot: TaskInputSnapshot) -> AgentWorkingCopy`: Initializes `current_text` from `snapshot.source_snapshot`, capturing the active editor draft text rather than stale disk content.
   - Computes SHA-256 integrity hash of `current_text`.
2. **Provenance & Stale Verification:**
   - `verify_provenance(working_copy_id: str) -> bool`: Checks SQLite to verify whether canonical document version has advanced ($N \to N+1$) during task execution. If advanced, marks status `STALE`.
3. **In-Memory Scratchpad Management:**
   - Working copy instances are maintained in a thread-safe LRU cache (capacity: 20 active copies).
   - `discard_working_copy(working_copy_id: str)`: Cleans working copy from RAM and purges associated ephemeral files.
4. **Lifecycle Separation:**
   - Enforces strict isolation between Canonical Document (disk), Editor Draft (editor RAM), and Agent Working Copy (scratchpad RAM).

---

## 4. Acceptance Criteria

- [ ] Creating a working copy from `TaskInputSnapshot` initializes `current_text` with the active editor text.
- [ ] If editor has uncommitted changes, working copy contains those changes (not stale disk text).
- [ ] Updating scratchpad text updates `current_text` and advances `updated_at`.
- [ ] If canonical version advances in the background, `verify_provenance()` marks working copy as `STALE`.
- [ ] Discarding a working copy cleans scratchpad RAM and sets status `REJECTED`.
- [ ] Zero writes to SQLite `document_versions` during working copy manipulation.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_agent_working_copy.py`:
  - `test_working_copy_creation_from_snapshot()`: Verifies snapshot binding.
  - `test_dirty_editor_draft_captured_accurately()`: Confirms uncommitted draft is used.
  - `test_scratchpad_text_mutation()`: Verifies in-memory updates.
  - `test_provenance_stale_detection()`: Simulates background canonical version increment.

### 5.2 Negative & Security Tests
- `test_scratchpad_isolation_from_disk()`: Asserts no disk file is created under canonical output directory.

### 5.3 Concurrency & Persistence Considerations
- Working copy operations are protected by an internal lock; memory bounded by LRU cache.

---

## 6. Error and Recovery Behavior

- Stale working copies raise `StaleWorkingCopyError` if modification is attempted after canonical advancement.
- Application restart purges ephemeral working copies from memory.

---

## 7. Observability & Audit Requirements

- Records creation and discard events in logs with working copy ID and snapshot revision.

---

## 8. Documentation Updates

- Document 6 lifecycle states in ADR-008.

---

## 9. Required Project Skills

- `domain-modeling`: Working copy and snapshot boundaries.
- `tdd`: Test-first implementation of working copy service.

---

## 10. Verification Commands

```bash
pytest tests/unit/test_agent_working_copy.py -v
python3 -c "import ast; tree = ast.parse(open('core/entities/agent_working_copy.py').read()); assert not any('PySide6' in ast.dump(n) or 'sqlite3' in ast.dump(n) for n in ast.walk(tree))"
```

---

## 11. Definition of Done

- All unit tests pass with 100% coverage.
- Snapshot baseline and scratchpad lifecycle fully operational.
