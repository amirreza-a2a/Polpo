# TICK-ADE-05: Agent Working Copy Aggregate, Scratchpad Lifecycle & Hash Verification

- **Ticket ID:** `TICK-ADE-05`
- **Milestone:** Milestone B (Working Copy & Patch Architecture)
- **Priority:** P1 (Blocker for Milestone B)
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Blocked By:** `TICK-ADE-01`
- **Authoritative ADR:** ADR-008 (Agent Working Copy, Isolated Patch Lifecycle & OCC Safety)

---

## 1. Objective

Implement the `AgentWorkingCopy` domain aggregate and `AgentWorkingCopyService` in `core/entities/agent_working_copy.py` and `application/services/agent_working_copy_service.py` to provide isolated document scratchpads for candidate testing.

---

## 2. User Value

Guarantees absolute document safety. The AI assistant can test changes, generate candidate PDFs, and analyze diffs in an isolated scratchpad without touching the author's saved files or corrupting document history.

---

## 3. Scope

- Define `WorkingCopyStatus` enum (`DRAFT`, `VALIDATING`, `VALIDATED`, `APPLIED`, `REJECTED`, `STALE`).
- Implement `AgentWorkingCopy` aggregate in `core/entities/agent_working_copy.py`:
  - `working_copy_id: str` (UUID4)
  - `task_id: str`, `job_id: int`
  - `base_canonical_version: int`
  - `base_source_sha256: str`
  - `draft_revision: int`
  - `current_text: str`
  - `staged_patches: List[DocumentPatch]`
  - `metadata_overrides: Dict[str, Any]`
  - `style_profile_id: Optional[str]`
  - `status: WorkingCopyStatus`
  - `created_at: float`, `updated_at: float`
- Implement `AgentWorkingCopyService` in `application/services/agent_working_copy_service.py`:
  - `create_working_copy(task_id: str, job_id: int) -> AgentWorkingCopy`: Loads canonical document text for `job_id`, records SHA-256 and base version, allocates in-memory working copy.
  - `get_working_copy(working_copy_id: str) -> Optional[AgentWorkingCopy]`
  - `update_scratchpad_text(working_copy_id: str, new_text: str) -> None`
  - `discard_working_copy(working_copy_id: str) -> None`: Cleans scratchpad from memory and removes associated ephemeral files.
  - `verify_provenance(working_copy_id: str) -> bool`: Verifies that base canonical version has not advanced in SQLite.

---

## 4. Explicit Non-Goals

- No direct disk writes to canonical document paths (`output_{job_id}_v{N}.md`).
- No mutation of active editor buffers (handled in `TICK-ADE-07`).

---

## 5. Codebase References

- `core/entities/document_version.py` (Canonical version entity).
- `application/services/document_publication_service.py` (Source of canonical document text).
- `application/ports/unit_of_work.py` (`IUnitOfWorkFactory`).

---

## 6. Proposed Files & Components

- Create: `core/entities/agent_working_copy.py`
- Create: `application/services/agent_working_copy_service.py`
- Create: `tests/unit/test_agent_working_copy.py`

---

## 7. Domain & Architectural Constraints

- **Rule 8 & 9 Compliance:** Working copies are strictly temporary and uncommitted; they never bypass OCC or advance version watermarks.
- **Rule 24 Compliance:** Clean, focused diffs with zero extraneous dependencies.

---

## 8. Detailed Implementation Requirements

1. `create_working_copy()` must compute SHA-256 hash using `hashlib.sha256(canonical_text.encode('utf-8')).hexdigest()`.
2. If `verify_provenance()` detects that SQLite's latest canonical version is greater than `base_canonical_version`, it updates status to `STALE` and raises `StaleWorkingCopyError`.
3. Working copy instances are held in a thread-safe in-memory cache with an LRU eviction bound (maximum 20 active working copies).

---

## 9. Acceptance Criteria

- [ ] Creating a working copy correctly captures the active canonical version and source text.
- [ ] Updating scratchpad text updates `current_text` and advances `updated_at`.
- [ ] If canonical version advances in the background, `verify_provenance()` flags the working copy as `STALE`.
- [ ] Discarding a working copy frees its memory and marks status `REJECTED`.
- [ ] Zero writes to SQLite `document_versions` during working copy manipulation.

---

## 10. Required Tests

- Unit tests in `tests/unit/test_agent_working_copy.py` verifying:
  - Working copy creation, cloning, and text updates.
  - SHA-256 integrity calculation.
  - Provenance verification against simulated canonical version advances.

---

## 11. Security Requirements

- Scratchpad buffers reside in process memory; never write unencrypted drafts to global temporary folders (`/tmp/` without job scoping).

---

## 12. Completion Evidence & Follow-up

- Unit test execution evidence passing 100%.
- Unblocks: `TICK-ADE-06`, `TICK-ADE-13`.
