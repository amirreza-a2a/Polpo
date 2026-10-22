# ADR-004: Pipeline 2 Artifact Lineage and Canonical Publication Contract

- **Status:** Accepted
- **Date:** 2026-10-22
- **Task ID:** P2-03 (Stabilization Roadmap)
- **Scope:** Pipeline 2 Typography Refinement, Document Publication Authority, Review Workspace
- **Authoritative Baseline:** Commit `b0d73aa` (Post-P2-02-R1 Startup Recovery Hardening)

---

## 1. Context

Polpo is an embedded, desktop-first, local-first document processing application. In Polpo's Clean Architecture, document transformations occur in distinct phases:
1. **Pipeline 1 (Initial Processing):** Converts raw PDF files into structured Markdown text and cropped visual region images. Upon completion, Pipeline 1 invokes `DocumentPublicationService.publish_initial()`, creating version 1 of the canonical Markdown document (`output_{job_id}_v1.md`), inserting a `DocumentVersionRecord` into `document_versions`, and pointing `jobs.output_path` to the canonical URI.
2. **Review Workspace (Human Editing):** Users inspect and edit canonical documents in a dual-pane editor. When saving edits, `MarkdownEditorController` invokes `DocumentPublicationService.publish_version()`, advancing the canonical version under Optimistic Concurrency Control (OCC).
3. **Pipeline 2 (Typography Refinement):** Takes the canonical Markdown output from Pipeline 1 or a completed stage, invokes secondary AI text refinement, and stores the resulting refined text as `ArtifactType.PIPELINE2_MARKDOWN` (filename `p2_output_{p2_job_id}.md`).

### The Publication and Lineage Gap (P2-03)
Prior to P2-03:
- `JobExecutionService.execute_claimed_pipeline2_job()` stored output directly via `self.storage.store()` as `PIPELINE2_MARKDOWN`, bypassing `DocumentPublicationService`.
- While avoiding silent overwrite of canonical documents was intended, the relationship between derivative artifacts and canonical versions was undefined.
- `execute_claimed_pipeline2_job()` published `JobCompletedEvent(job_id=source_job_id, output_artifact_uri=p2_output_uri)`. This caused downstream presentation consumers to mistake the derivative artifact URI for the parent job's canonical document URI, even though `jobs.output_path` remained pointing to `output_{job_id}_v1.md`.
- No durable record existed tracking the exact base version against which a Pipeline 2 refinement was generated. If a user edited the document while Pipeline 2 was running, there was no safe, OCC-compliant mechanism to adopt or reject the refinement.

---

## 2. Problem Statement

How should Polpo define and enforce the architectural contract between Pipeline 2 refinement outputs and canonical, versioned Markdown documents, guaranteeing that:
1. Derivative artifacts never silently overwrite canonical documents or corrupt monotonic version watermarks?
2. Lineage between a derivative artifact and its source canonical version is durably persisted and verifiable?
3. Canonical adoption is an explicit, user-controlled action?
4. Optimistic concurrency control (OCC) rejects stale adoption attempts if human edits have advanced the canonical document?
5. Application events clearly distinguish between canonical document completion and derivative artifact generation without contradictory lifecycle notifications?

---

## 3. Settled Architectural & Product Decisions

### D01 — Pipeline 2 Output Is a Derivative Artifact
- **Decision:** Output from Pipeline 2 is strictly a derivative auxiliary artifact (`ArtifactType.PIPELINE2_MARKDOWN`). Completing Pipeline 2 execution must **never** automatically overwrite the canonical document, update `jobs.output_path`, increment version watermarks, or insert a `DocumentVersionRecord`.
- **Rationale:** AI refinement can introduce unexpected alterations or hallucinated formatting. The canonical document of record must remain stable until explicitly approved by the human user.

### D02 — Canonical Adoption Is an Explicit User Action
- **Decision:** Adopting a Pipeline 2 refinement as a new canonical document version requires an explicit, deliberate user action in the presentation layer (e.g., clicking *"Adopt refinement as a new document version"*).
- **Rationale:** Aligns with Polpo's human-in-the-loop desktop philosophy and AGENTS.md Rule 5 (Business logic belongs in application/core, presentation provides user controls).

