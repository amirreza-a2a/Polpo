# ============================================================
#  tests/unit/test_tinytex_manager.py
#  Unit tests for TinyTexManager
#  Phase 13 — Ticket #104
# ============================================================

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from application.dto.pdf_readiness_dto import EngineReadinessDTO
from application.ports.pdf_export_port import IPdfRuntimeDiscovery
from core.exceptions.domain_exceptions import (
    OfflineRecoveryError,
    PdfExportCancelledError,
)
from infrastructure.pdf.subprocess_supervisor import (
    SubprocessResult,
    SubprocessSupervisor,
)
from infrastructure.pdf.tinytex_manager import TinyTexManager


def _create_mock_discovery(
    quarto_available: bool = True,
    quarto_path: str = "/opt/quarto/bin/quarto",
    is_tinytex: bool = True,
    tex_distribution_name: str = "TinyTeX",
) -> MagicMock:
    discovery = MagicMock(spec=IPdfRuntimeDiscovery)
    discovery.probe_readiness.return_value = EngineReadinessDTO(
        quarto_available=quarto_available,
        quarto_version="1.5.0",
        quarto_path=quarto_path if quarto_available else None,
        xelatex_available=True,
        is_tinytex=is_tinytex,
        tex_distribution_name=tex_distribution_name,
    )
    return discovery


def test_tinytex_manager_empty_packages_noop():
    """Empty or whitespace-only package list returns success immediately without supervisor call."""
    mock_supervisor = MagicMock(spec=SubprocessSupervisor)
    mock_discovery = _create_mock_discovery()

    manager = TinyTexManager(supervisor=mock_supervisor, discovery=mock_discovery)
    result = manager.install_packages([])

    assert result.success is True
    assert result.installed_packages == ()
    mock_supervisor.execute.assert_not_called()


def test_tinytex_manager_resolve_command_quarto():
    """Resolves 'quarto tools tlmgr install <pkgs>' when Quarto is available."""
    mock_supervisor = MagicMock(spec=SubprocessSupervisor)
    mock_supervisor.execute.return_value = SubprocessResult(
        returncode=0,
        stdout="[1/1] Installed package: fancyhdr\n",
        stderr="",
    )
    mock_discovery = _create_mock_discovery(
        quarto_available=True,
        quarto_path="/usr/local/bin/quarto",
    )

    manager = TinyTexManager(supervisor=mock_supervisor, discovery=mock_discovery)
    result = manager.install_packages(["fancyhdr", "tcolorbox"])

    assert result.success is True
    assert result.installed_packages == ("fancyhdr", "tcolorbox")

    mock_supervisor.execute.assert_called_once()
    called_cmd = mock_supervisor.execute.call_args.kwargs["cmd"]
    assert called_cmd == ["/usr/local/bin/quarto", "tools", "tlmgr", "install", "fancyhdr", "tcolorbox"]


def test_tinytex_manager_resolve_command_standalone_tlmgr():
    """Resolves standalone 'tlmgr install <pkgs>' when Quarto is not available."""
    mock_supervisor = MagicMock(spec=SubprocessSupervisor)
    mock_supervisor.execute.return_value = SubprocessResult(
        returncode=0,
        stdout="tlmgr: package installed\n",
        stderr="",
    )
    mock_discovery = _create_mock_discovery(quarto_available=False, quarto_path=None)

    with patch("shutil.which", return_value="/usr/bin/tlmgr"):
        manager = TinyTexManager(supervisor=mock_supervisor, discovery=mock_discovery)
        result = manager.install_packages(["amsmath"])

    assert result.success is True
    called_cmd = mock_supervisor.execute.call_args.kwargs["cmd"]
    assert called_cmd == ["/usr/bin/tlmgr", "install", "amsmath"]


def test_tinytex_manager_execute_success():
    """Successful tlmgr run returns PackageInstallResult with installed packages."""
    mock_supervisor = MagicMock(spec=SubprocessSupervisor)
    mock_supervisor.execute.return_value = SubprocessResult(
        returncode=0,
        stdout="tlmgr install: package microtype installed\n",
        stderr="",
    )
    mock_discovery = _create_mock_discovery()

    manager = TinyTexManager(supervisor=mock_supervisor, discovery=mock_discovery)
    result = manager.install_packages(["microtype"])

    assert result.success is True
    assert result.installed_packages == ("microtype",)
    assert result.failed_packages == ()
    assert result.error_message == ""
    # Post-install re-probe executed
    assert mock_discovery.probe_readiness.call_count >= 2


