# ============================================================
#  infrastructure/pdf/tinytex_manager.py
#  Consented Asynchronous TinyTeX Package Recovery Manager
#  Phase 13 — Ticket #104
# ============================================================

"""
TinyTeX Package Recovery Manager.

Security Trust Boundary Disclosure (Mandatory F-03):
---------------------------------------------------
Child processes execute under ordinary desktop user OS privileges.
Package installation is executed strictly with explicit user consent for the
specific missing package set. Automatic root or administrative elevation (sudo)
is strictly prohibited.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import platform
import re
import shutil
from typing import Callable, List, Optional, Sequence

from application.ports.pdf_export_port import (
    IPdfRuntimeDiscovery,
    ITinyTexManager,
    PackageInstallResult,
)
from core.exceptions.domain_exceptions import (
    OfflineRecoveryError,
    PdfExportCancelledError,
)
from infrastructure.pdf.runtime_discovery import QuartoRuntimeDiscovery
from infrastructure.pdf.subprocess_supervisor import SubprocessSupervisor

logger = logging.getLogger(__name__)

_DEFAULT_RECOVERY_TIMEOUT_SECONDS: float = 120.0

_OFFLINE_PATTERNS = [
    r"ctan\s+unreachable",
    r"cannot\s+reach\s+ctan",
    r"could\s+not\s+connect",
    r"failed\s+to\s+download",
    r"temporary\s+failure\s+in\s+name\s+resolution",
    r"getaddrinfo\s+failed",
    r"no\s+address\s+associated",
    r"network\s+is\s+unreachable",
    r"connection\s+refused",
    r"connection\s+timed\s+out",
    r"tlmgr:\s*ctan\s*unreachable",
    r"server\s+not\s+reachable",
    r"dns\s+resolution\s+failed",
]

_OFFLINE_REGEX = re.compile("|".join(_OFFLINE_PATTERNS), re.IGNORECASE)


class TinyTexManager(ITinyTexManager):
    """
    Infrastructure manager for executing consented TinyTeX package installations.

    Implements executable resolution (quarto tools tlmgr vs standalone tlmgr),
    single grouped command execution via SubprocessSupervisor, offline/network
    failure detection, timeout governance, and post-installation readiness verification.
    """

    def __init__(
        self,
        supervisor: Optional[SubprocessSupervisor] = None,
        discovery: Optional[IPdfRuntimeDiscovery] = None,
        timeout_seconds: float = _DEFAULT_RECOVERY_TIMEOUT_SECONDS,
    ) -> None:
        self._supervisor = supervisor or SubprocessSupervisor()
        self._discovery = discovery or QuartoRuntimeDiscovery()
        self._timeout_seconds = timeout_seconds

    def install_packages(
        self,
        package_names: Sequence[str],
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> PackageInstallResult:
        """
        Installs missing LaTeX packages into the active TinyTeX distribution.

        Args:
            package_names: Sequence of package names to install.
            is_cancelled: Optional cooperative cancellation check callable.

        Returns:
            PackageInstallResult indicating outcome.

        Raises:
            PdfExportCancelledError: If recovery is cancelled by user.
            OfflineRecoveryError: If network connectivity or CTAN is unreachable.
        """
        clean_packages = sorted(set(p.strip() for p in package_names if p.strip()))
        if not clean_packages:
            return PackageInstallResult(
                success=True,
                installed_packages=(),
                failed_packages=(),
                error_message="",
            )

        if is_cancelled and is_cancelled():
            raise PdfExportCancelledError("Package recovery was cancelled by user.")

        cmd = self._resolve_install_command(clean_packages)
        logger.info("Executing TinyTeX package recovery: %s", " ".join(cmd))

        result = self._supervisor.execute(
            cmd=cmd,
            cwd=Path.home(),
            timeout_seconds=self._timeout_seconds,
            is_cancelled=is_cancelled,
        )

        if result.cancelled:
            raise PdfExportCancelledError("Package recovery was cancelled by user.")

        if result.timed_out:
            return PackageInstallResult(
                success=False,
                installed_packages=(),
                failed_packages=tuple(clean_packages),
                error_message=(
                    f"TinyTeX package recovery timed out after {self._timeout_seconds:.1f} seconds."
                ),
            )

        combined_output = f"{result.stdout}\n{result.stderr}"

        # Network / Offline Detection
        if _OFFLINE_REGEX.search(combined_output):
            err_detail = result.stderr.strip() or result.stdout.strip()
            raise OfflineRecoveryError(
                f"LaTeX package recovery failed: workstation is offline or CTAN is unreachable. "
                f"Output: {err_detail}"
            )

        if result.returncode != 0:
            err_msg = (
                result.stderr.strip()
                or result.stdout.strip()
                or f"tlmgr process exited with code {result.returncode}"
            )
            return PackageInstallResult(
                success=False,
                installed_packages=(),
                failed_packages=tuple(clean_packages),
                error_message=err_msg,
            )

        # Post-install verification: re-probe environment
        try:
            self._discovery.probe_readiness()
        except Exception as e:
            logger.warning("Post-install readiness revalidation encountered non-fatal error: %s", e)

        return PackageInstallResult(
            success=True,
            installed_packages=tuple(clean_packages),
            failed_packages=(),
            error_message="",
        )

    def _resolve_install_command(self, package_names: Sequence[str]) -> List[str]:
        """Resolves the executable command for tlmgr package installation."""
        is_windows = platform.system() == "Windows"
        try:
            readiness = self._discovery.probe_readiness()
            if readiness.quarto_available and readiness.quarto_path:
                return [readiness.quarto_path, "tools", "tlmgr", "install", *package_names]
        except Exception:
            pass

        # Standalone tlmgr on system PATH
        candidates = ["tlmgr.bat", "tlmgr.exe", "tlmgr"] if is_windows else ["tlmgr"]
        for cand in candidates:
            resolved = shutil.which(cand)
            if resolved:
                return [resolved, "install", *package_names]

        # Scan standard user TinyTeX paths
        home = Path.home()
        tinytex_search = [
            home / ".TinyTeX" / "bin",
            home / "AppData" / "Roaming" / "TinyTeX" / "bin",
            home / "AppData" / "Local" / "TinyTeX" / "bin",
            home / "AppData" / "Local" / "Programs" / "TinyTeX" / "bin",
        ]
        for base in tinytex_search:
            try:
                if base.exists():
                    for root, _, files in os.walk(base):
                        for file in files:
                            if file.lower() in ("tlmgr.bat", "tlmgr.exe", "tlmgr"):
                                return [str(Path(root) / file), "install", *package_names]
            except Exception:
                pass

        return ["tlmgr", "install", *package_names]
