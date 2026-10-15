# ============================================================
#  application/dto/package_recovery_dto.py
#  Package Recovery DTOs for PDF Subsystem
#  Phase 13 — Ticket #104
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple


@dataclass(frozen=True)
class PackageRecoveryPlanDTO:
    """
    Plan describing recovery options for identified missing LaTeX packages.

    Attributes:
        missing_packages: Tuple of identified missing LaTeX package names.
        can_auto_recover: True if active distribution supports automatic installation (TinyTeX).
        manual_command: Copyable terminal command for manual package installation.
        diagnostic_summary: Human-readable diagnostic explaining recovery strategy.
    """
    missing_packages: Tuple[str, ...]
    can_auto_recover: bool
    manual_command: str = ""
    diagnostic_summary: str = ""


@dataclass(frozen=True)
class PackageRecoveryRequest:
    """
    Request DTO initiating consented package recovery.

    Attributes:
        packages: Tuple of explicitly consented LaTeX package names to install.
        is_cancelled: Optional cooperative cancellation callback.
    """
    packages: Tuple[str, ...]
    is_cancelled: Optional[Callable[[], bool]] = None


@dataclass(frozen=True)
class PackageRecoveryResultDTO:
    """
    Result DTO representing the outcome of package recovery.

    Attributes:
        success: True if all requested packages were successfully installed.
        installed_packages: Tuple of successfully installed package names.
        failed_packages: Tuple of package names that failed to install.
        error_message: Actionable diagnostic or error message on failure.
    """
    success: bool
    installed_packages: Tuple[str, ...] = ()
    failed_packages: Tuple[str, ...] = ()
    error_message: str = ""
