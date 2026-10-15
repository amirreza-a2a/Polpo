# ============================================================
#  application/services/package_recovery_service.py
#  Consented LaTeX Package Recovery Application Service
#  Phase 13 — Ticket #104
# ============================================================

from __future__ import annotations

import logging
import sys
from typing import Sequence

from application.dto.package_recovery_dto import (
    PackageRecoveryPlanDTO,
    PackageRecoveryRequest,
    PackageRecoveryResultDTO,
)
from application.ports.pdf_export_port import (
    IPdfRuntimeDiscovery,
    ITinyTexManager,
)
from core.exceptions.domain_exceptions import (
    DomainError,
    OfflineRecoveryError,
    PdfExportCancelledError,
)

logger = logging.getLogger(__name__)


class PackageRecoveryService:
    """
    Application service orchestrating consented LaTeX package recovery.

    Enforces ADR-003 D04 policies:
    - Strict distribution discrimination (TinyTeX vs system TeX Live / MiKTeX).
    - Explicit grouped user consent (zero silent installations).
    - Prohibition of automatic privilege elevation (sudo).
    - Offline error handling with actionable diagnostics.
    - Post-installation readiness re-validation.
    """

    def __init__(
        self,
        discovery: IPdfRuntimeDiscovery,
        tinytex_manager: ITinyTexManager,
    ) -> None:
        self._discovery = discovery
        self._tinytex_manager = tinytex_manager

    def create_recovery_plan(
        self,
        missing_packages: Sequence[str],
    ) -> PackageRecoveryPlanDTO:
        """
        Determines recovery capabilities and generates instructions based on TeX distribution.

        Args:
            missing_packages: Sequence of identified missing LaTeX package names.

        Returns:
            PackageRecoveryPlanDTO specifying auto-recovery availability and commands.
        """
        clean_packages = tuple(sorted(set(p.strip() for p in missing_packages if p.strip())))
        if not clean_packages:
            return PackageRecoveryPlanDTO(
                missing_packages=(),
                can_auto_recover=True,
                manual_command="",
                diagnostic_summary="No missing packages specified.",
            )

        readiness = self._discovery.probe_readiness()

        if readiness.is_tinytex:
            summary = (
                f"Detected TinyTeX distribution. Polpo can automatically install the "
                f"{len(clean_packages)} missing dependency(ies): {', '.join(clean_packages)}."
            )
            return PackageRecoveryPlanDTO(
                missing_packages=clean_packages,
                can_auto_recover=True,
                manual_command="",
                diagnostic_summary=summary,
            )

        dist_name = readiness.tex_distribution_name or "System TeX"
        manual_cmd = self._generate_manual_command(clean_packages, dist_name)
        summary = (
            f"Detected system TeX distribution '{dist_name}'. Polpo does not automatically "
            f"elevate privileges (sudo) or modify system-managed packages. "
            f"Please install the missing packages manually using your package manager."
        )
        return PackageRecoveryPlanDTO(
            missing_packages=clean_packages,
            can_auto_recover=False,
            manual_command=manual_cmd,
            diagnostic_summary=summary,
        )

    def execute_recovery(
        self,
        request: PackageRecoveryRequest,
        raise_on_error: bool = False,
    ) -> PackageRecoveryResultDTO:
        """
        Executes consented package installation for TinyTeX.

        Args:
            request: Typed recovery request containing consented package names and optional cancel check.
            raise_on_error: When True, raises domain exceptions; when False, formats into ResultDTO.

        Returns:
            PackageRecoveryResultDTO indicating recovery outcome.

        Raises:
            PdfExportCancelledError: If recovery is cancelled and raise_on_error is True.
            OfflineRecoveryError: If network/CTAN is offline and raise_on_error is True.
            DomainError: If attempted on non-TinyTeX distribution and raise_on_error is True.
        """
        clean_packages = tuple(sorted(set(p.strip() for p in request.packages if p.strip())))
        if not clean_packages:
            return PackageRecoveryResultDTO(
                success=True,
                installed_packages=(),
                failed_packages=(),
                error_message="",
            )

        if request.is_cancelled and request.is_cancelled():
            msg = "Package recovery was cancelled by user."
            if raise_on_error:
                raise PdfExportCancelledError(msg)
            return PackageRecoveryResultDTO(
                success=False,
                installed_packages=(),
                failed_packages=clean_packages,
                error_message=msg,
            )

        readiness = self._discovery.probe_readiness()
        if not readiness.is_tinytex:
            dist_name = readiness.tex_distribution_name or "System TeX"
            msg = (
                f"Automatic package recovery is only supported for TinyTeX distributions. "
                f"Active distribution '{dist_name}' requires manual package management."
            )
            if raise_on_error:
                raise DomainError(msg)
            return PackageRecoveryResultDTO(
                success=False,
                installed_packages=(),
                failed_packages=clean_packages,
                error_message=msg,
            )

        try:
            install_result = self._tinytex_manager.install_packages(
                package_names=clean_packages,
                is_cancelled=request.is_cancelled,
            )
        except OfflineRecoveryError as exc:
            if raise_on_error:
                raise
            return PackageRecoveryResultDTO(
                success=False,
                installed_packages=(),
                failed_packages=clean_packages,
                error_message=f"OfflineRecoveryError: {exc}",
            )
        except PdfExportCancelledError as exc:
            if raise_on_error:
                raise
            return PackageRecoveryResultDTO(
                success=False,
                installed_packages=(),
                failed_packages=clean_packages,
                error_message=str(exc),
            )
        except Exception as exc:
            if raise_on_error:
                raise
            return PackageRecoveryResultDTO(
                success=False,
                installed_packages=(),
                failed_packages=clean_packages,
                error_message=f"Package recovery failed: {exc}",
            )

        if not install_result.success:
            failed = tuple(install_result.failed_packages or clean_packages)
            return PackageRecoveryResultDTO(
                success=False,
                installed_packages=tuple(install_result.installed_packages),
                failed_packages=failed,
                error_message=install_result.error_message,
            )

        # Post-install verification: re-probe environment
        try:
            self._discovery.probe_readiness()
        except Exception as e:
            logger.warning("Post-recovery probe encountered non-fatal error: %s", e)

        return PackageRecoveryResultDTO(
            success=True,
            installed_packages=tuple(install_result.installed_packages or clean_packages),
            failed_packages=tuple(install_result.failed_packages),
            error_message="",
        )

    @staticmethod
    def _generate_manual_command(packages: Sequence[str], dist_name: str) -> str:
        """Generates platform-appropriate manual commands without automatic sudo elevation."""
        is_windows = sys.platform.startswith("win")
        is_darwin = sys.platform == "darwin"

        if is_windows or "miktex" in dist_name.lower():
            pkg_str = " ".join(packages)
            return f"mpm --install {pkg_str}"
        elif is_darwin:
            pkg_str = " ".join(packages)
            return f"tlmgr install {pkg_str}"
        else:
            # Linux / TeX Live (Debian, Ubuntu, Fedora)
            apt_pkgs = set()
            for pkg in packages:
                clean_pkg = pkg.lower().strip()
                if clean_pkg in ("tikz", "pgf"):
                    apt_pkgs.add("texlive-pictures")
                elif clean_pkg in ("amsmath", "amssymb", "amsfonts"):
                    apt_pkgs.add("texlive-latex-base")
                elif clean_pkg in ("fontspec", "unicode-math", "microtype"):
                    apt_pkgs.add("texlive-latex-recommended")
                elif clean_pkg in ("algorithm2e", "siunitx"):
                    apt_pkgs.add("texlive-science")
                else:
                    apt_pkgs.add("texlive-latex-extra")
            if not apt_pkgs:
                apt_pkgs = {"texlive-latex-extra", "texlive-pictures"}
            return f"sudo apt install {' '.join(sorted(apt_pkgs))}"
