# ============================================================
#  application/ports/unit_of_work.py
# ============================================================

from abc import ABC, abstractmethod
from application.ports.repositories import (
    IJobRepository,
    IPipeline2JobRepository,
    IUserRepository,
    IPromptRepository,
    IApiRepository,
    IDonationRepository,
)


class IUnitOfWork(ABC):
    """
    درگاه مدیریت تراکنش و انسجام داده‌ها (Unit of Work).
    عملیات‌های چند-مخزنی را تحت یک تراکنش اتمیک واحد قرار می‌دهد.
    """
    jobs: IJobRepository
    pipeline2_jobs: IPipeline2JobRepository
    users: IUserRepository
    prompts: IPromptRepository
    apis: IApiRepository
    donations: IDonationRepository

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
    """Factory جهت ایجاد نمونه‌های تازه از IUnitOfWork."""

    @abstractmethod
    def create(self) -> IUnitOfWork:
        pass
