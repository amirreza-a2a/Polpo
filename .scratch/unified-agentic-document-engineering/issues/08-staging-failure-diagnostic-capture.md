# TICK-ADE-08: Staging Workspace Failure Interception & Diagnostic Bundle Capture

- **Ticket ID:** `TICK-ADE-08`
- **Title:** Staging Workspace Failure Interception & Diagnostic Bundle Capture
- **Milestone:** Milestone C (Quarto Diagnostic Infrastructure)
- **Priority:** P1 (Blocker for Milestone C)
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Modify `QuartoPdfAdapter` and `StagingWorkspaceManager` in `infrastructure/pdf/quarto_adapter.py` and `infrastructure/pdf/staging_workspace.py` to capture a structured `DiagnosticBundle` (including intermediate `document.tex` excerpts and bounded compiler logs) before the ephemeral staging directory is purged on failure.
- **Explicit Non-Goals:**
  - No indefinite disk retention of full temporary staging folders (staging workspaces are still purged on every failure).
  - No unredacted logging of user home directories, absolute paths, or credentials.
  - No transmitting full `document.tex` to external AI providers (Finding G).
- **Prerequisites:** `TICK-ADE-01` completed; ADR-009 approved.
- **Dependencies (Blocked By):** `TICK-ADE-01`.
- **Unblocks:** `TICK-ADE-09`, `TICK-ADE-10`.

---

## 1. Existing Source-Code Integration Points

- `infrastructure/pdf/quarto_adapter.py`: Lines 161–179 (existing PDF rendering and compilation error handling).
- `infrastructure/pdf/staging_workspace.py`: Ephemeral staging session creation and cleanup in `finally:` block.
- `infrastructure/pdf/tex_log_sanitizer.py`: Path redaction and compiler error parsing.

---

## 2. Proposed Application Interfaces & DTOs

```python
class ErrorCategory(Enum):
    SOURCE_SYNTAX = "SOURCE_SYNTAX"
    PACKAGE_MISSING = "PACKAGE_MISSING"
    FONT_MISSING = "FONT_MISSING"
    TEMPLATE_CONFLICT = "TEMPLATE_CONFLICT"
    PANDOC_TRANSFORM = "PANDOC_TRANSFORM"
    LAYOUT_OVERFLOW = "LAYOUT_OVERFLOW"
    PRODUCT_DEFECT = "PRODUCT_DEFECT"
    UNKNOWN = "UNKNOWN"

class MappingConfidence(Enum):
    EXACT_TOKEN = "EXACT_TOKEN"          # 1.0
    CONTEXTUAL_MATCH = "CONTEXTUAL_MATCH"# 0.7
    SECTION_ANCHOR = "SECTION_ANCHOR"    # 0.5
    UNKNOWN = "UNKNOWN"                  # 0.0

@dataclass(frozen=True)
class DiagnosticBundle:
    failure_id: str                      # UUID4
    job_id: int
    canonical_version: int
    error_category: ErrorCategory
    sanitized_message: str
    bounded_log_excerpt: str             # Up to 100 lines / 10,000 characters
    failing_tex_line: Optional[int]
    failing_tex_snippet: str             # 10 lines surrounding failing line in document.tex
    mapped_markdown_line: Optional[int]
    mapped_markdown_snippet: str
    mapping_confidence: MappingConfidence
    missing_packages: Tuple[str, ...]
    missing_fonts: Tuple[str, ...]
    preamble_fingerprint: str
    captured_at: float
```

---

## 3. Detailed Implementation Requirements

1. **Failure Hook in `QuartoPdfAdapter`:**
   - When Quarto subprocess returns non-zero:
     1. Inspects `session.staging_dir` before exiting context manager.
     2. Reads intermediate `document.tex` and locates line reported by XeLaTeX log (`l.<N>`).
     3. Extracts failing TeX snippet (5 lines before, 5 lines after).
     4. Sanitizes compiler logs (redacts host home directories, paths, usernames, and hashes).
     5. Constructs `DiagnosticBundle` and caches in an in-memory bounded store (max 5 bundles per job, max 10MB total).
     6. Raises `PdfCompilationError(..., diagnostic_bundle=bundle)`.
2. **Ephemeral Purge Preservation:**
   - The `finally: shutil.rmtree(staging_dir)` block in `StagingWorkspaceManager` executes unconditionally, preventing disk bloat.
3. **Data Minimization (Finding G):**
   - The full `document.tex` is read strictly locally in memory to populate the snippet; it is NEVER marked for AI transmission or persistent disk storage.

---

## 4. Acceptance Criteria

- [ ] On non-zero exit code from Quarto, `PdfCompilationError` carries a populated `DiagnosticBundle`.
- [ ] `DiagnosticBundle` contains the relevant snippet from `document.tex` surrounding the failure line.
- [ ] Staging directory (`.pdf_staging/<job_id>_<token>/`) is completely removed from disk after the failure.
- [ ] Absolute host paths and usernames are redacted from `bounded_log_excerpt`.
- [ ] Zero disk leaks verified after 10 consecutive simulated compilation failures.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_diagnostic_bundle_capture.py`:
  - `test_intermediate_tex_snippet_extraction()`: Confirms failing snippet extraction around error line.
  - `test_staging_directory_purged_on_failure()`: Confirms directory removal even after exception.
  - `test_log_sanitization_redacts_host_paths()`: Confirms path and username redaction.
  - `test_diagnostic_store_lru_capacity()`: Verifies eviction after 5 bundles.

### 5.2 Negative & Security Tests
- `test_path_traversal_prevention_in_staging()`: Asserts no reading outside `session.staging_dir`.

### 5.3 Concurrency & Persistence Considerations
- Diagnostic store is thread-safe and stored in process memory.

---

## 6. Error and Recovery Behavior

- If `document.tex` is missing because Pandoc failed earlier, captures Pandoc stderr into `failing_tex_snippet = "No TeX file generated (Pandoc transform failure)"`.

---

## 7. Observability & Audit Requirements

- Logs diagnostic capture events with `failure_id` and error category.

---

## 8. Documentation Updates

- Document 5-tier diagnostic data lifecycle in ADR-009.

---

## 9. Required Project Skills

- `diagnosing-bugs`: Analyzing compiler output capture.
- `tdd`: Test-driven diagnostic bundle interception.

---

## 10. Verification Commands

```bash
pytest tests/unit/test_diagnostic_bundle_capture.py -v
```

---

## 11. Definition of Done

- All unit tests pass with 100% coverage.
- Staging failure capture verified without disk leaks.
