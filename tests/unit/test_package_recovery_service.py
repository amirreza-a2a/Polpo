# ============================================================
#  tests/unit/test_package_recovery_service.py
#  Unit tests for PackageRecoveryService
#  Phase 13 — Ticket #104
# ============================================================

from unittest.mock import MagicMock, patch

import pytest

from application.dto.package_recovery_dto import (
    PackageRecoveryPlanDTO,
    PackageRecoveryRequest,
    PackageRecoveryResultDTO,
)
from application.dto.pdf_readiness_dto import EngineReadinessDTO
from application.ports.pdf_export_port import (
    IPdfRuntimeDiscovery,
    ITinyTexManager,
    PackageInstallResult,
)
from application.services.package_recovery_service import PackageRecoveryService
from core.exceptions.domain_exceptions import (
    DomainError,
    OfflineRecoveryError,
    PdfExportCancelledError,
)


def _create_mock_discovery(
    is_tinytex: bool = True,
    tex_distribution_name: str = "TinyTeX",
) -> MagicMock:
    discovery = MagicMock(spec=IPdfRuntimeDiscovery)
    discovery.probe_readiness.return_value = EngineReadinessDTO(
        quarto_available=True,
        quarto_version="1.5.0",
        quarto_path="/usr/bin/quarto",
        xelatex_available=True,
        is_tinytex=is_tinytex,
        tex_distribution_name=tex_distribution_name,
    )
    return discovery


def test_plan_tinytex_allows_auto_recover():
    """Asserts can_auto_recover=True when active TeX distribution is TinyTeX."""
    discovery = _create_mock_discovery(is_tinytex=True, tex_distribution_name="TinyTeX")
    mock_manager = MagicMock(spec=ITinyTexManager)

    service = PackageRecoveryService(discovery=discovery, tinytex_manager=mock_manager)
    plan = service.create_recovery_plan(["fancyhdr", "tcolorbox"])

    assert isinstance(plan, PackageRecoveryPlanDTO)
    assert plan.can_auto_recover is True
    assert plan.manual_command == ""
    assert plan.missing_packages == ("fancyhdr", "tcolorbox")
    assert "TinyTeX" in plan.diagnostic_summary


def test_plan_system_tex_provides_manual_instructions():
    """Asserts can_auto_recover=False and returns manual apt command for Linux system TeX Live."""
    discovery = _create_mock_discovery(is_tinytex=False, tex_distribution_name="TeX Live")
    mock_manager = MagicMock(spec=ITinyTexManager)

    service = PackageRecoveryService(discovery=discovery, tinytex_manager=mock_manager)

    with patch("sys.platform", "linux"):
        plan = service.create_recovery_plan(["tikz", "fancyhdr"])

    assert isinstance(plan, PackageRecoveryPlanDTO)
    assert plan.can_auto_recover is False
    assert "sudo apt install" in plan.manual_command
    assert "texlive-pictures" in plan.manual_command
    assert "texlive-latex-extra" in plan.manual_command
    assert "TeX Live" in plan.diagnostic_summary


def test_plan_miktex_provides_manual_instructions():
    """Asserts can_auto_recover=False and returns manual mpm command for MiKTeX."""
    discovery = _create_mock_discovery(is_tinytex=False, tex_distribution_name="MiKTeX")
    mock_manager = MagicMock(spec=ITinyTexManager)

    service = PackageRecoveryService(discovery=discovery, tinytex_manager=mock_manager)
    plan = service.create_recovery_plan(["microtype"])

    assert plan.can_auto_recover is False
    assert "mpm --install microtype" in plan.manual_command
    assert "MiKTeX" in plan.diagnostic_summary


def test_plan_empty_packages_noop():
    """Asserts empty package list returns noop recovery plan."""
    discovery = _create_mock_discovery(is_tinytex=True)
    mock_manager = MagicMock(spec=ITinyTexManager)

    service = PackageRecoveryService(discovery=discovery, tinytex_manager=mock_manager)
    plan = service.create_recovery_plan([])

    assert plan.can_auto_recover is True
    assert plan.missing_packages == ()
    assert plan.manual_command == ""


