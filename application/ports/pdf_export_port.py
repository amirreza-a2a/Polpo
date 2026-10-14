# ============================================================
#  application/ports/pdf_export_port.py
#  Abstract Application Ports for PDF Export Subsystem
#  Phase 13 — Ticket #98
# ============================================================

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from application.dto.pdf_readiness_dto import EngineReadinessDTO


class IPdfRuntimeDiscovery(ABC):
    """Port interface for discovering external Quarto and TeX runtimes."""

    @abstractmethod
    def probe_readiness(
        self,
        configured_quarto_path: Optional[str] = None,
    ) -> EngineReadinessDTO:
        """
        Probes the host environment for Quarto and XeLaTeX runtimes.

        Args:
            configured_quarto_path: Optional path configured by user in AppSettings.

        Returns:
            EngineReadinessDTO containing availability, versions, and diagnostics.
        """
        raise NotImplementedError
