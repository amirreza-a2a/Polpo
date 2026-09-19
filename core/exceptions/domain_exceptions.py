# ============================================================
#  core/exceptions/domain_exceptions.py
# ============================================================

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
