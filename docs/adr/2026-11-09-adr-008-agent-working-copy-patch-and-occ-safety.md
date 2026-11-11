# ADR-008: Agent Working Copy, Editor Draft Snapshot, Patch Architecture, and OCC Safety

- **Status:** Proposed (Awaiting Maintainer Approval)
- **Date:** 2026-11-09 (Revised: 2026-11-11)
- **Scope:** Working Copy Lifecycle, Editor Draft Snapshot, Three-Way Conflict Handling, Document Patch Engine, Stale Revision Detection, Multi-Region Edits, Native Editor Integration, Undo/Redo Integrity, OCC Publication
- **Authoritative Baseline:** Commit `25bb95d198c34c035b5d66513166e06494d82cdb`
- **Related ADRs:** ADR-001 (Three-Way Merge Conflict Resolution), ADR-004 (Artifact Lineage & Canonical Publication), ADR-006 (AI LaTeX Repair), ADR-007 (Agent Orchestration & Tool Governance), ADR-009 (Quarto Diagnostics & PDF Repair)
- **Confirmed Requirements:** D-USER-01 (Unified Assistant Scope), D-USER-02 (Supervised Agent Autonomy), D-USER-03 (Document vs Rendering-System Repair)

---

## 1. Context

Polpo is an embedded, local-first document workspace where canonical document state is governed by immutable, versioned files on disk (`output_{job_id}_v{version}.md`) and Optimistic Concurrency Control (OCC) managed by `DocumentPublicationService` ([ADR-004](docs/adr/2026-10-22-adr-004-pipeline2-artifact-lineage-and-canonical-publication-contract.md)). Simultaneously, the user interacts with an in-memory draft buffer in `MarkdownEditorController`, which tracks local dirty status (`isDirty`) and maintains an active undo/redo stack (`QUndoStack`).

When the Unified AI Assistant investigates a document repair, analyzes technical diagrams, or proposes publication styling (ADR-007), it frequently generates candidate modifications across multiple document regions. If the assistant were to derive its input solely from stale canonical files on disk while the user is actively typing uncommitted changes in the editor, or if it were to mutate the editor buffer while background tasks run, severe data corruption, race conditions, loss of undo history, and OCC save collisions would occur.

---

## 2. Problem Statement

How should Polpo structure the Working Copy, patch application, and concurrency safety models so that:
1. The assistant captures the user's active editor draft at task initiation rather than silently analyzing stale canonical files?
2. The assistant never mutates immutable canonical documents or advances the database version watermark while investigating, planning, or validating changes?
3. The user's active editor buffer and undo/redo history are strictly protected from in-flight agent disruptions?
4. Three-way concurrency conflicts (Canonical Base vs. Editor Draft vs. Agent Working Copy) are detected and resolved deterministically?
5. Disjoint changes can be rebased safely without guessing, while overlapping changes force explicit review?
6. Stable source anchors and context hashes prevent ambiguous replacements when source fragments recur?
7. Final adoption of agent-proposed changes adheres strictly to the existing Optimistic Concurrency Control (OCC) protocol without silent saves?

---

## 3. Architectural Forces & Constraints

1. **Canonical Immutability ([`AGENTS.md`](AGENTS.md) Rules 8 & 9):** Canonical document files are strictly append-only and immutable. Existing version files are never overwritten in-place.
2. **Raw Text Authority ([`CONTEXT.md`](CONTEXT.md) Section 3.4):** Plain text is the single source of truth. CommonMark AST parsing is strictly read-only; AST-to-text re-serialization is forbidden to prevent formatting or whitespace corruption.
3. **OCC Watermark Invariant ([`AGENTS.md`](AGENTS.md) Rule 8):** Commits require `base_version == active_canonical_version`. If a concurrent change advanced the version, `StaleDocumentVersionError` must be raised.
4. **Human In-Buffer Safety ([`CONTEXT.md`](CONTEXT.md) Section 2.2):** Applying external changes must not discard uncommitted human keystrokes without explicit review.
5. **No Silent Commit:** The Agent must never commit changes to canonical history without explicit human approval.
6. **Snapshot Fidelity (Finding C):** The task must capture the exact editor buffer at task creation. Deriving input solely from disk while the editor has uncommitted changes is prohibited.

---

## 4. Confirmed Requirements (Maintainer Settled)

- **D-USER-02 (Supervised Agent Autonomy):** The Agent may create and modify temporary working copies, generate patches, and run local validation workflows. However, applying proposed document changes requires explicit user approval; persistent canonical changes must use existing Editor and OCC workflows; and the Agent must never silently commit changes.

---

## 5. Considered Alternatives

