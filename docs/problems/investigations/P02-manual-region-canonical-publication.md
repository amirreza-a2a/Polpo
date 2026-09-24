# Investigation Record — P02: Manual Visual Region Does Not Reach Canonical Markdown

- **Problem ID:** P02
- **Title:** Creating a manual visual region does not publish/insert it into Markdown
- **Status:** READY FOR TICKETING
- **Date:** 2026-09-24
- **Repository:** `amirreza-a2a/Polpo`
- **Base Commit:** `e98a2c0` (`main`)
- **Authoritative Publication Gateway:** [`DocumentPublicationService`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/document_publication_service.py)

---

## 1. Executive Summary

In PolpoT's desktop Review Workspace, users can draw a manual bounding box on a PDF page in `DocumentViewerView.qml` to capture unextracted diagrams, tables, or figures. While the bounding box geometry is successfully persisted into the local SQLite `visual_regions` table via `DocumentViewerService.create_manual_region()`, the newly created region **never reaches the canonical Markdown document**. No cropped image artifact is promoted, no Markdown image token is inserted into the document text, no new canonical document version is published, and the rendered preview remains completely unchanged.

Investigation confirms that the root cause is **not** a minor wiring glitch or an accidentally omitted parameter. Rather, it represents an intentional architectural quarantine from Phase 10E.3a: the legacy `ApplyReviewService` was decommissioned and disconnected in `interfaces/desktop/app.py:238` because it bypassed the authoritative 4-phase crash-consistent [`DocumentPublicationService`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/document_publication_service.py), bypassed isolated crop staging, and utilized legacy Obsidian-style `![[...]]` wiki syntax. The replacement application use case to bridge visual region lifecycle events to the canonical publication gateway was deferred (documented in `DEBT-10E-03`).

This investigation establishes the definitive architecture to complete the manual region review loop without violating Clean Architecture, without resurrecting legacy direct writers, and without compromising human-authored Markdown edits.

---

## 2. Observed Behavior

