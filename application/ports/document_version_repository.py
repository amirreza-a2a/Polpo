# ============================================================
#  application/ports/document_version_repository.py
#  Document Version & Publish Intent Persistence Port Definitions
# ============================================================

from abc import ABC, abstractmethod
from typing import List, Optional

from core.entities.document_version import DocumentVersionRecord, PublishIntentRecord


class IPublishIntentRepository(ABC):
    """
    Port interface for publication intent journaling.
    Supports durable reservation and recovery of in-flight publication operations.
    """

    @abstractmethod
    def get_by_id(self, intent_id: str) -> Optional[PublishIntentRecord]:
        """Retrieves a publish intent by its unique UUID intent_id."""
        pass

    @abstractmethod
    def get_by_job_id(self, job_id: int) -> Optional[PublishIntentRecord]:
        """Retrieves the active publish intent for a given job, or None."""
        pass

    def get_intent_by_job_id(self, job_id: int) -> Optional[PublishIntentRecord]:
        """Alias for get_by_job_id."""
        return self.get_by_job_id(job_id)

    @abstractmethod
    def list_all(self) -> List[PublishIntentRecord]:
        """Lists all active or pending publish intents (used for startup reconciliation)."""
        pass

    @abstractmethod
    def insert_intent(self, intent: PublishIntentRecord) -> PublishIntentRecord:
        """Inserts a new publish intent record. Raises error if job_id already has an active intent."""
        pass

    @abstractmethod
    def update_status(self, intent_id: str, status: str) -> bool:
        """Updates the status of an existing publish intent (e.g. 'PENDING' -> 'FLUSHED')."""
        pass

    def update_intent_status(self, intent_id: str, status: str) -> bool:
        """Alias for update_status."""
        return self.update_status(intent_id, status)

    @abstractmethod
    def delete_intent(self, intent_id: str) -> bool:
        """Deletes a completed or discarded publish intent."""
        pass


class IDocumentVersionRepository(ABC):
    """
    Port interface for immutable canonical document version history.
    """

    @abstractmethod
    def get_latest(self, job_id: int) -> Optional[DocumentVersionRecord]:
        """Retrieves the highest/latest document version record for a job, or None."""
        pass

    def get_latest_document_version(self, job_id: int) -> Optional[DocumentVersionRecord]:
        """Alias for get_latest."""
        return self.get_latest(job_id)

    @abstractmethod
    def get_by_job_id(self, job_id: int) -> List[DocumentVersionRecord]:
        """Retrieves all version history records for a given job ordered by version ascending."""
        pass

    @abstractmethod
    def get_by_version(self, job_id: int, version: int) -> Optional[DocumentVersionRecord]:
        """Retrieves a specific document version record for a job, or None."""
        pass

    @abstractmethod
    def insert_document_version(self, record: DocumentVersionRecord) -> DocumentVersionRecord:
        """Inserts a new immutable document version record."""
        pass