def test_tinytex_manager_offline_detection():
    """Detects CTAN unreachable network error and raises OfflineRecoveryError."""
    mock_supervisor = MagicMock(spec=SubprocessSupervisor)
    mock_supervisor.execute.return_value = SubprocessResult(
        returncode=1,
        stdout="",
        stderr="tlmgr: CTAN unreachable, please check your network connection\n",
    )
    mock_discovery = _create_mock_discovery()

    manager = TinyTexManager(supervisor=mock_supervisor, discovery=mock_discovery)

    with pytest.raises(OfflineRecoveryError) as exc_info:
        manager.install_packages(["tcolorbox"])

    assert "CTAN" in str(exc_info.value) or "offline" in str(exc_info.value).lower()


def test_tinytex_manager_dns_failure_offline_detection():
    """Detects DNS / network unreachability error and raises OfflineRecoveryError."""
    mock_supervisor = MagicMock(spec=SubprocessSupervisor)
    mock_supervisor.execute.return_value = SubprocessResult(
        returncode=2,
        stdout="",
        stderr="getaddrinfo failed: Temporary failure in name resolution\n",
    )
    mock_discovery = _create_mock_discovery()

    manager = TinyTexManager(supervisor=mock_supervisor, discovery=mock_discovery)

    with pytest.raises(OfflineRecoveryError) as exc_info:
        manager.install_packages(["fontspec"])

    assert "offline" in str(exc_info.value).lower() or "unreachable" in str(exc_info.value).lower()


def test_tinytex_manager_cancellation_before_execution():
    """Raises PdfExportCancelledError if cancellation signal is active prior to supervisor call."""
    mock_supervisor = MagicMock(spec=SubprocessSupervisor)
    mock_discovery = _create_mock_discovery()

    manager = TinyTexManager(supervisor=mock_supervisor, discovery=mock_discovery)

    with pytest.raises(PdfExportCancelledError):
        manager.install_packages(["listings"], is_cancelled=lambda: True)

    mock_supervisor.execute.assert_not_called()


def test_tinytex_manager_cancellation_during_execution():
    """Raises PdfExportCancelledError if supervisor returns cancelled=True."""
    mock_supervisor = MagicMock(spec=SubprocessSupervisor)
    mock_supervisor.execute.return_value = SubprocessResult(
        returncode=-1,
        stdout="",
        stderr="",
        cancelled=True,
    )
    mock_discovery = _create_mock_discovery()

    manager = TinyTexManager(supervisor=mock_supervisor, discovery=mock_discovery)

    with pytest.raises(PdfExportCancelledError):
        manager.install_packages(["listings"])


def test_tinytex_manager_timeout():
    """Returns PackageInstallResult with success=False when supervisor reports timeout."""
    mock_supervisor = MagicMock(spec=SubprocessSupervisor)
    mock_supervisor.execute.return_value = SubprocessResult(
        returncode=-1,
        stdout="",
        stderr="",
        timed_out=True,
    )
    mock_discovery = _create_mock_discovery()

    manager = TinyTexManager(
        supervisor=mock_supervisor,
        discovery=mock_discovery,
        timeout_seconds=30.0,
    )
    result = manager.install_packages(["pgf"])

    assert result.success is False
    assert result.failed_packages == ("pgf",)
    assert "timed out" in result.error_message.lower()


def test_tinytex_manager_exit_code_failure():
    """Returns PackageInstallResult with success=False when tlmgr returns non-zero without offline pattern."""
    mock_supervisor = MagicMock(spec=SubprocessSupervisor)
    mock_supervisor.execute.return_value = SubprocessResult(
        returncode=1,
        stdout="",
        stderr="tlmgr: package nonexistentpkg not found in repository\n",
    )
    mock_discovery = _create_mock_discovery()

    manager = TinyTexManager(supervisor=mock_supervisor, discovery=mock_discovery)
    result = manager.install_packages(["nonexistentpkg"])

    assert result.success is False
    assert result.failed_packages == ("nonexistentpkg",)
    assert "nonexistentpkg" in result.error_message
