"""Presentation controller for Quarto and XeLaTeX runtime setup and diagnostics."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional, Sequence

from application.dto.package_recovery_dto import PackageRecoveryRequest
from application.dto.pdf_readiness_dto import EngineReadinessDTO
from application.ports.pdf_export_port import IPdfRuntimeDiscovery
from application.sanitizer import sanitize_error_message
from application.services.package_recovery_service import PackageRecoveryService
from interfaces.desktop.qt_compat import Property, QObject, Signal, Slot


class PdfSetupController(QObject):
    """
    Presentation controller for Quarto and XeLaTeX setup assistance,
    prerequisite capability probing, custom path configuration,
    and consented LaTeX package recovery.
    """

    readinessChanged = Signal()
    recoveryStatusChanged = Signal(bool, str)

    _internalReadinessUpdated = Signal(bool, str, bool, str, str)
    _internalRecoveryStatusChanged = Signal(bool, str)

    def __init__(
        self,
        runtime_discovery: IPdfRuntimeDiscovery,
        package_recovery_service: Optional[PackageRecoveryService] = None,
        settings_service: Optional[Any] = None,
        parent: Optional[QObject] = None,
        auto_probe: bool = True,
    ):
        super().__init__(parent)
        self.runtime_discovery = runtime_discovery
        self.package_recovery_service = package_recovery_service
        self.settings_service = settings_service

        self._quarto_available: bool = False
        self._quarto_version: str = ""
        self._xelatex_available: bool = False
        self._tex_distribution: str = ""
        self._setup_instructions: str = ""
        self._custom_quarto_path: str = ""
        self._is_probing: bool = False
        self._is_recovering: bool = False
        self._is_shutdown: bool = False

        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="PdfSetupWorker")

        self._internalReadinessUpdated.connect(self._on_internal_readiness_updated)
        self._internalRecoveryStatusChanged.connect(self._on_internal_recovery_status_changed)

        # Restore custom path from settings if available
        if self.settings_service is not None:
            try:
                settings = self.settings_service.get_settings()
                if hasattr(settings, "custom_quarto_path") and settings.custom_quarto_path:
                    self._custom_quarto_path = str(settings.custom_quarto_path)
            except Exception:
                pass

        if auto_probe:
            self.probeEnvironment()

    # --- Properties ---

    def is_export_ready(self) -> bool:
        return self._quarto_available and self._xelatex_available

    isExportReady = Property(bool, is_export_ready, notify=readinessChanged)

    def quarto_available(self) -> bool:
        return self._quarto_available

    quartoAvailable = Property(bool, quarto_available, notify=readinessChanged)

    def quarto_version(self) -> str:
        return self._quarto_version

    quartoVersion = Property(str, quarto_version, notify=readinessChanged)

    def xelatex_available(self) -> bool:
        return self._xelatex_available

    xelatexAvailable = Property(bool, xelatex_available, notify=readinessChanged)

    def tex_distribution(self) -> str:
        return self._tex_distribution

    texDistribution = Property(str, tex_distribution, notify=readinessChanged)

    def setup_instructions(self) -> str:
        return self._setup_instructions

    setupInstructions = Property(str, setup_instructions, notify=readinessChanged)

    def custom_quarto_path(self) -> str:
        return self._custom_quarto_path

    customQuartoPath = Property(str, custom_quarto_path, notify=readinessChanged)

    # --- Slots ---

    @Slot()
    def probeEnvironment(self) -> None:
        """Asynchronously probes the host environment for Quarto and XeLaTeX runtimes."""
        if self._is_shutdown:
            return
        self._is_probing = True
        self._executor.submit(self._run_probe_worker)

    @Slot(str)
    def setCustomQuartoPath(self, path: str) -> None:
        """Updates the custom Quarto executable path, persists preference, and re-probes."""
        if self._is_shutdown:
            return
        cleaned = path.strip()
        self._custom_quarto_path = cleaned

        if self.settings_service is not None:
            try:
                settings = self.settings_service.get_settings()
                if hasattr(settings, "custom_quarto_path"):
                    setattr(settings, "custom_quarto_path", cleaned)
                    self.settings_service.update_settings(settings)
            except Exception:
                pass

        self.readinessChanged.emit()
        self.probeEnvironment()

    @Slot(list)
    def installMissingPackages(self, packages: Sequence[str]) -> None:
        """Submits consented LaTeX package installation to the background worker."""
        if self._is_shutdown:
            return
        self._is_recovering = True
        self._executor.submit(self._run_install_worker, list(packages))

    @Slot()
    def shutdown(self) -> None:
        """Gracefully shuts down background worker pool."""
        self._is_shutdown = True
        try:
            self._executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=True)

    # --- Background Workers & Internal Handlers ---

    def _run_probe_worker(self) -> None:
        """Executed on background worker thread to probe external runtimes."""
        try:
            dto: EngineReadinessDTO = self.runtime_discovery.probe_readiness(
                configured_quarto_path=self._custom_quarto_path or None
            )
            self._internalReadinessUpdated.emit(
                dto.quarto_available,
                dto.quarto_version,
                dto.xelatex_available,
                dto.tex_distribution_name,
                dto.diagnostic_instructions,
            )
        except Exception as e:
            sanitized = sanitize_error_message(str(e))
            self._internalReadinessUpdated.emit(
                False,
                "",
                False,
                "",
                f"Failed to probe PDF export environment: {sanitized}",
            )

    def _run_install_worker(self, packages: List[str]) -> None:
        """Executed on background worker thread to perform package installation."""
        try:
            if self.package_recovery_service is None:
                self._internalRecoveryStatusChanged.emit(
                    False, "Package recovery service is not available."
                )
                return

            req = PackageRecoveryRequest(packages=tuple(packages))
            result = self.package_recovery_service.execute_recovery(req)

            if result.success:
                installed_summary = (
                    f"Successfully installed: {', '.join(result.installed_packages)}"
                    if result.installed_packages
                    else "All requested packages installed successfully."
                )
                self._internalRecoveryStatusChanged.emit(True, installed_summary)
                # Re-probe environment automatically after successful installation
                self._run_probe_worker()
            else:
                err_msg = result.error_message or "Package recovery failed."
                self._internalRecoveryStatusChanged.emit(False, sanitize_error_message(err_msg))
        except Exception as e:
            self._internalRecoveryStatusChanged.emit(False, sanitize_error_message(str(e)))

    @Slot(bool, str, bool, str, str)
    def _on_internal_readiness_updated(
        self,
        quarto_available: bool,
        quarto_version: str,
        xelatex_available: bool,
        tex_dist_name: str,
        instructions: str,
    ) -> None:
        """Main-thread slot updating readiness properties and emitting readinessChanged."""
        if self._is_shutdown:
            return
        self._is_probing = False
        self._quarto_available = quarto_available
        self._quarto_version = quarto_version
        self._xelatex_available = xelatex_available
        self._tex_distribution = tex_dist_name
        self._setup_instructions = instructions
        self.readinessChanged.emit()

    @Slot(bool, str)
    def _on_internal_recovery_status_changed(self, success: bool, message: str) -> None:
        """Main-thread slot emitting recoveryStatusChanged."""
        if self._is_shutdown:
            return
        self._is_recovering = False
        self.recoveryStatusChanged.emit(success, message)
