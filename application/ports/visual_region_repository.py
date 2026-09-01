# ============================================================
#  application/ports/visual_region_repository.py
#  Visual Region Persistence Port Definition
# ============================================================

from abc import ABC, abstractmethod
from typing import List, Optional
from core.entities.visual_region import VisualRegion


class IVisualRegionRepository(ABC):
    """
    Port interface for visual document region persistence.
    Provides decoupled access to region provenance and review states without leaking persistence details.
    """

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[VisualRegion]:
        """Retrieves a visual region by database internal primary key."""
        pass

    @abstractmethod
    def get_by_region_id(self, region_id: str) -> Optional[VisualRegion]:
        """Retrieves a visual region by stable global UUID4 region_id."""
        pass

    @abstractmethod
    def get_by_job_id(self, job_id: int) -> List[VisualRegion]:
        """Retrieves all visual regions for a given job ordered by page and display_order."""
        pass

    @abstractmethod
    def get_by_job_and_page(self, job_id: int, page_number: int) -> List[VisualRegion]:
        """Retrieves visual regions for a specific page of a job ordered by display_order."""
        pass

    @abstractmethod
    def save(self, region: VisualRegion) -> VisualRegion:
        """Persists a single visual region entity (insert or update)."""
        pass

    @abstractmethod
    def save_all(self, regions: List[VisualRegion]) -> List[VisualRegion]:
        """Persists multiple visual region entities within the active transaction."""
        pass

    @abstractmethod
    def delete_by_job_id(self, job_id: int) -> int:
        """Deletes all visual regions associated with a specific job."""
        pass

    @abstractmethod
    def delete_by_region_id(self, region_id: str) -> bool:
        """Deletes a specific visual region by global region_id."""
        pass