### Alternative A: In-Place Canonical Document Overwrite
- *Description:* Let the agent directly modify `output_{job_id}_v{version}.md` on disk.
- *Rejection Rationale:* Directly violates Polpo's foundational immutability rules. Any crash or rejected proposal leaves disk state corrupt. Completely breaks OCC and audit lineage.

### Alternative B: Direct Editor Buffer Mutation During In-Flight Tasks
- *Description:* Have the agent modify `MarkdownEditorController._source_text` in real-time as tool steps execute.
- *Rejection Rationale:* Disrupts the user's typing focus, corrupts the Qt `QUndoStack`, and causes race conditions if the user edits text concurrently with background agent tool calls.

### Alternative C: Proposed Decision — `TaskInputSnapshot`, Isolated `AgentWorkingCopy`, and Three-Way Conflict-Safe Patch Application
- *Description:* At task initiation, capture an immutable `TaskInputSnapshot` of the active editor buffer. The agent operates strictly on an isolated scratchpad `AgentWorkingCopy`. When candidate changes are validated and approved by the user, a three-way conflict engine verifies the current editor buffer against the snapshot and canonical base, rebasing disjoint hunks or presenting conflicts, and applying changes via an atomic `QTextCursor` edit block.
- *Selection Rationale:* Completely isolates experimental agent work from canonical storage and user typing; guarantees clean stale-revision detection; preserves full undo/redo capability.

---

## 6. Proposed Decision

We propose establishing the **Agent Working Copy, Snapshot, and Patch Architecture** consisting of:
1. An explicit **`TaskInputSnapshot`** model capturing the exact editor state at task initiation.
2. Six clearly distinguished **Document Lifecycle Objects & States** with unambiguous ownership.
3. An isolated **`AgentWorkingCopy`** aggregate in `core/entities/agent_working_copy.py`.
4. A typed **`DocumentPatch` / `PatchSet`** model with stable anchors in `core/entities/document_patch.py`.
5. A deterministic **Three-Way Conflict Handling Engine** in `application/services/document_patch_service.py`.
6. Integration with `MarkdownEditorController` enforcing atomic edit blocks, undo preservation, and OCC save gating.

---

## 7. Detailed Design

### 7.1 Architecture & Entity Boundaries

```text
Core Domain (core/)
 ├── entities/task_input_snapshot.py  ──> TaskInputSnapshot value object
 ├── entities/agent_working_copy.py   ──> Working copy state, hash provenance, validation history
 ├── entities/document_patch.py       ──> Patch, PatchSet, PatchAnchor, PatchOperation
 └── policies/patch_safety_policy.py  ──> Non-overlapping hunk checks, whitespace preservation
Application Layer (application/)
 ├── services/agent_working_copy_service.py  ──> Working copy lifecycle, cloning, scratchpad management
 ├── services/document_patch_service.py     ──> Three-way conflict analysis, patch application, rebase
 └── dto/working_copy_dto.py                ──> DTOs for diff rendering, proposal cards, and candidate text
Presentation Layer (interfaces/desktop/)
 ├── controllers/agent_controller.py        ──> Exposes diff proposals to QML
 └── controllers/markdown_editor_controller.py ──> Captures snapshots and applies approved PatchSets
```

### 7.2 The Six Document Lifecycle States and Objects (Finding C)

To prevent conceptual conflation, six distinct entities are formally defined:

1. **Canonical Document:** The immutable on-disk file (`output_{job_id}_v{version}.md`). Authoritative source of historical truth, managed exclusively by `DocumentPublicationService` under SQLite OCC transactions.
2. **Editor Draft:** The active in-memory plain-text buffer in `MarkdownEditorController._source_text`. It tracks dirty status (`isDirty`) and may contain uncommitted human keystrokes.
3. **Agent Working Copy:** The isolated scratchpad (`AgentWorkingCopy.current_text`) instantiated inside `AgentWorkingCopyService`. Initialized from `TaskInputSnapshot.source_snapshot`, it undergoes candidate patch applications and scratchpad compilation without touching disk or the editor buffer.
4. **Proposed Patch:** The discrete structured difference (`DocumentPatch` / `PatchSet`) computed between the validated Working Copy and the original `TaskInputSnapshot`.
5. **Applied Editor Buffer:** The editor draft buffer following user approval, modified via atomic `QTextCursor.beginEditBlock()` and `endEditBlock()`. Retains dirty state (`isDirty = True`) and full `QUndoStack` history.
6. **Newly Published Canonical Version:** The new on-disk file (`output_{job_id}_v{version+1}.md`) written only when the user deliberately executes Save (`Ctrl+S`) or clicks "Apply and Save", advancing the database watermark under OCC.

### 7.3 Task Input Snapshot Model

At the moment an AI task is initiated, `AgentController` calls `MarkdownEditorController.capture_task_snapshot()` to freeze the active context:

