# 07: Application Export Orchestration Service & Canonical OCC Integration

**What to build:** Implement `QuartoPdfExportService` in `application/services/quarto_pdf_export_service.py` and result DTOs in `application/dto/pdf_export_dto.py`. Orchestrates the end-to-end PDF export pipeline: resolves canonical document versions under Optimistic Concurrency Control (OCC) via `IUnitOfWorkFactory`, validates front matter metadata through the normalizer, delegates rendering to the `IPdfExportEngine` port, and publishes the result atomically.

**Blocked by:** 01 (Metadata Normalizer, Allowlist Schema & Typesetting Header Compatibility), 05 (Quarto PDF Infrastructure Adapter & TeX Log Sanitizer), 06 (Atomic Artifact Publisher, No-Clobber Hard Links & Durability State Machine).

**Status:** ready-for-agent

## Scope

- Define export DTOs in `application/dto/pdf_export_dto.py`:
  - `ExportPdfCommand(job_id: int, version: int, destination_path: Path, overwrite: bool = False, is_cancelled: Optional[Callable[[], bool]] = None)`
  - `PdfExportResultDTO(destination_path: Path, canonical_version: int, page_count: int, file_size_bytes: int, duration_seconds: float, cleanup_warning: Optional[str] = None)`
- Implement `QuartoPdfExportService` in `application/services/quarto_pdf_export_service.py`:
  - **OCC & Canonical Validation:** Uses `IUnitOfWorkFactory` to query `Job` and `DocumentVersionRecord`. Verifies document file `output_{job_id}_v{version}.md` exists and content SHA-256 matches the version record (`CanonicalDocumentIntegrityError` if mismatched).
  - **Metadata Extraction:** Extracts raw YAML front matter from canonical Markdown. Derives fallback title from job filename via `derive_document_slug()`.
  - **Metadata Normalization:** Invokes `normalize_quarto_pdf_metadata()` (Ticket 01). Passes `is_tinytex` flag from engine discovery.
  - **Compilation Delegation:** Constructs `PdfExportRequest` and invokes `IPdfExportEngine.render_pdf()` (Ticket 05).
  - **Atomic Publication:** Invokes `AtomicPdfPublisher.publish()` (Ticket 06).
  - **Result Packaging:** Returns `PdfExportResultDTO` with page count, file size, duration, and any non-fatal cleanup warnings.
  - **F-03 Enforced (Trust Boundary Disclosure):** Docstrings document the desktop user OS privilege model and TeX filesystem read limitations.

## Non-Goals

- Presentation controller logic or Qt signals (handled by `ExportController` in Ticket 09).
- Direct subprocess execution (handled by infrastructure adapters).
- In-memory editor dirty buffer management (handled by `ExportController` save gate).

## Files Likely Impacted

- Create: `application/dto/pdf_export_dto.py`
- Create: `application/services/quarto_pdf_export_service.py`
- Create: `tests/unit/test_quarto_pdf_export_service.py`

## Public Interfaces / Contracts

```python
@dataclass(frozen=True)
class ExportPdfCommand:
    job_id: int
    version: int
    destination_path: Path
    overwrite: bool = False
    is_cancelled: Optional[Callable[[], bool]] = None


@dataclass(frozen=True)
class PdfExportResultDTO:
    destination_path: Path
    canonical_version: int
    page_count: int
    file_size_bytes: int
    duration_seconds: float
    cleanup_warning: Optional[str] = None


class QuartoPdfExportService:
    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        export_engine: IPdfExportEngine,
        publisher: AtomicPdfPublisher,
    ):
        ...

    def export_pdf(self, cmd: ExportPdfCommand) -> PdfExportResultDTO:
        """Orchestrates validation, normalization, compilation, and atomic publication."""
        ...
```

## Architectural Invariants

- **Clean Architecture (AGENTS.md Rule 6.2):** `QuartoPdfExportService` depends strictly on ports (`IUnitOfWorkFactory`, `IPdfExportEngine`) and domain abstractions. ZERO imports of `subprocess`, `PySide6`, or `sqlite3`.
- **Immutable Canonical Source (ADR-003 Section 4.1):** Canonical Markdown files are read-only inputs and are NEVER mutated during export.
- **OCC Integrity:** Operations must verify that the requested document version matches the on-disk canonical file.

## Acceptance Criteria

- [ ] `QuartoPdfExportService` orchestrates the complete PDF export flow through abstract ports.
- [ ] Validates canonical document version; raises `EntityNotFoundError` or `ExportSourceNotFoundError` if missing.
- [ ] Verifies content SHA-256 against version record; raises `CanonicalDocumentIntegrityError` if mismatched.
- [ ] Passes extracted metadata to `normalize_quarto_pdf_metadata()` (enforcing F-01 and F-02).
- [ ] Invokes `IPdfExportEngine.render_pdf()` and propagates cancellation/timeout errors cleanly.
- [ ] Publishes compiled PDF atomically via `AtomicPdfPublisher`.
- [ ] Surfaces any non-fatal cleanup warnings in `PdfExportResultDTO`.
- [ ] **F-03 Enforced:** Service docstring documents the desktop OS privilege model and TeX filesystem read limitations.
- [ ] Comprehensive unit tests pass in `tests/unit/test_quarto_pdf_export_service.py` with mocked ports.

## Verification / Tests Required

- `test_export_service_happy_path()`: verifies full orchestration and returns `PdfExportResultDTO`.
- `test_export_service_version_mismatch()`: asserts `CanonicalDocumentIntegrityError` when hash differs.
- `test_export_service_propagates_compilation_error()`: asserts `MissingLatexPackageError` propagates cleanly.
- `test_export_service_propagates_destination_exists()`: asserts `DestinationAlreadyExistsError` on `overwrite=False`.
- `test_export_service_surfaces_cleanup_warning()`: verifies warning attached when publisher reports cleanup issue.

## Commit Boundary

Single commit: `feat(export): implement quarto pdf export application orchestration service`
