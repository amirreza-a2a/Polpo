# TICK-ADE-08: Staging Workspace Failure Interception & Diagnostic Bundle Capture

- **Ticket ID:** `TICK-ADE-08`
- **Milestone:** Milestone C (Quarto Diagnostic Infrastructure)
- **Priority:** P1 (Blocker for Milestone C)
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Authoritative ADR:** ADR-009 (Quarto PDF Failure Diagnostics & AI PDF Repair)

---

## 1. Objective

Modify `QuartoPdfAdapter` and `StagingWorkspaceManager` in `infrastructure/pdf/quarto_adapter.py` and `infrastructure/pdf/staging_workspace.py` to capture a structured `DiagnosticBundle` (including intermediate `document.tex` excerpts and bounded compiler logs) before the ephemeral staging directory is purged on failure.

---

## 2. User Value

Transforms cryptic "LaTeX compilation failed" dialogs into rich, actionable diagnostics. Authors and the AI assistant can inspect the exact generated LaTeX code where XeLaTeX crashed without suffering from disk leaks or missing log files.

---

## 3. Scope

- Extend `StagingSession` in `infrastructure/pdf/staging_workspace.py` to expose `intermediate_tex_path: Path`.
- In `QuartoPdfAdapter.render_pdf()`:
  - When `sub_res.returncode != 0`:
    - Before exiting the `create_session` context manager, inspect `session.staging_dir` for `document.tex` and `*.log` files.
    - Read the failing TeX lines around the reported error line (5 lines before and 5 lines after).
    - Aggregate and sanitize logs using `TexLogSanitizer` with expanded limits (100 lines / 10,000 characters).
    - Construct an immutable `DiagnosticBundle` value object.
    - Store the bundle in a bounded, job-scoped in-memory/ephemeral diagnostic store (max 5 bundles per job, max 10MB total).
    - Raise `PdfCompilationError(..., diagnostic_bundle=bundle)`.
  - Staging workspace directory is still safely and deterministically removed in the `finally:` block of `create_session`!

---

## 4. Explicit Non-Goals

- No indefinite disk retention of full temporary staging folders (staging workspaces are still purged on every failure).
- No unredacted logging of user home directories or file paths.

---

## 5. Codebase References

- `infrastructure/pdf/quarto_adapter.py` (Lines 161–179: existing failure path).
- `infrastructure/pdf/staging_workspace.py` (Lines 543–573: ephemeral workspace cleanup in `finally:` block).
- `infrastructure/pdf/tex_log_sanitizer.py` (Sanitization and redaction logic).

---

## 6. Proposed Files & Components

- Create: `application/dto/pdf_diagnostic_dto.py` (Defines `DiagnosticBundle`, `ErrorCategory`, `MappingConfidence`)
- Modify: `infrastructure/pdf/quarto_adapter.py`
- Modify: `infrastructure/pdf/staging_workspace.py`
- Modify: `core/exceptions/domain_exceptions.py` (Update `PdfCompilationError` to hold optional `diagnostic_bundle`)
- Create: `tests/unit/test_diagnostic_bundle_capture.py`

---

## 7. Domain & Architectural Constraints

- **AGENTS.md Rule 29:** Ephemeral storage must be strictly bounded and cleaned up; no orphan directories accumulating in `.pdf_staging/`.
- **AGENTS.md Rule 10 & 11:** Sanitizer must redact absolute paths, usernames, and staging hashes before creating the bundle.

---

## 8. Detailed Implementation Requirements

1. If `document.tex` does not exist in staging (e.g. because Pandoc failed before generating LaTeX), capture the Pandoc stderr message into `failing_tex_snippet = "No TeX file generated (Pandoc transform failure)"`.
2. Extract lines from `document.tex` with safe bounds: if error line is 384, extract lines `max(1, 384 - 5)` to `min(total_lines, 384 + 5)`.
3. Bounded store: `DiagnosticStore` enforces an LRU capacity of 5 bundles per `job_id`. Older bundles are evicted automatically.

---

## 9. Acceptance Criteria

- [ ] On non-zero exit code from Quarto, `PdfCompilationError` carries a populated `DiagnosticBundle`.
- [ ] `DiagnosticBundle` contains the relevant snippet from `document.tex` surrounding the failure line.
- [ ] Staging directory (`.pdf_staging/<job_id>_<token>/`) is completely removed from disk after the failure.
- [ ] Absolute host paths and usernames are redacted from `bounded_log_excerpt`.
- [ ] Zero disk leaks verified after 10 consecutive simulated compilation failures.

---

## 10. Required Tests

- Unit tests in `tests/unit/test_diagnostic_bundle_capture.py` verifying:
  - Capture of generated TeX lines on simulated XeLaTeX failure.
  - Ephemeral directory removal in `finally:` block.
  - Redaction of username and path patterns.
  - Diagnostic store LRU eviction.

---

## 11. Security Requirements

- Prevent path traversal when resolving files inside `session.staging_dir`.

---

## 12. Completion Evidence & Follow-up

- Unit test execution evidence passing 100%.
- Unblocks: `TICK-ADE-09`, `TICK-ADE-10`.