### D03 — Durable Source-Version Lineage Tracking
- **Decision:** The `Pipeline2Job` domain entity and the `pipeline2_jobs` database table must persist an explicit `source_version: Optional[int]` column.
- **Rules:**
  1. When Pipeline 2 is triggered automatically following Pipeline 1, `source_version` is set to the initial publication version (`1`).
  2. When Pipeline 2 is submitted manually via `JobSubmissionService.submit_pipeline2_job()`, `source_version` is resolved from the active canonical document version (`DocumentVersionRecord.version`).
  3. Legacy Pipeline 2 records created prior to schema version 5 have `source_version = NULL`. For these records, adoption is disabled to prevent accidental overwrite of unverified versions. Lineage is never fabricated at adoption time.
  4. At adoption time, the system verifies that `p2_job.source_version` matches the recorded input artifact and passes it as `base_version` to `DocumentPublicationService.publish_version()`.

### D04 — All Canonical Writes Pass Exclusively Through `DocumentPublicationService`
- **Decision:** Adoption must invoke `DocumentPublicationService.publish_version(job_id, base_version, markdown_text, published_by="PIPELINE_2_ADOPTION")`.
- **Invariants Enforced by `DocumentPublicationService`:**
  - Validates that the job exists and has an active canonical document.
  - Checks for active publication intents (`PublicationInProgressError`).
  - Verifies document integrity (`CanonicalDocumentIntegrityError`).
  - Enforces OCC: `latest_version == base_version`. If a human edit has advanced the canonical document, raises `StaleDocumentVersionError(job_id, base_version, latest_version)`.
  - Stages and fsyncs the new canonical file atomically (`os.replace`).
  - Updates `jobs.output_path` and inserts an immutable `DocumentVersionRecord` atomically in SQLite.

### D05 — Preservation of Derivative Provenance and Terminal States
- **Decision:** Adopting a refinement does not delete or overwrite the derivative artifact (`p2_output_{id}.md`).
- **Rationale:** Provenance and auditability are preserved. The `Pipeline2Job` remains in terminal status `JobStatus.DONE`. Adoption is an independent publication lifecycle event.

### D06 — Unambiguous Application Event Contract
- **Decision:** Pipeline 2 execution must **never** publish `JobCompletedEvent(source_job_id, p2_output_uri)`.
- **New Event Specification:**
  - When Pipeline 2 completes, `JobExecutionService` publishes `Pipeline2CompletedEvent(job_id, source_job_id, output_artifact_uri, source_version)`.
  - `Pipeline2CompletedEvent` is a distinct typed event that does not inherit from `JobCompletedEvent`.
  - `QtSignalEventBridge` adapts `Pipeline2CompletedEvent` into Qt signal `pipeline2_completed_received(int p2_job_id, int source_job_id, str output_uri, int source_version)`.
  - Presentation subscribers receive unambiguous refinement notifications without confusing derivative paths for canonical document paths or emitting contradictory `JobStateChangedEvent` transitions for the source job.

### D07 — Human Edit Protection in Review Workspace
- **Decision:** No completion event or adoption action may silently discard or overwrite unsaved human edits in `MarkdownEditorController`.
- **Rules:**
  1. Receiving a `Pipeline2CompletedEvent` notifies the UI of refinement availability without mutating the editor buffer or dirty flag.
  2. If the editor has unsaved changes (`isDirty == True`), adoption is disabled in the UI. If triggered programmatically, adoption is rejected with an actionable error.
  3. If an editor save is in progress (`isSaving == True`) or a conflict session is active, adoption is disabled.
  4. After successful adoption, the canonical document advances. The editor reconciles its state via existing `notifyCanonicalDocumentAdvance()` mechanics.

---

## 4. Consequences and Compatibility

### Positive
- Strict separation of derivative artifacts from canonical version history.
- Zero risk of silent data loss or race conditions between background AI refinement and user editing.
- Deterministic OCC conflict handling via `DocumentPublicationService`.
- Full audit trail preserved in `pipeline2_jobs` and artifact storage.

### Negative / Trade-Offs
- Requires a new SQLite schema migration (`005_pipeline2_source_version.sql`).
- Legacy Pipeline 2 jobs lacking `source_version` cannot be adopted automatically.