```python
@dataclass(frozen=True)
class TaskInputSnapshot:
    """Immutable snapshot of the editor buffer at the moment an AI task is initiated."""
    document_id: int                    # Job ID
    base_canonical_version: int         # Current canonical version loaded in editor
    base_source_hash: str               # SHA-256 of canonical disk file
    editor_draft_revision: int          # Monotonically incrementing editor buffer revision
    editor_draft_hash: str              # SHA-256 of active editor buffer text
    source_snapshot: str                # Verbatim plain text of editor buffer
    created_at: float
```

The agent task operates against `source_snapshot`. This guarantees that if the user had uncommitted edits in the editor, the assistant analyzes the text the user actually sees, not stale disk content.

### 7.4 The `AgentWorkingCopy` Aggregate

```python
@dataclass
class AgentWorkingCopy:
    """Isolated document scratchpad for autonomous agent investigation and candidate validation."""
    working_copy_id: str                 # UUID4
    task_id: str                        # Parent AgentTask UUID4
    job_id: int                         # Target document job ID
    base_canonical_version: int         # Snapshot canonical version
    snapshot_draft_revision: int        # Snapshot editor draft revision
    snapshot_draft_hash: str            # Snapshot source SHA-256
    current_text: str                   # Active scratchpad Markdown text
    staged_patches: List[DocumentPatch] # Ordered sequence of applied candidate patches
    metadata_overrides: Dict[str, Any]  # Staged front-matter metadata changes
    style_profile_id: Optional[str]     # Assigned PublicationStyleProfile identifier
    status: WorkingCopyStatus           # DRAFT, VALIDATING, VALIDATED, APPLIED, REJECTED, STALE
    created_at: float
    updated_at: float
```

### 7.5 Stable Patch Anchors & Representation

To ensure robustness against line number drift and identical recurring fragments, patches use **stable source anchors** and **context hashes**, not line numbers alone:

```python
class PatchScope(Enum):
    SOURCE_LINE_RANGE = "SOURCE_LINE_RANGE"  # Exact line interval with anchor verification
    RAW_BLOCK_TOKEN = "RAW_BLOCK_TOKEN"      # Specific fenced raw LaTeX or TikZ block
    METADATA_FIELD = "METADATA_FIELD"        # Front-matter YAML key-value adjustment
    STYLE_PROFILE = "STYLE_PROFILE"          # PublicationStyleProfile assignment

@dataclass(frozen=True)
class PatchAnchor:
    """Contextual anchors bounding a patch target for disambiguation and rebase."""
    context_before: str                  # 2 lines immediately preceding target
    target_lines: str                    # Expected verbatim target lines
    context_after: str                   # 2 lines immediately following target
    target_sha256: str                   # SHA-256 of expected target_lines

@dataclass(frozen=True)
class DocumentPatch:
    """Atomic modification unit within an AgentWorkingCopy."""
    patch_id: str
    scope: PatchScope
    target_start_line: int              # 1-indexed start line in snapshot text
    target_end_line: int                # 1-indexed end line in snapshot text
    anchor: PatchAnchor                 # Contextual verification anchor
    original_text: str                  # Expected original text fragment
    replacement_text: str               # Proposed replacement text
    change_summary: str                 # Human-readable rationale (English)
    preserved_invariants: Tuple[str, ...] # Invariant assertions (e.g. "formula_syntax_valid")
```

### 7.6 Three-Way Concurrency & Conflict Handling (Finding C)

```text
Canonical Base (vN)
      |
      +--------------------+
      |                    |
Editor Draft         Agent Working Copy
(User typing)        (Candidate repairs)
      |                    |
      +---------+----------+
                |
         Conflict Analysis (diff3 / AST anchors)
                |
        Patch Review / Rebase
                |
       Explicit User Approval
                |
        Editor Buffer Apply (Undoable)
                |
          Explicit Save (Ctrl+S)
                |
           OCC Publish (vN+1)
```

#### Collision Detection Scenarios:
1. **Editor Unchanged (`editor_draft_hash == snapshot.editor_draft_hash`):**
   The user did not type during agent execution. The patch set applies cleanly to the editor buffer at original line coordinates.
2. **Canonical Version Changed (`active_canonical_version > snapshot.base_canonical_version`):**
   Another process published a new canonical version in the background. Patch application is halted; the user is alerted to reload or trigger a three-way merge before applying agent changes.
3. **Editor Changed — Disjoint Lines (Safe Rebase):**
   The user edited lines 40–45, while the agent patched lines 10–15. The rebase engine uses `anchor.context_before` and `anchor.target_sha256` to locate the target in the current buffer. Because line ranges do not overlap and target anchors match exactly, the patch offsets are safely rebased and applied automatically upon user approval.
