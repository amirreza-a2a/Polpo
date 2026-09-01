# ============================================================
#  interfaces/desktop/controllers/settings_controller.py
#  Desktop Presentation Controller for Application Settings
# ============================================================

from typing import Optional, Dict, Any
from interfaces.desktop.qt_compat import QObject, Slot, Signal, Property
from core.entities.settings import AppSettings
from application.services.settings_service import LocalSettingsService
from application.services.artifact_service import ArtifactService
from application.sanitizer import sanitize_error_message


class SettingsController(QObject):
    """
    Presentation controller managing desktop preferences (theme, concurrency, retention, schedule policy).
    """

    settings_changed = Signal()
    error_occurred = Signal(str)

    def __init__(
        self,
        settings_service: LocalSettingsService,
        artifact_service: ArtifactService,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.settings_service = settings_service
        self.artifact_service = artifact_service
        self._current_settings: AppSettings = self.settings_service.get_settings()

    def _refresh(self) -> None:
        self._current_settings = self.settings_service.get_settings()
        self.settings_changed.emit()

    # --- Q_PROPERTY Bindings ---

    def _get_theme(self) -> str:
        return self._current_settings.theme

    def _get_max_concurrency(self) -> int:
        return self._current_settings.max_concurrent_jobs

    def _get_missed_policy(self) -> str:
        return self._current_settings.missed_schedule_policy

    def _get_retention_days(self) -> int:
        return self._current_settings.artifact_retention_days

    def _get_auto_retry(self) -> bool:
        return self._current_settings.auto_retry

    def _get_auto_pipeline2(self) -> bool:
        return self._current_settings.auto_pipeline2

    theme = Property(str, _get_theme, notify=settings_changed)
    maxConcurrentJobs = Property(int, _get_max_concurrency, notify=settings_changed)
    missedSchedulePolicy = Property(str, _get_missed_policy, notify=settings_changed)
    artifactRetentionDays = Property(int, _get_retention_days, notify=settings_changed)
    autoRetry = Property(bool, _get_auto_retry, notify=settings_changed)
    autoPipeline2 = Property(bool, _get_auto_pipeline2, notify=settings_changed)

    # --- Slots ---

    @Slot(result="QVariantMap")
    def get_settings(self) -> Dict[str, Any]:
        """Returns current settings dictionary."""
        s = self._current_settings
        return {
            "theme": s.theme,
            "max_concurrent_jobs": s.max_concurrent_jobs,
            "missed_schedule_policy": s.missed_schedule_policy,
            "artifact_retention_days": s.artifact_retention_days,
            "auto_retry": s.auto_retry,
            "auto_pipeline2": s.auto_pipeline2,
        }

    @Slot(str, int, str, int, bool, bool, result=bool)
    def save_settings(
        self,
        theme: str,
        max_concurrent_jobs: int,
        missed_schedule_policy: str,
        artifact_retention_days: int,
        auto_retry: bool,
        auto_pipeline2: bool,
    ) -> bool:
        """Persists updated application settings."""
        try:
            settings = AppSettings(
                theme=theme.strip(),
                max_concurrent_jobs=max(1, min(8, max_concurrent_jobs)),
                missed_schedule_policy=missed_schedule_policy.strip(),
                artifact_retention_days=max(1, artifact_retention_days),
                auto_retry=auto_retry,
                auto_pipeline2=auto_pipeline2,
            )
            saved = self.settings_service.update_settings(settings)
            self._current_settings = saved
            self.settings_changed.emit()
            return True
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return False

    @Slot(int, result=int)
    @Slot(result=int)
    def prune_artifacts(self, max_age_hours: int = 48) -> int:
        """Prunes local output artifacts older than max_age_hours."""
        try:
            return self.artifact_service.prune_expired_artifacts(max_age_hours)
        except Exception as err:
            self.error_occurred.emit(sanitize_error_message(str(err)))
            return 0
