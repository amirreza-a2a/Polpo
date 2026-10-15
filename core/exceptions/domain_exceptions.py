from __future__ import annotations

from typing import Optional, Sequence, Tuple

class DomainError(Exception):
    """Base exception for all domain logic errors."""
    pass


class EntityNotFoundError(DomainError):
    """Raised when a requested domain entity is not found."""
    def __init__(self, entity_name: str, entity_id: any):
        super().__init__(f"{entity_name} with identifier '{entity_id}' was not found.")
        self.entity_name = entity_name
        self.entity_id = entity_id


class QuotaExceededError(DomainError):
    """Raised when user daily quota is exhausted or insufficient."""
    def __init__(self, message: str = "Daily page quota exceeded."):
        super().__init__(message)


class QueueFullError(DomainError):
    """Raised when the processing queue capacity has been reached."""
    def __init__(self, message: str = "Processing queue is currently full."):
        super().__init__(message)


class ArtifactNotFoundError(DomainError):
    """Raised when a requested artifact or file cannot be located in storage."""
    def __init__(self, uri: str):
        super().__init__(f"Artifact not found at URI: {uri}")
        self.uri = uri


class AuthenticationError(DomainError):
    """Raised when authentication fails or access credentials are invalid."""
    def __init__(self, message: str = "Authentication failed or token expired."):
        super().__init__(message)


class CredentialConsistencyError(DomainError):
    """Raised when credential storage and database metadata fail to reconcile consistently."""
    def __init__(self, message: str):
        super().__init__(message)


class StaleDocumentVersionError(DomainError):
    """Raised when an operation attempts to commit a canonical document based on an outdated version or path."""
    def __init__(self, job_id: int, base_version: int, current_version: int, message: str = ""):
        msg = message or (
            f"Cannot commit document for job {job_id}: base version {base_version} "
            f"is stale (active canonical version is {current_version})."
        )
        super().__init__(msg)
        self.job_id = job_id
        self.base_version = base_version
        self.current_version = current_version


class PublicationInProgressError(DomainError):
    """Raised when a publication operation is attempted while an active intent exists for the job."""
    def __init__(self, job_id: int, message: str = ""):
        msg = message or f"A publication operation is already in progress for job {job_id}."
        super().__init__(msg)
        self.job_id = job_id


class CanonicalDocumentIntegrityError(DomainError):
    """Raised when publication or access is rejected due to document quarantine or missing version history."""
    def __init__(self, message: str):
        super().__init__(message)


class DocumentAlreadyExistsError(DomainError):
    """Raised when initial publication is attempted on a job that already has canonical version history."""
    def __init__(self, job_id: int, message: str = ""):
        msg = message or f"Canonical document already exists for job {job_id}."
        super().__init__(msg)
        self.job_id = job_id


class AmbiguousVisualTokenError(DomainError):
    """Raised when multiple visual occurrence tokens match the same region identity."""
    def __init__(self, message: str = "Multiple visual tokens match the specified region."):
        super().__init__(message)


class RegionPublicationError(DomainError):
    """Raised when visual region review publication fails or exhausts retries."""
    def __init__(self, message: str = "Visual region publication failed."):
        super().__init__(message)


class UntrustedDocumentExtensionError(DomainError):
    """Raised when metadata contains prohibited execution keys, templates, or external files."""
    def __init__(self, message: str = "Untrusted document extension or execution configuration detected."):
        super().__init__(message)


class UnsupportedMetadataKeyError(DomainError):
    """Raised when YAML front matter contains unsupported or custom format keys."""
    def __init__(self, message: str = "Unsupported metadata key or format detected."):
        super().__init__(message)


class UnsupportedDocumentClassError(DomainError):
    """Raised when documentclass is not in the approved allowlist."""
    def __init__(self, message: str = "Unsupported document class specified."):
        super().__init__(message)


class MalformedMetadataError(DomainError):
    """Raised when metadata values violate type schema constraints."""
    def __init__(self, message: str = "Malformed metadata values or type violation."):
        super().__init__(message)


class ConflictingLatexHeaderError(DomainError):
    """Raised when user LaTeX headers explicitly conflict with system-managed packages."""
    def __init__(self, message: str = "Conflicting LaTeX package specified in headers."):
        super().__init__(message)


class UnsupportedFilesystemError(DomainError):
    """Raised when atomic linking fails due to filesystem limitations (e.g. cross-volume link, exFAT)."""
    def __init__(self, message: str = "Filesystem does not support atomic hard linking for export."):
        super().__init__(message)


class ExportPermissionError(DomainError):
    """Raised when user lacks write permission to destination directory."""
    def __init__(self, message: str = "Permission denied while writing to destination directory."):
        super().__init__(message)


class PdfExportError(DomainError):
    """Base exception for PDF export domain errors."""
    pass


class MissingLatexPackageError(PdfExportError):
    """Raised when LaTeX compilation fails due to missing packages."""
    def __init__(self, package_names: Sequence[str], sanitized_message: str = ""):
        super().__init__(sanitized_message or f"Missing LaTeX packages: {', '.join(package_names)}")
        self.package_names: Tuple[str, ...] = tuple(package_names)
        self.sanitized_message = sanitized_message


class PdfCompilationError(PdfExportError):
    """Raised when Quarto/XeLaTeX compilation fails with a compiler error."""
    def __init__(self, sanitized_message: str, line_number: Optional[int] = None):
        msg = (
            f"LaTeX compilation failed at line {line_number}: {sanitized_message}"
            if line_number
            else f"LaTeX compilation failed: {sanitized_message}"
        )
        super().__init__(msg)
        self.sanitized_message = sanitized_message
        self.line_number = line_number


class PdfCompilationTimeoutError(PdfExportError):
    """Raised when PDF compilation exceeds the configured timeout."""
    def __init__(self, message: str = "PDF compilation timed out."):
        super().__init__(message)


class PdfExportCancelledError(PdfExportError):
    """Raised when PDF export is cooperatively cancelled by user."""
    def __init__(self, message: str = "PDF export was cancelled."):
        super().__init__(message)


class OfflineRecoveryError(PdfExportError):
    """Raised when LaTeX package recovery fails due to lack of network connectivity or CTAN unreachability."""
    def __init__(self, message: str = "LaTeX package recovery failed due to lack of network connectivity."):
        super().__init__(message)
