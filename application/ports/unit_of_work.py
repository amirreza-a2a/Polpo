# ============================================================
#  application/ports/unit_of_work.py
# ============================================================

from abc import ABC, abstractmethod
from application.ports.repositories import (
    ISettingsRepository,
    IJobRepository,
    IPipeline2JobRepository,
    IPromptRepository,
    IApiRepository,
    IVisualRegionRepository,
)


class IUnitOfWork(ABC):
    """
    Transactional Unit of Work contract for the desktop application.
    Coordinates persistence operations across local repositories within an atomic boundary.
    """
    settings: ISettingsRepository
    jobs: IJobRepository
    pipeline2_jobs: IPipeline2JobRepository
    prompts: IPromptRepository
    apis: IApiRepository
    visual_regions: IVisualRegionRepository

    @abstractmethod
    def __enter__(self) -> "IUnitOfWork":
        pass

    @abstractmethod
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    @abstractmethod
    def commit(self) -> None:
        pass

    @abstractmethod
    def rollback(self) -> None:
        pass


class IUnitOfWorkFactory(ABC):
    """Factory interface for producing scoped IUnitOfWork instances."""

    @abstractmethod
    def create(self) -> IUnitOfWork:
        pass
