# ============================================================
#  application/services/settings_service.py
# ============================================================

from typing import Optional
from application.ports.unit_of_work import IUnitOfWorkFactory
from core.entities.settings import AppSettings


class LocalSettingsService:
    """
    Application service managing local desktop client settings and preferences.
    Provides use cases for reading and updating single-user runtime preferences.
    """

    def __init__(self, uow_factory: IUnitOfWorkFactory):
        self.uow_factory = uow_factory

    def get_settings(self) -> AppSettings:
        with self.uow_factory.create() as uow:
            if hasattr(uow, "settings") and uow.settings:
                return uow.settings.get()
            return AppSettings()

    def update_settings(self, settings: AppSettings) -> AppSettings:
        settings.validate()
        with self.uow_factory.create() as uow:
            if hasattr(uow, "settings") and uow.settings:
                saved = uow.settings.save(settings)
                uow.commit()
                return saved
            return settings