def test_execute_recovery_success():
    """Mocks successful tlmgr install and post-install revalidation."""
    discovery = _create_mock_discovery(is_tinytex=True)
    mock_manager = MagicMock(spec=ITinyTexManager)
    mock_manager.install_packages.return_value = PackageInstallResult(
        success=True,
        installed_packages=("fancyhdr", "tcolorbox"),
        failed_packages=(),
        error_message="",
    )

    service = PackageRecoveryService(discovery=discovery, tinytex_manager=mock_manager)
    request = PackageRecoveryRequest(packages=("fancyhdr", "tcolorbox"))

    result = service.execute_recovery(request)

    assert isinstance(result, PackageRecoveryResultDTO)
    assert result.success is True
    assert result.installed_packages == ("fancyhdr", "tcolorbox")
    assert result.failed_packages == ()
    assert result.error_message == ""

    mock_manager.install_packages.assert_called_once_with(
        package_names=("fancyhdr", "tcolorbox"),
        is_cancelled=None,
    )
    # Probed once for pre-flight and once for post-install verification
    assert discovery.probe_readiness.call_count >= 2


def test_execute_recovery_offline_fails_fast():
    """Mocks network error during install, asserts OfflineRecoveryError diagnostic."""
    discovery = _create_mock_discovery(is_tinytex=True)
    mock_manager = MagicMock(spec=ITinyTexManager)
    mock_manager.install_packages.side_effect = OfflineRecoveryError(
        "LaTeX package recovery failed: workstation is offline or CTAN is unreachable."
    )

    service = PackageRecoveryService(discovery=discovery, tinytex_manager=mock_manager)
    request = PackageRecoveryRequest(packages=("tcolorbox",))

    # Test DTO return (standard UI path)
    result = service.execute_recovery(request)
    assert result.success is False
    assert result.failed_packages == ("tcolorbox",)
    assert "OfflineRecoveryError" in result.error_message
    assert "offline" in result.error_message.lower() or "ctan" in result.error_message.lower()

    # Test raise_on_error path
    with pytest.raises(OfflineRecoveryError) as exc_info:
        service.execute_recovery(request, raise_on_error=True)
    assert "offline" in str(exc_info.value).lower() or "ctan" in str(exc_info.value).lower()


def test_execute_recovery_cancellation():
    """Triggers cancellation callback; asserts process cancellation handled cleanly."""
    discovery = _create_mock_discovery(is_tinytex=True)
    mock_manager = MagicMock(spec=ITinyTexManager)
    mock_manager.install_packages.side_effect = PdfExportCancelledError(
        "Package recovery was cancelled by user."
    )

    service = PackageRecoveryService(discovery=discovery, tinytex_manager=mock_manager)
    request = PackageRecoveryRequest(
        packages=("geometry",),
        is_cancelled=lambda: True,
    )

    result = service.execute_recovery(request)
    assert result.success is False
    assert "cancelled" in result.error_message.lower()

    with pytest.raises(PdfExportCancelledError):
        service.execute_recovery(request, raise_on_error=True)


def test_execute_recovery_non_tinytex_rejected():
    """Asserts that attempting automated recovery on non-TinyTeX distribution fails fast."""
    discovery = _create_mock_discovery(is_tinytex=False, tex_distribution_name="TeX Live")
    mock_manager = MagicMock(spec=ITinyTexManager)

    service = PackageRecoveryService(discovery=discovery, tinytex_manager=mock_manager)
    request = PackageRecoveryRequest(packages=("microtype",))

    result = service.execute_recovery(request)
    assert result.success is False
    assert "TinyTeX" in result.error_message
    mock_manager.install_packages.assert_not_called()

    with pytest.raises(DomainError):
        service.execute_recovery(request, raise_on_error=True)


def test_execute_recovery_empty_packages_noop():
    """Empty package request returns success no-op immediately."""
    discovery = _create_mock_discovery(is_tinytex=True)
    mock_manager = MagicMock(spec=ITinyTexManager)

    service = PackageRecoveryService(discovery=discovery, tinytex_manager=mock_manager)
    request = PackageRecoveryRequest(packages=())

    result = service.execute_recovery(request)
    assert result.success is True
    assert result.installed_packages == ()
    mock_manager.install_packages.assert_not_called()


def test_execute_recovery_tlmgr_error_result():
    """Asserts that non-zero tlmgr execution failure returns result DTO with failure details."""
    discovery = _create_mock_discovery(is_tinytex=True)
    mock_manager = MagicMock(spec=ITinyTexManager)
    mock_manager.install_packages.return_value = PackageInstallResult(
        success=False,
        installed_packages=(),
        failed_packages=("unknownpkg",),
        error_message="package unknownpkg not found in repository",
    )

    service = PackageRecoveryService(discovery=discovery, tinytex_manager=mock_manager)
    request = PackageRecoveryRequest(packages=("unknownpkg",))

    result = service.execute_recovery(request)
    assert result.success is False
    assert result.failed_packages == ("unknownpkg",)
    assert "unknownpkg not found" in result.error_message
