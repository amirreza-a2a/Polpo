# ============================================================
#  core/exceptions/domain_exceptions.py
# ============================================================

class DomainError(Exception):
    """خطای پایه برای تمام خطاهای منطق دامنه."""
    pass


class EntityNotFoundError(DomainError):
    """خطای عدم یافتن موجودیت مورد نظر."""
    def __init__(self, entity_name: str, entity_id: any):
        super().__init__(f"{entity_name} with identifier '{entity_id}' was not found.")
        self.entity_name = entity_name
        self.entity_id = entity_id


class QuotaExceededError(DomainError):
    """خطای اتمام یا ناکافی بودن سهمیه روزانه."""
    def __init__(self, message: str = "Daily page quota exceeded."):
        super().__init__(message)


class QueueFullError(DomainError):
    """خطای تکمیل ظرفیت صف پردازش."""
    def __init__(self, message: str = "Processing queue is currently full."):
        super().__init__(message)


class ArtifactNotFoundError(DomainError):
    """خطای عدم دسترسی یا ناموجود بودن فایل/آرتیفکت در مخزن."""
    def __init__(self, uri: str):
        super().__init__(f"Artifact not found at URI: {uri}")
        self.uri = uri


class AuthenticationError(DomainError):
    """خطای عدم احراز هویت یا نامعتبر بودن توکن/کلید دسترسی."""
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
