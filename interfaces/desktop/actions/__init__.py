# ============================================================
#  interfaces/desktop/actions/__init__.py
#  Presentation Action Models & Providers
# ============================================================

from interfaces.desktop.actions.region_actions import (
    RegionActionDescriptor,
    RegionActionProvider,
    normalize_region_status,
)

__all__ = [
    "RegionActionDescriptor",
    "RegionActionProvider",
    "normalize_region_status",
]
