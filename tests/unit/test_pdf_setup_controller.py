# ============================================================
#  tests/unit/test_pdf_setup_controller.py
#  Unit tests for Desktop PdfSetupController
# ============================================================

import time
from unittest.mock import MagicMock

import pytest

from application.dto.package_recovery_dto import (
    PackageRecoveryRequest,
    PackageRecoveryResultDTO,
)
from application.dto.pdf_readiness_dto import EngineReadinessDTO
from interfaces.desktop.controllers.pdf_setup_controller import PdfSetupController
from interfaces.desktop.qt_compat import QGuiApplication


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(["-platform", "offscreen"])
    return app


def pump_until(app, condition, timeout=3.0, step=0.01):
    """Pumps the Qt event loop until condition evaluates to True or timeout expires."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        app.processEvents()
        if condition():
            return True
        time.sleep(step)
    app.processEvents()
    return bool(condition())


@pytest.fixture
def mock_discovery():
    discovery = MagicMock()
    discovery.probe_readiness.return_value = EngineReadinessDTO(
        quarto_available=True,
        quarto_version="1.4.550",
        quarto_path="/usr/bin/quarto",
        xelatex_available=True,
        is_tinytex=True,
        tex_distribution_name="TinyTeX",
        missing_prerequisites=(),
        diagnostic_instructions="",
    )
    return discovery


@pytest.fixture
def mock_recovery_service():
    service = MagicMock()
    service.execute_recovery.return_value = PackageRecoveryResultDTO(
        success=True,
        installed_packages=("amsmath", "geometry"),
        failed_packages=(),
        error_message="",
    )
    return service


def test_pdf_setup_controller_initial_readiness(qapp, mock_discovery):
    ctrl = PdfSetupController(
        runtime_discovery=mock_discovery,
        auto_probe=True,
    )

    # Wait for async initial probe to settle
    assert pump_until(qapp, lambda: ctrl.isExportReady is True, timeout=2.0)
    assert ctrl.quartoAvailable is True
    assert ctrl.quartoVersion == "1.4.550"
    assert ctrl.xelatexAvailable is True
    assert ctrl.texDistribution == "TinyTeX"
    assert ctrl.setupInstructions == ""
    assert ctrl.customQuartoPath == ""

    ctrl.shutdown()


def test_pdf_setup_controller_missing_prerequisites(qapp, mock_discovery):
    mock_discovery.probe_readiness.return_value = EngineReadinessDTO(
        quarto_available=False,
        quarto_version="",
        quarto_path=None,
        xelatex_available=False,
        is_tinytex=False,
        tex_distribution_name="",
        missing_prerequisites=("quarto", "xelatex"),
        diagnostic_instructions="Install Quarto and TinyTeX from https://quarto.org",
    )

    ctrl = PdfSetupController(
        runtime_discovery=mock_discovery,
        auto_probe=True,
    )

    assert pump_until(qapp, lambda: ctrl.setupInstructions != "", timeout=2.0)
    assert ctrl.isExportReady is False
    assert ctrl.quartoAvailable is False
    assert ctrl.xelatexAvailable is False
    assert "Install Quarto" in ctrl.setupInstructions

    ctrl.shutdown()


def test_pdf_setup_controller_custom_path_probing(qapp, mock_discovery):
    """Updates custom path, verifies probe with configured path and readiness signal emission."""
    mock_settings_service = MagicMock()
    mock_settings = MagicMock()
    mock_settings.custom_quarto_path = ""
    mock_settings_service.get_settings.return_value = mock_settings

    ctrl = PdfSetupController(
        runtime_discovery=mock_discovery,
        settings_service=mock_settings_service,
        auto_probe=False,
    )

    readiness_signals = []
    ctrl.readinessChanged.connect(lambda: readiness_signals.append(True))

    custom_path = "/opt/custom/bin/quarto"
    ctrl.setCustomQuartoPath(custom_path)

    assert ctrl.customQuartoPath == custom_path
    assert mock_settings.custom_quarto_path == custom_path
    mock_settings_service.update_settings.assert_called_with(mock_settings)

    # Wait for probe worker to complete
    assert pump_until(qapp, lambda: len(readiness_signals) >= 2, timeout=2.0)
    mock_discovery.probe_readiness.assert_called_with(configured_quarto_path=custom_path)

    ctrl.shutdown()


def test_pdf_setup_controller_install_missing_packages_success(
    qapp, mock_discovery, mock_recovery_service
):
    ctrl = PdfSetupController(
        runtime_discovery=mock_discovery,
        package_recovery_service=mock_recovery_service,
        auto_probe=False,
    )

    recovery_events = []
    ctrl.recoveryStatusChanged.connect(lambda ok, msg: recovery_events.append((ok, msg)))

    ctrl.installMissingPackages(["amsmath", "geometry"])

    assert pump_until(qapp, lambda: len(recovery_events) > 0, timeout=2.0)
    ok, msg = recovery_events[0]
    assert ok is True
    assert "amsmath" in msg
    mock_recovery_service.execute_recovery.assert_called_once()

    ctrl.shutdown()


def test_pdf_setup_controller_install_missing_packages_failure(
    qapp, mock_discovery, mock_recovery_service
):
    mock_recovery_service.execute_recovery.return_value = PackageRecoveryResultDTO(
        success=False,
        installed_packages=(),
        failed_packages=("boguspkg",),
        error_message="Package boguspkg not found on CTAN.",
    )

    ctrl = PdfSetupController(
        runtime_discovery=mock_discovery,
        package_recovery_service=mock_recovery_service,
        auto_probe=False,
    )

    recovery_events = []
    ctrl.recoveryStatusChanged.connect(lambda ok, msg: recovery_events.append((ok, msg)))

    ctrl.installMissingPackages(["boguspkg"])

    assert pump_until(qapp, lambda: len(recovery_events) > 0, timeout=2.0)
    ok, msg = recovery_events[0]
    assert ok is False
    assert "not found on CTAN" in msg

    ctrl.shutdown()


def test_pdf_setup_controller_shutdown_ignores_further_calls(qapp, mock_discovery):
    ctrl = PdfSetupController(
        runtime_discovery=mock_discovery,
        auto_probe=False,
    )
    ctrl.shutdown()
    assert ctrl._is_shutdown is True

    # Should not crash or submit tasks after shutdown
    ctrl.probeEnvironment()
    ctrl.setCustomQuartoPath("/some/path")
    ctrl.installMissingPackages(["pkg"])
