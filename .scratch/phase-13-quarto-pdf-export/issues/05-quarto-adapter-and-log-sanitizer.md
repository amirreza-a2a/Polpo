# 05: Quarto PDF Infrastructure Adapter & TeX Log Sanitizer

**What to build:** Implement `QuartoPdfAdapter` in `infrastructure/pdf/quarto_adapter.py` and `TexLogSanitizer` in `infrastructure/pdf/tex_log_sanitizer.py`. Implements the `IPdfExportEngine` application port. Coordinates staging, subprocess execution, and compilation. Audits effective engine options, sanitizes LaTeX compiler logs, extracts structured missing package diagnostics, strips machine-specific paths, and maps errors to typed domain exceptions.

**Blocked by:** 02 (Quarto and TeX Runtime Discovery, Readiness Probing & In-App Diagnostics), 03 (Subprocess Supervisor, Environment Sanitization & Process Tree Cancellation), 04 (Isolated Staging Workspace, Preamble Management & Visual Region Asset Closure).

**Status:** ready-for-agent

## Scope

- Define export port interfaces and DTOs in `application/ports/pdf_export_port.py`:
  - `PdfExportRequest(job_id: int, canonical_markdown: str, raw_metadata: Dict[str, Any], job_artifacts_dir: Path, is_tinytex: bool, is_cancelled: Optional[Callable[[], bool]] = None)`
  - `PdfExportResult(compiled_pdf_path: Path, page_count: int, duration_seconds: float, warnings: Tuple[str, ...])`
  - `IPdfExportEngine(ABC)`:
    - `render_pdf(request: PdfExportRequest) -> PdfExportResult`
    - `install_packages(package_names: Sequence[str], is_cancelled: Optional[Callable[[], bool]] = None) -> PackageInstallResult`
- Implement `TexLogSanitizer` in `infrastructure/pdf/tex_log_sanitizer.py`:
  - **Missing Package Extraction:** Detects `! LaTeX Error: File '<name>.sty' not found.` or `Package <name> Error`. Extracts missing package stem and maps to CTAN package identifier via known package dictionary.
  - **Font Error Detection:** Detects `! The font "<fontname>" cannot be found` or fontspec errors.
  - **Path Sanitization:** Strips host-specific absolute paths (e.g. `/home/username/`, `C:\Users\username\`), internal staging hashes, and system usernames from output messages.
  - **Line Number Mapping:** Extracts error line numbers from TeX logs when available.
- Implement `QuartoPdfAdapter` in `infrastructure/pdf/quarto_adapter.py`:
  - **Command Construction:** Assembles `[quarto_path, "render", "document.qmd", "--to", "pdf", "--no-execute"]`.
  - **Staging Orchestration:** Executes within `StagingWorkspaceManager` context.
  - **Execution & Audit:** Calls `SubprocessSupervisor.execute()`. Audits effective engine options to ensure XeLaTeX and `-no-shell-escape` were bound.
  - **Error Mapping:**
    - Missing packages raise `MissingLatexPackageError(package_names, sanitized_message)`.
    - Compiler syntax errors raise `PdfCompilationError(sanitized_message, line_number)`.
    - Subprocess timeouts raise `PdfCompilationTimeoutError`.
    - Subprocess cancellation raises `PdfExportCancelledError`.
  - **Output Verification:** Verifies compiled `document.pdf` exists, has non-zero size, and begins with `%PDF-` magic header.
  - **F-02 Verification Support:** Supports retaining intermediate `.tex` (when `keep-tex: true`) for test verification of preamble and body ordering.
  - **F-03 Enforced (Trust Boundary Disclosure):** Docstrings document the OS privilege model and TeX filesystem read limitations.

## Non-Goals

- User-initiated package installation (handled by `TinyTexManager` in Ticket 08).
- Atomic destination publication (handled by `AtomicPdfPublisher` in Ticket 06).
- Desktop GUI progress presentation (handled in Ticket 09).

## Files Likely Impacted

- Modify: `application/ports/pdf_export_port.py`
- Create: `infrastructure/pdf/tex_log_sanitizer.py`
- Create: `infrastructure/pdf/quarto_adapter.py`
- Create: `tests/unit/test_tex_log_sanitizer.py`
- Create: `tests/unit/test_quarto_adapter.py`

## Public Interfaces / Contracts

```python
class TexLogSanitizer:
    def sanitize(self, raw_log: str) -> SanitizedDiagnostic:
        """Parses LaTeX log output, extracts missing packages/fonts, and redacts host paths."""
        ...


class QuartoPdfAdapter(IPdfExportEngine):
    def __init__(
        self,
        discovery: IPdfRuntimeDiscovery,
        supervisor: SubprocessSupervisor,
        staging: StagingWorkspaceManager,
        sanitizer: TexLogSanitizer,
    ):
        ...

    def render_pdf(self, request: PdfExportRequest) -> PdfExportResult:
        """Executes Quarto PDF rendering in isolated staging and returns compiled artifact."""
        ...
```

## Architectural Invariants

- **Clean Architecture:** `QuartoPdfAdapter` implements the `IPdfExportEngine` port. Application services consume only the port.
- **Fail Fast on Missing Packages (ADR-003 D04):** Compilation never attempts silent network installation during `render_pdf()`.
- **Sanitized Presentation:** Raw paths containing usernames or internal build directories MUST never leak into user-facing error messages.

## Acceptance Criteria

- [ ] `QuartoPdfAdapter` implements `IPdfExportEngine.render_pdf()` conforming to the application port.
- [ ] Command line passes `--to pdf --no-execute` and executes inside the isolated staging workspace.
- [ ] Compiler output is parsed by `TexLogSanitizer`: missing `.sty` files raise `MissingLatexPackageError` with mapped package names.
- [ ] Compiler logs containing usernames or absolute paths are sanitized before being attached to exceptions.
- [ ] Successful render returns `PdfExportResult` pointing to a valid `%PDF-` document.
- [ ] Cancelled render raises `PdfExportCancelledError` and terminates subprocesses immediately.
- [ ] Timed-out render raises `PdfCompilationTimeoutError`.
- [ ] **F-02 Verified:** Test harness verifies intermediate `.tex` character-offset ordering: `polpo_preamble.tex` $\to$ root headers $\to$ format headers $\to$ inline headers $\to$ `\begin{document}` $\to$ before body $\to$ body $\to$ after body $\to$ `\end{document}`.
- [ ] **F-03 Enforced:** Docstrings document that XeLaTeX executes within desktop user OS privileges without filesystem read sandboxing.
- [ ] All unit tests pass in `tests/unit/test_tex_log_sanitizer.py` and `tests/unit/test_quarto_adapter.py`.

## Verification / Tests Required

- `test_sanitizer_extracts_missing_package_and_redacts_paths()`: verifies regex extraction of `.sty` files and path sanitization.
- `test_sanitizer_identifies_font_error()`: verifies fontspec error detection.
- `test_adapter_successful_render()`: mocks successful Quarto run producing valid `%PDF-` file.
- `test_adapter_missing_package_raises_typed_error()`: asserts `MissingLatexPackageError` with extracted package list.
- `test_adapter_intermediate_tex_ordering_invariant()`: asserts character offsets of preamble, headers, and body elements (F-02).
- `test_adapter_cancellation_and_timeout()`: asserts proper exception types on abort.

## Commit Boundary

Single commit: `feat(export): implement quarto pdf adapter and tex log sanitizer`