4. **Editor Changed — Overlapping Lines (Conflict):**
   The user edited text within or adjacent to the region the agent modified. The engine detects the anchor mismatch or hunk overlap, raises `EditorConflictError`, and displays a split conflict review dialog requiring the user to choose between User Draft, Agent Candidate, or manual merge.
5. **Repeated or Ambiguous Fragments:**
   If `original_text` appears identically in multiple places (e.g. two identical equations `$$E=mc^2$$`), the anchor (`context_before` and `context_after`) disambiguates the target. If the anchor cannot uniquely identify the location, the patch is rejected with `AmbiguousPatchAnchorError` rather than guessing.
6. **Multiple Overlapping Patches in `PatchSet`:**
   If the agent proposes overlapping spans within the same patch set, the set is rejected during pre-application validation.

### 7.7 Final Adoption Sequence & OCC Gating

```mermaid
sequenceDiagram
    participant User as Human User (QML)
    participant AC as AgentController
    participant MEC as MarkdownEditorController
    participant DPS as DocumentPublicationService
    participant DB as SQLite WAL

    User->>AC: Clicks "Apply Changes"
    AC->>MEC: apply_patch_set(patch_set, snapshot)

    alt Editor Has Overlapping Conflicting Edits
        MEC-->>AC: Rejects with EditorConflictError
        AC-->>User: Prompts: "Editor was modified in the same region. Review conflict."
    else Clean or Disjoint Rebase Verified
        Note over MEC: Begins atomic edit block (QTextCursor)
        loop For each patch in PatchSet (bottom-up)
            MEC->>MEC: Replace exact anchored range
        end
        Note over MEC: Ends atomic edit block (QUndoStack preserved!)
        MEC->>MEC: Mark buffer dirty (isDirty = True)
        MEC-->>AC: Applied successfully
        AC-->>User: UI reflects applied changes; undoable via Ctrl+Z

        opt User Chooses Immediate Save
            User->>MEC: Clicks "Save" (Ctrl+S)
            MEC->>DPS: commit_source_text(job_id, base_version, text)
            DPS->>DB: Atomic 4-Phase OCC Publication
            DB-->>DPS: Committed version N+1
            DPS-->>MEC: Active version updated to N+1
        end
    end
```

#### Key Invariants of Adoption:
1. **Undo Stack Preservation:** In `MarkdownEditorController`, patches are applied via `QTextCursor.beginEditBlock()` and `QTextCursor.endEditBlock()`. The entire multi-region patch set forms a single undo action. The user can immediately press `Ctrl+Z` to revert the entire agent proposal cleanly.
2. **Zero Silent Persistence:** Applying a proposal modifies the active plain-text buffer and marks `isDirty = True`, but does NOT write to SQLite or disk automatically.
3. **OCC Gating:** When the document is saved, `DocumentPublicationService.publish_version()` verifies that `base_version == latest_canonical_version`. If a concurrent process advanced the version in the background, `StaleDocumentVersionError` is raised, triggering standard three-way merge conflict resolution ([ADR-001](docs/adr/2026-09-18-adr-001-phase10f5-three-way-merge-conflict-resolution.md)).

---

## 8. Consequences

### Positive:
- **Snapshot Fidelity:** Tasks always execute against the active draft text the author sees, preventing stale canonical regressions.
- **Three-Way Safety:** Clear distinction between Canonical Base, Editor Draft, and Agent Working Copy ensures no keystroke is ever silently overwritten.
- **Flawless Undo Integration:** Authors retain total control. Any applied agent proposal can be instantly undone with standard `Ctrl+Z`.
- **Anchor Disambiguation:** Contextual anchors prevent misapplication when identical tokens recur.

### Negative / Trade-offs:
- **Rebase Overhead:** Checking anchors and rebasing line offsets when the editor is dirty adds a few milliseconds of computation prior to buffer application.
- **Rejection on Overlap:** If the author types in the exact line interval the agent was analyzing, the patch cannot be auto-applied and requires manual review.

---

## 9. Testing & Verification Strategy

- **Snapshot Capture Tests:** Verify that `capture_task_snapshot()` captures dirty buffer text, computing valid SHA-256 and revision metadata.
- **Anchor Disambiguation Tests:** Verify that identical formulas at different document locations map uniquely based on before/after context.
- **Three-Way Conflict Tests:**
  - Verify clean application when editor draft is untouched.
  - Verify safe offset rebase when editor changes disjoint lines.
  - Verify `EditorConflictError` when editor changes overlapping lines.
  - Verify rejection when canonical version advances externally.
- **Editor Undo Tests:** Verify `QTextCursor` edit block allows single `QUndoStack.undo()` to revert entire multi-hunk patch set.