1. User switches the PDF viewer interaction mode to `create_region` in [`BoundingBoxOverlay.qml`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/qml/components/BoundingBoxOverlay.qml#L130-L133).
2. User drags a rubber-band rectangle over an unextracted figure on page $P$ and releases the mouse.
3. QML invokes [`DocumentViewerController.commitCreateManual()`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/controllers/document_viewer_controller.py#L1253-L1278).
4. [`DocumentViewerService.create_manual_region()`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/document_viewer_service.py#L265-L293) assigns a new canonical UUID4 `region_id`, creates a `VisualRegion` with `origin=USER_MANUAL`, `review_status=MANUAL`, `sync_status=PENDING_INITIAL_CROP`, and persists it to SQLite.
5. The controller calls `self._trigger_async_apply(job_id, region_id)`.
6. **Failure point:** `_trigger_async_apply()` checks `if not self.apply_review_service: return` and terminates immediately.
7. **Result:**
   - No image crop is extracted or staged on disk.
   - The canonical Markdown file `output_{job_id}_v{N}.md` is not updated.
   - The rendered preview pane shows no new image.
   - The region remains permanently in `sync_status=PENDING_INITIAL_CROP` with `active_artifact_version=0` and `active_artifact_uri=None`.

---

## 3. Evidence

### 3.1 Controller Wiring in `interfaces/desktop/app.py`
[`interfaces/desktop/app.py:236-239`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/app.py#L236-L239):
```python
document_viewer_controller = DocumentViewerController(
    viewer_service=container.document_viewer_service,
    apply_review_service=None,  # Canonical write path disabled until 10E.3b
)
```

### 3.2 Controller Guard in `interfaces/desktop/controllers/document_viewer_controller.py`
[`interfaces/desktop/controllers/document_viewer_controller.py:400-406`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/controllers/document_viewer_controller.py#L400-L406):
```python
def _trigger_async_apply(self, job_id: int, region_id: str) -> None:
    if not self.apply_review_service or not region_id:
        return
```

### 3.3 Quarantine Notice in `application/services/apply_review_service.py`
[`application/services/apply_review_service.py:30-37`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/apply_review_service.py#L30-L37):
```python
class ApplyReviewService:
    """
    [QUARANTINED LEGACY SERVICE EXCEPTION]
    This service contains legacy direct canonical Markdown writing logic and is quarantined
    from runtime execution in Phase 10E.3a. It is omitted from active desktop presentation
    wiring in app.py:237.
    Formal replacement/removal scheduled for Phase 10E.3b.
```

### 3.4 Architecture Invariants Enforced in Test Suite
[`tests/unit/test_phase10e3a_architecture_invariants.py:89-166`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/tests/unit/test_phase10e3a_architecture_invariants.py#L89-L166):
- `test_ast_sole_active_runtime_publisher`: Enforces that `DocumentPublicationService` is the sole runtime canonical Markdown publisher, that `DocumentViewerController` is instantiated with `apply_review_service=None`, and that `DesktopAppContainer` does NOT instantiate `ApplyReviewService`.
- `test_ast_composition_container_omits_quarantined_apply_review_service`: Statically inspects AST of `interfaces/desktop/composition.py` to assert that `ApplyReviewService` is neither imported nor called.
- `test_ast_apply_review_quarantined_exception`: Statically asserts that `ApplyReviewService` is quarantined and contains explicit documentation.

---

## 4. Current-State vs. Target Architecture

### 4.1 Current Flow (Broken / Quarantined)
```text
QML BoundingBoxOverlay
    │  (mouse released in create_region mode)
    ▼
DocumentViewerController.commitCreateManual()
    │
    ├──> DocumentViewerService.create_manual_region()
    │       │
    │       └──> SQLite visual_regions (persisted: PENDING_INITIAL_CROP)
    │
    └──> DocumentViewerController._trigger_async_apply()
            │
            └──> self.apply_review_service is None
                    │
                    ▼ [TERMINATES — NO CROP, NO MARKDOWN, NO PUBLICATION]
```

### 4.2 Target Flow (Clean Architecture via Publication Gateway)
```text
QML BoundingBoxOverlay
    │  (mouse released in create_region mode)
    ▼
DocumentViewerController.commitCreateManual()
    │
    ├──> DocumentViewerService.create_manual_region()
    │       │
    │       └──> SQLite visual_regions (persisted: PENDING_INITIAL_CROP)
    │
    └──> DocumentViewerController._trigger_async_apply()
            │
            └──> Dispatches to Background Worker (QThreadPool / ThreadPoolExecutor)
                    │
                    ▼
         VisualRegionPublicationService.publish_region_review(job_id, region_id)
            │
            ├── 1. Load active Job & VisualRegion from SQLite
            │
            ├── 2. Extract & Stage Crop Artifact
            │       │
            │       ├── IDocumentProcessor.render_page_to_jpeg()
            │       ├── IDocumentProcessor.crop_region_image()
            │       └── CropArtifactStagingService.stage_crop()
            │               --> .staging/{staging_id}/crop_{region_id}_v{version}.jpg
            │               --> returns StagedCropHandle
            │
            ├── 3. Mutate Markdown Text (Format-Preserving)
            │       │
            │       └── VisualTokenMutator.insert_visual_token(canonical_text, token, page)
            │               --> canonical format: ![alt](crop.jpg "polpo:region=...;occ=...")
            │               --> zero AST re-serialization; leaves human edits intact
            │
            ├── 4. Publish Version via Sole Authoritative Gateway
            │       │
            │       └── DocumentPublicationService.publish_version(
            │               job_id, base_version, mutated_text, staged_crops=[handle]
            │           )
            │               --> Phase 1: Intent reservation in SQLite
            │               --> Phase 2: Promote staged crop to job_{job_id}/ & flush temp MD
            │               --> Phase 3: Atomic disk activation (os.replace)
            │               --> Phase 4: Atomic SQLite update (job.output_path, document_versions)
            │
            ├── 5. Update Region Status in SQLite
            │       │
            │       └── sync_status = SYNCED, active_artifact_uri = uri, active_version = v
            │
            └── 6. Emit regionArtifactCommitted Signal to GUI Thread
                    │
                    ├──> MarkdownViewerController.updateRegionArtifact()
                    │       └── in-place image reload & preview model update
                    │
                    └──> MarkdownEditorController (if dirty)
                            └── triggers MarkdownMergeService.analyze_three_way_merge()
                                    --> non-overlapping auto-merge into local draft
```

---

## 5. Confirmed Root Cause

1. **Quarantine Without Replacement:** When Phase 10E.3a established [`DocumentPublicationService`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/document_publication_service.py) as the sole crash-consistent publication authority and introduced [`CropArtifactStagingService`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/crop_artifact_staging_service.py) for uncommitted crop staging, the old `ApplyReviewService` was correctly quarantined and disconnected from runtime wiring. However, the replacement orchestrator connecting the presentation controller to `CropArtifactStagingService` and `DocumentPublicationService` was never implemented.
2. **Missing Canonical Token Mutator:** `ApplyReviewService` relied on regex targeting legacy Obsidian-style `![[...]]` wiki-links and sliced `page_{p}.md` files. Phase 12 standardized canonical CommonMark visual tokens:
   `![alt](uri "polpo:region=<region_uuid>;occ=<occ_uuid>")`
   No pure, format-preserving Markdown mutator existed to insert or update these canonical tokens into unified raw Markdown text without damaging surrounding text, comments, or formulas.
3. **Controller Interface Inertia:** `DocumentViewerController` retained a dependency on the quarantined `ApplyReviewService` rather than a modern application service.

---

## 6. Open Ambiguities & Grilling Decisions

| Grilling Question | Finding from Codebase / Architecture | Settled Decision |
| :--- | :--- | :--- |
| **What does a manual region mean?** | Defined in [`core/entities/visual_region.py`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/core/entities/visual_region.py): `origin=USER_MANUAL`, `detected_bbox=None`, `reviewed_bbox=bbox`. It represents an unextracted visual diagram or table on a specific PDF page. | Manual region captures user-drawn bounding boxes. Cannot be "reset to AI". |
| **Should creating a manual region immediately publish to Markdown?** | User intent in drawing a region on the PDF is to incorporate that visual element into the document. `DEBT-10E-03` and `P02` explicitly identify the absence of this as a blocking defect. | **YES.** Creation triggers asynchronous crop extraction, staging, Markdown token insertion, and canonical publication in the background. |
| **Is region creation and publication one command or two?** | User interaction is a single gesture: release mouse to create. The application layer coordinates this as two phases: (1) immediate synchronous SQLite persistence of the region entity, and (2) asynchronous background publication pipeline. | **Single user gesture; two-phase application execution.** Persistence is immediate; cropping/publication is asynchronous off the GUI thread. |
| **Which component owns Markdown token mutation?** | `DocumentPublicationService` is text-agnostic (handles bytes, hashes, intents). `DocumentViewerService` manages PDF viewer state. AST re-serialization is forbidden by ADR-001 D02. | Dedicated pure domain component: [`core/markdown/visual_token_mutator.py`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/core/markdown/visual_token_mutator.py). Operates purely on raw Markdown text. |
| **Which component owns crop staging?** | [`CropArtifactStagingService`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/crop_artifact_staging_service.py) already exists in `application/services/`. It writes isolated files in `.staging/{staging_id}/`. | `CropArtifactStagingService` holds candidate crops until publication atomically promotes them. |
| **Which component owns canonical publication?** | [`DocumentPublicationService`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/document_publication_service.py) is the sole authoritative gateway. It already implements atomic promotion of `staged_crops`. | `DocumentPublicationService.publish_version(...)` exclusively owns canonical publication. Zero duplicate writers permitted. |
| **How are human-authored Markdown edits preserved?** | ADR-001 forbids AST re-serialization. Token mutation must be localized string insertion/replacement. Concurrently dirty editor drafts use three-way merge (`diff3`). | Format-preserving string manipulation only. Surrounding text, whitespace, comments, and formulas remain byte-for-byte intact. |
| **What if Markdown changed concurrently?** | `DocumentPublicationService` enforces OCC against `document_versions`. If `latest_ver.version != base_version`, it raises `StaleDocumentVersionError`. | The orchestrator catches `StaleDocumentVersionError`, fetches latest canonical text, reapplies token mutation, and retries (bounded to 3 attempts). |
| **How do region review statuses differ?** | `USER_MANUAL` has no AI bbox. `MODIFIED` updates existing bbox. `REJECTED` deletes token from text and clears active crop URI. `ACCEPTED` approves region. | Statuses preserve full lifecycle. Rejection removes the token from text but preserves audit record in SQLite. |
| **"Persisted" vs "Published" distinction?** | SQLite `visual_regions` table holds region coordinates and lifecycle status. `jobs.output_path` and `document_versions` hold the published document. | **Persisted:** Region row in SQLite. **Published:** Promoted crop on disk, token in Markdown, version incremented in SQLite. |
| **Failure/recovery semantics?** | If cropping fails, region transitions to `SYNC_FAILED`; document is untouched. If publication fails, 4-phase intent rollback cleans tmp files. | Atomic all-or-nothing: document and crop promote together or neither does. |

---

## 7. Explicit Decision Matrix

| Responsibility | Current Owner | Intended Owner | Architectural Decision |
| :--- | :--- | :--- | :--- |
| **Region Drawing & Interaction** | `BoundingBoxOverlay.qml` + `DocumentViewerController` | `BoundingBoxOverlay.qml` + `DocumentViewerController` | **Retain.** QML mouse handling and transient rubber-band preview in presentation layer remain unchanged. |
| **Region Coordinate Persistence** | `DocumentViewerService` | `DocumentViewerService` | **Retain.** Synchronous SQLite persistence on mouse release preserves user bounding box even if subsequent background cropping fails. |
| **Page Rendering & Cropping** | Quarantined `ApplyReviewService` (direct) | `PyMuPDFDocumentProcessor` (`IDocumentProcessor`) | **Re-delegate.** Pure document processor renders page JPEG and crops bounding box. |
| **Crop Artifact Staging** | Quarantined `ApplyReviewService` (direct storage) | `CropArtifactStagingService` | **Adopt.** Uncommitted crops are staged in isolated `.staging/{staging_id}/` directories with SHA-256 hashes. |
| **Markdown Token Mutation** | Quarantined `ApplyReviewService` (regex on `page_{p}.md`) | `core/markdown/visual_token_mutator.py` | **Create pure component.** Format-preserving raw text mutator replacing/inserting canonical CommonMark tokens `![alt](uri "polpo:region=...;occ=...")`. |
| **Canonical Publication & Promotion** | Quarantined `ApplyReviewService` (direct storage/SQLite) | `DocumentPublicationService` | **Adopt sole gateway.** `DocumentPublicationService.publish_version(staged_crops=[...])` promotes crops and commits version atomically. |
| **Version / OCC Enforcement** | None in visual regions path | `DocumentPublicationService` + bounded retry | **Enforce.** `publish_version` checks `base_version`. Bounded retry (3 attempts) on `StaleDocumentVersionError`. |
| **Review Orchestration** | Quarantined `ApplyReviewService` | `VisualRegionPublicationService` | **Create Application Service.** Coordinates processor, staging, token mutator, and publication gateway. |
| **UI Model & Editor Refresh** | `app.py:wire_review_workspace_sync` | `app.py:wire_review_workspace_sync` | **Retain.** Signal `regionArtifactCommitted` updates preview model in-place; notifies editor for non-overlapping three-way merge. |

---
## 8. Architectural Design: `VisualRegionPublicationService`

### 8.1 Location and Seam
- **Module:** `application/services/visual_region_publication_service.py`
- **Port Dependencies:**
  - `uow_factory: IUnitOfWorkFactory`
  - `doc_processor: IDocumentProcessor`
  - `staging_service: CropArtifactStagingService`
  - `publication_service: DocumentPublicationService`
  - `event_publisher: Optional[IApplicationEventPublisher] = None`
- **Zero Framework Coupling:** No imports from PySide6, Qt, QML, or SQLite drivers. Pure Python application layer.

### 8.2 Public Contract
```python
class VisualRegionPublicationService:
    def publish_region_review(
        self,
        job_id: int,
        region_id: str,
    ) -> RegionPublicationResultDTO:
        """
        Orchestrates the complete lifecycle of applying a visual region review:
        1. Validates region and job existence in SQLite.
        2. Captures snapshot of region geometry (snapshot_bbox, snapshot_updated_at).
        3. If active (not rejected), renders and crops the bounding box, then stages the crop.
        4. Re-validates snapshot geometry against database; aborts if superseded by concurrent user edits.
        5. Loads the latest canonical Markdown source text and active document version.
        6. Mutates the Markdown text to insert, update, or remove the visual occurrence token.
        7. Atomically publishes the updated document version via DocumentPublicationService.
        8. Updates the VisualRegion record in SQLite (active_artifact_version, active_artifact_uri, sync_status).
        9. Returns a structured DTO with new_version, new_artifact_uri, and success status.
        """
```

---

## 9. State-Transition Model & Crash Recovery

### 9.1 Entity State Invariants (`core/entities/visual_region.py`)

```text
                     [Draw manual bbox]
                             │
                             ▼
                    origin: USER_MANUAL
                   review_status: MANUAL
             sync_status: PENDING_INITIAL_CROP
                active_artifact_version: 0
                  active_artifact_uri: None
                             │
                             ▼ [publish_region_review begins]
                  (doc_processor crops image)
                 (staging_service stages crop)
                             │
                             ├─── [Crop/Staging Fails] ───► sync_status: SYNC_FAILED
                             ├─── [BBox changed concurrently] ─► Abort stale worker
                             │
                             ▼ [Token mutated & publish_version succeeds]
                    origin: USER_MANUAL
                   review_status: MANUAL
                    sync_status: SYNCED
                active_artifact_version: 1
          active_artifact_uri: file:///.../crop_..._v1.jpg
                             │
            ┌────────────────┴────────────────┐
            │ [User resizes box]              │ [User deletes region]
            ▼                                 ▼
   review_status: MANUAL             review_status: REJECTED
sync_status: DIRTY_RECROP_REQUIRED  sync_status: DIRTY_RECROP_REQUIRED
            │                                 │
            ▼ [publish_region_review]         ▼ [publish_region_review]
   review_status: MANUAL             review_status: REJECTED
    sync_status: SYNCED               sync_status: SYNCED
active_artifact_version: 2          active_artifact_version: 2
active_artifact_uri: ..._v2.jpg     active_artifact_uri: None (token stripped)
```

### 9.2 Publication Atomicity vs. Entity State Atomicity
`DocumentPublicationService.publish_version()` maintains its own 4-phase transaction boundary (Phase 1 intent insert, Phase 4 atomic commit of `jobs.output_path`, `document_versions`, and intent deletion). The subsequent `VisualRegion` update is executed in a scoped UnitOfWork transaction.

**Crash Recovery & Idempotent Reconciliation:**
If a process crashes after `publish_version()` completes Phase 4 but before `VisualRegion` is updated to `SYNCED`:
1. `document_versions` is at $N+1$, `jobs.output_path` points to `output_{job_id}_v{N+1}.md`, and the promoted crop exists in `job_{job_id}/`.
2. `VisualRegion` remains in `sync_status = PENDING_INITIAL_CROP` or `DIRTY_RECROP_REQUIRED`.
3. **Reconciliation Invariant:** When `VisualRegionPublicationService.reconcile_region_sync()` is invoked (or during startup reconciliation), it narrows verification strictly to verifiable repository facts:
   - The region identity (`region_id`) is valid in SQLite.
   - The active canonical document contains a valid canonical visual token for this `region_id`.
   - The token's artifact URI matches `VisualRegion.active_artifact_uri` (with `active_artifact_version > 0`).
   - The artifact file referenced by `active_artifact_uri` actually exists on disk in the job directory.
   If all four facts hold, the service reconciles `VisualRegion` to `SyncStatus.SYNCED` without re-running crop extraction or publishing a duplicate document version. The service does not make unprovable assertions about pixel-coordinate matches without tracked provenance.

---

## 10. Version / OCC Semantics & Concurrency Protection

### 10.1 Document Version vs. Artifact Version Independence
The system strictly separates document-level versioning from region-level artifact versioning:
- **`document_version` (Integer, Monotonic Document-Wide):**
  Tracked in `document_versions` table and `jobs.output_path`. Increments every time *any* part of the canonical Markdown document changes (human text edits, region insertions, region resizes, AI pipeline passes).
- **`active_artifact_version` (Integer, Monotonic Per-Region):**
  Tracked on `VisualRegion` entity and used for filenames (`crop_{region_id}_v{artifact_version}.jpg`). Increments *only* when this specific visual region's image crop is extracted or re-cropped.
- **Rule:** `document_version` $\ne$ `region.active_artifact_version`. A document at version 12 may contain Region A at `artifact_version=1` and Region B at `artifact_version=3`.

### 10.2 Document-Level OCC Retry Policy
1. Service reads latest `DocumentVersionRecord` (`base_version = latest_doc.version`).
2. Calls `DocumentPublicationService.publish_version(job_id, base_version, mutated_text, ...)`.
3. If concurrent edit advanced document version, `publish_version` raises `StaleDocumentVersionError(base_version, latest_version)`.
4. **Retry Loop:** Service re-fetches latest canonical text, reapplies token mutation, and retries up to **3 attempts** before reporting failure.

### 10.3 Region Mutation Concurrency & Worker Staleness Guard
Document-level OCC alone does not protect against race conditions where a slow worker crops bounding box $B_1$ after the user has already resized the region to $B_2$ or deleted it.
- **Snapshot Guard Invariant:** When `VisualRegionPublicationService` begins processing, it captures `snapshot_bbox = region.reviewed_bbox` and `snapshot_updated_at = region.updated_at`.
- **Pre-Publication Check:** Immediately before staging and publication, the service re-reads the region from SQLite within a UnitOfWork. If `current_region.reviewed_bbox != snapshot_bbox` or `current_region.updated_at != snapshot_updated_at`:
  - The worker recognizes that its crop was superseded by a newer user edit.
  - The worker aborts execution immediately without publishing stale artifacts or tokens.

---

## 11. Markdown Insertion & Preservation Semantics

### 11.1 Canonical Token Format & UUID Normalization
Standardized in [`core/domain/visual_token.py`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/core/domain/visual_token.py):
```markdown
![<alt_text>](<relative_or_file_uri> "polpo:region=<region_uuid>;occ=<occurrence_uuid>")
```
- **UUID Format in Canonical Tokens:** Lowercase 36-character hyphenated UUIDv4 (as enforced by `core/domain/visual_token.py:validate_token_uuid`).
- **Comparison Normalization:** Target `region_id` (whether passed as 32-hex `val_uuid.hex` from `VisualRegion` or hyphenated 36-char) is parsed to `uuid.UUID` for comparisons:
  - Canonical token metadata is matched by comparing parsed `UUID(title_region) == target_uuid`.
  - Legacy tokens (`![[...]]`) are matched if the target contains `target_uuid.hex.lower()` or `str(target_uuid).lower()`.
- **Text Safety:** Comparison normalization is purely internal to the scanner. Document text and unrelated UUIDs are never altered.

### 11.2 Strict Byte-Preserving Mutation Invariant
Mutation operates on raw Markdown text. Outside the exact character span of the inserted, replaced, or deleted token line:
```text
mutated_text[:mutation_start] == original_text[:mutation_start]
mutated_text[mutation_end:]   == original_text[orig_mutation_end:]
```
- **No Global Whitespace Normalization:** Surrounding blank lines, indentation, tabs, and line breaks are preserved exactly as authored.
- **No AST Re-serialization:** Never parse into an AST tree and serialize back to text.

### 11.3 Lexical Safety & Scanner State Machine
The token mutator uses a syntax-aware state machine (consistent with `infrastructure/markdown/legacy_normalizer.py`):
1. **Fenced Code Isolation:** Tracks code fence blocks (`` ``` `` and `~~~`). Any `<!-- Page N -->` comments or token-like strings inside fenced code blocks or inline backticks are treated as inert text, never as structural page markers or managed visual tokens.
2. **Ambiguity Resolution:**
   - **0 matching tokens:**
     - For `insert`: inserts token at designated page location.
     - For `update`: falls back to `insert` (handles unlinked regions).
     - For `remove`: returns original text unchanged (idempotent delete).
   - **1 matching token:** Replaces or removes exactly that token span.
   - **> 1 matching tokens:** Raises explicit `AmbiguousVisualTokenError`. Duplicate canonical tokens for the same `region_id` are never silently or arbitrarily resolved.

### 11.4 Legacy Token Migration Safety
- **Separation of Concerns:** The pure core mutator (`VisualTokenMutator`) has zero database or job metadata knowledge. Any lookup required to associate a legacy filename convention `crop_{job_id}_p{page}_{order}.jpg` with a `VisualRegion` is performed outside the mutator by the caller (`VisualRegionPublicationService`), which passes the resolved filename via `legacy_target: Optional[str] = None`.
- **Mutator Rules:**
  1. If a legacy token `![[...]]` target explicitly contains the region's 32-hex or 36-hyphenated UUID, it is migrated to canonical format.
  2. If `legacy_target` is provided by the caller and matches the legacy token target, it is migrated to canonical format.
  3. No fuzzy matching or heuristic guessing on arbitrary image names.

### 11.5 Deterministic Placement Semantics
1. **Target Page Marker Exists:** Search for structural `<!-- Page {page_number} -->` (outside code blocks). The token is inserted **immediately following the `<!-- Page {page_number} -->` line** (after its terminating newline as `{new_token}\n`).
2. **Target Page Marker Missing:** (User deleted HTML comments): Append `\n\n{new_token}\n` cleanly to the end of the document.

---

## 12. Proposed Implementation Ticket Decomposition

```text
TICK-P02A: Pure Canonical Markdown Visual Token Mutator
    │
    ▼
TICK-P02B: Visual Region Publication Application Service
    │
    ▼
TICK-P02C: Desktop Presentation Wiring & Quarantined Service Retirement
    │
    ▼
TICK-P02D: End-to-End Review Workspace Integration & Concurrency Tests
```

### Ticket 1: `TICK-P02A` — Pure Canonical Markdown Visual Token Mutator
- **Scope:** `core/markdown/visual_token_mutator.py`, `tests/unit/test_visual_token_mutator.py`
- **Dependencies:** None
- **Responsibilities:**
  - Pure, format-preserving functions:
    - `insert_visual_token(markdown_text: str, token: VisualOccurrenceToken, page_number: int) -> str`
    - `update_visual_token(markdown_text: str, region_id: UUID, new_uri: str) -> str`
    - `remove_visual_token(markdown_text: str, region_id: UUID) -> str`
  - In-place replacement of existing canonical tokens and legacy `![[...]]` tokens.
  - Page-marker placement (`<!-- Page N -->`) and graceful end-of-document fallback.
  - Unit tests covering: ASCII, RTL Persian, math blocks, fenced code blocks, missing page markers, multiple regions per page, and idempotent updates.
- **Non-Goals:** No file I/O, no database access, no Qt imports.

### Ticket 2: `TICK-P02B` — Visual Region Publication Application Service
- **Scope:** `application/services/visual_region_publication_service.py`, `tests/unit/test_visual_region_publication_service.py`
- **Dependencies:** `TICK-P02A`
- **Responsibilities:**
  - Implements `VisualRegionPublicationService`:
    - Page rendering & bbox cropping via `doc_processor`.
    - Staging candidate crops via `CropArtifactStagingService`.
    - Mutating raw Markdown via `VisualTokenMutator`.
    - Publishing document versions via `DocumentPublicationService.publish_version(...)`.
    - Managing `VisualRegion` SQLite state transitions (`sync_status=SYNCED`, `active_artifact_uri`, `active_artifact_version`).
    - Bounded OCC retry on `StaleDocumentVersionError`.
  - Comprehensive unit tests with mocked storage, processor, and publication gateway.
- **Non-Goals:** No direct UI interaction; no direct file writes bypassing `DocumentPublicationService`.

### Ticket 3: `TICK-P02C` — Desktop Presentation Wiring & Quarantined Service Retirement
- **Scope:** `interfaces/desktop/composition.py`, `interfaces/desktop/app.py`, `interfaces/desktop/controllers/document_viewer_controller.py`, `tests/unit/test_phase10e3a_architecture_invariants.py`
- **Dependencies:** `TICK-P02B`
- **Responsibilities:**
  - Instantiate `VisualRegionPublicationService` in `DesktopAppContainer` (`composition.py`).
  - Update `DocumentViewerController.__init__`: replace `apply_review_service: Optional[ApplyReviewService]` with `region_publication_service: Optional[VisualRegionPublicationService]`.
  - Wire `DocumentViewerController._trigger_async_apply()` to dispatch `region_publication_service.publish_region_review()` in background executor.
  - Wire completion signals to trigger `updateRegionArtifact` and structural reconciliation.
  - Formally decommission and delete quarantined `application/services/apply_review_service.py`.
  - Update architecture invariant tests (`test_phase10e3a_architecture_invariants.py`) to assert zero references to `ApplyReviewService`.
- **Non-Goals:** No QML visual redesign; no context menu changes (P04).

### Ticket 4: `TICK-P02D` — End-to-End Review Workspace Integration & Concurrency Tests
- **Scope:** `tests/integration/test_manual_region_publication_workflow.py`
- **Dependencies:** `TICK-P02C`
- **Responsibilities:**
  - Integration tests verifying full workflow:
    - User creates manual region in PDF viewer -> crop staged -> token inserted -> published to SQLite -> preview reflects new crop.
    - User resizes existing region -> crop updated -> token URI updated -> version incremented.
    - User deletes region -> token removed -> document updated.
    - Concurrent edit scenario: dirty Markdown editor draft auto-merges the external region advance seamlessly using `MarkdownMergeService` without losing human text.
- **Non-Goals:** Manual testing only; must be 100% automated pytest suite.

---

## 13. Acceptance Criteria for Future Implementation

### Persistence
- [ ] A manual visual region created in the PDF viewer is durably stored in SQLite `visual_regions` table with `origin=USER_MANUAL`, `review_status=MANUAL`, and valid `reviewed_bbox`.

### Crop Artifact
- [ ] The region image crop is rendered from the source PDF and staged in `.staging/{staging_id}/` with a valid SHA-256 digest before publication.
- [ ] Upon publication, the crop artifact is promoted into the job's artifact directory `job_{job_id}/crop_{region_id}_v{N}.jpg` and removed from staging.

### Markdown Token
- [ ] Exactly one canonical visual occurrence token `![alt](crop_{region_id}_v{N}.jpg "polpo:region={region_id};occ={occ_id}")` is inserted into the canonical Markdown text.
- [ ] If `<!-- Page N -->` exists, the token is placed within that page's text section before the next page marker.
- [ ] If no page markers exist, the token is appended cleanly to the end of the document.

### Text Preservation
- [ ] All existing human-authored text, YAML headers, HTML comments, and LaTeX math formulas (`$...$`, `$$...$$`) remain completely unaltered.
- [ ] AST parsing is never used for document serialization.

### Publication Authority & OCC
- [ ] Document version increments monotonically via `DocumentPublicationService.publish_version()`.
- [ ] `jobs.output_path` points to the new immutable `output_{job_id}_v{N}.md`.
- [ ] A new `DocumentVersionRecord` is recorded in `document_versions` table.
- [ ] If a concurrent edit advances the document version during processing, the service retries up to 3 times before reporting an error.

### UI Synchronization
- [ ] After successful publication, `regionArtifactCommitted` updates the preview model in-place without requiring full document reload.
- [ ] If the Markdown editor has an uncommitted draft, the external advance triggers non-overlapping three-way merge (`MarkdownMergeService`) automatically.

---

## 14. Explicit Non-Goals

1. **No Context Menu Implementation:** Right-click context menus (`P04`) and "Insert into Markdown at Cursor" are separate follow-up tickets.
2. **No QML Canvas Redesign:** Mouse handling and rubber-band drawing in `BoundingBoxOverlay.qml` are functional and out of scope.
3. **No AST Serialization:** We will not use Markdown AST writers or serializers to write the document text.
4. **No Server or HTTP Endpoints:** Desktop-first, local-first, serverless architecture is strictly preserved.
5. **No Telemetry or Logging Secrets:** Zero API keys or secrets in logs, DTOs, or database rows.

---

## 15. Remaining Risks & Mitigation

| Risk | Impact | Mitigation |
| :--- | :--- | :--- |
| **High-DPI / Large Page Crop Performance** | Rendering high-DPI page rasters (150+ DPI) could take 200–500ms. | Execution is strictly asynchronous on background `ThreadPoolExecutor`. Qt GUI thread is never blocked. |
| **Concurrent Editor Overlapping Conflict** | If user is typing on the exact line where the region token is inserted. | Handled by existing Phase 10F.5 in-buffer conflict markers (`<<<<<<< Local Draft ... >>>>>>> Incoming Canonical`) without data loss. |
| **Missing Source PDF on Disk** | Source PDF moved or deleted by user. | `DocumentViewerService` validates source artifact existence and emits structured `applyFailed` signal with user-friendly error message. |
