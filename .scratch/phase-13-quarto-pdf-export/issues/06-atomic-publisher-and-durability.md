# 06: Atomic Artifact Publisher, No-Clobber Hard Links & Durability State Machine

**What to build:** Implement `AtomicPdfPublisher` in `infrastructure/pdf/atomic_publisher.py`. Provides guaranteed atomic PDF publication without 0-byte placeholders or direct destination streaming. Enforces strict no-clobber semantics via `os.link` when `overwrite=False`, atomic replacement via `os.replace` when `overwrite=True`, cross-platform filesystem error mapping, and non-fatal cleanup warning propagation.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

## Scope

- Define publication exceptions in `application/services/export_package_service.py` or `core/exceptions/domain_exceptions.py`:
  - `DestinationAlreadyExistsError(ExportPackageError)` (reused)
  - `DestinationDirectoryNotFoundError(ExportPackageError)` (reused)
  - `UnsupportedFilesystemError(DomainError)`: raised when atomic linking fails due to filesystem limitations (e.g. cross-volume link, exFAT lacking hard links).
  - `ExportPermissionError(DomainError)`: raised when user lacks write permission to destination directory.
- Define publication result DTO in `application/dto/pdf_publish_dto.py`:
  - `PublishResultDTO(destination_path: Path, published_bytes: int, cleanup_warning: Optional[str] = None)`
- Implement `AtomicPdfPublisher` in `infrastructure/pdf/atomic_publisher.py`:
  - **Pre-flight Validation:** Checks destination directory exists (`DestinationDirectoryNotFoundError`). If `overwrite=False` and target file exists, immediately raises `DestinationAlreadyExistsError`.
  - **No-Clobber Publication (`overwrite=False`):**
    - Executes atomic hard-link `os.link(src_compiled_pdf, destination_path)`.
    - If target already exists, atomic link fails with `FileExistsError`, mapped to `DestinationAlreadyExistsError`. Destination is never modified.
    - If filesystem does not support hard links or operation crosses mount boundaries (`EXDEV`), raises `UnsupportedFilesystemError` without modifying destination.
  - **Overwrite Publication (`overwrite=True`):**
    - Writes a temporary file in the destination's parent directory (`<dest_dir>/.tmp_<token>.pdf`).
    - Copies compiled PDF bytes to the temp file and flushes to disk (`os.fsync`).
    - Atomically replaces target via `os.replace(temp_path, destination_path)`.
  - **Cleanup State Machine:**
    - Attempts to remove the source compiled temporary artifact after successful publication.
    - If unlinking fails (e.g. file lock on Windows), the publication still succeeds, returning `cleanup_warning="Failed to delete temporary staging artifact"`.
  - **Zero 0-Byte Placeholders:** Never creates empty placeholder files at the destination.

## Non-Goals

- Compiling PDF documents (handled by `QuartoPdfAdapter` in Ticket 05).
- Orchestrating document resolution or editor save states (handled by `QuartoPdfExportService` in Ticket 07).

## Files Likely Impacted

- Modify/Create: `core/exceptions/domain_exceptions.py`
- Create: `application/dto/pdf_publish_dto.py`
- Create: `infrastructure/pdf/atomic_publisher.py`
- Create: `tests/unit/test_atomic_publisher.py`

## Public Interfaces / Contracts

```python
@dataclass(frozen=True)
class PublishResultDTO:
    destination_path: Path
    published_bytes: int
    cleanup_warning: Optional[str] = None


class AtomicPdfPublisher:
    def publish(
        self,
        source_compiled_pdf: Path,
        destination_path: Path,
        overwrite: bool = False,
    ) -> PublishResultDTO:
        """Atomically publishes compiled PDF to destination without partial writes or clobbering."""
        ...
```

## Architectural Invariants

- **Durability Invariant:** A failed publication must NEVER leave a partial, corrupted, or 0-byte file at `destination_path`.
- **No-Clobber Guarantee:** When `overwrite=False`, an existing destination must NEVER be replaced or mutated under any concurrency race condition.
- **Fail-Safe Cleanup:** Failure to delete a temporary file in staging must never cause an otherwise successful export to report failure to the user.

## Acceptance Criteria

- [ ] Publishing with `overwrite=False` to an existing file raises `DestinationAlreadyExistsError`.
- [ ] Publishing with `overwrite=False` uses atomic linking; existing destination is never corrupted or altered.
- [ ] Publishing with `overwrite=True` atomically replaces an existing file via `os.replace` from a sibling temp file.
- [ ] Publishing to a non-existent parent directory raises `DestinationDirectoryNotFoundError`.
- [ ] Filesystem permission denials raise `ExportPermissionError`.
- [ ] Cross-device link failures raise `UnsupportedFilesystemError` without touching destination.
- [ ] If temporary file unlinking fails after publication succeeds, returns `PublishResultDTO` with a non-null `cleanup_warning`.
- [ ] All unit tests pass in `tests/unit/test_atomic_publisher.py`.

## Verification / Tests Required

- `test_publish_no_clobber_success()`: publishes new file via atomic link; asserts contents and file exists.
- `test_publish_no_clobber_destination_exists()`: asserts `DestinationAlreadyExistsError` raised and destination unchanged.
- `test_publish_overwrite_atomic_replacement()`: publishes over existing file; asserts content replaced cleanly.
- `test_publish_missing_destination_directory()`: asserts `DestinationDirectoryNotFoundError`.
- `test_publish_cleanup_failure_surfaces_warning()`: mocks unlinking failure; asserts successful publication with warning.

## Commit Boundary

Single commit: `feat(export): implement atomic pdf publisher with hard-link no-clobber semantics`
