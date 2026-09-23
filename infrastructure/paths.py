"""Path resolution for application assets and runtime binaries.

Provides deterministic location of bundled third-party runtimes (Pandoc, Node.js)
and packaged assets across both development checkout and frozen binary bundles.
"""

from __future__ import annotations

import sys
from pathlib import Path


def get_runtime_resource_path(relative_path: str | Path) -> Path:
    """Resolve the absolute path to a bundled runtime binary or static resource.

    In a frozen bundle (e.g. PyInstaller or Briefcase):
    - Under PyInstaller, resources are extracted/packaged at `sys._MEIPASS`.
    - Under Briefcase or other frozen frameworks without `_MEIPASS`, resources
      are resolved relative to the executable root.

    In unfrozen development checkout:
    - Resources are resolved relative to the repository root (two levels above
      `infrastructure/paths.py`).

    Args:
        relative_path: Relative path string or Path from the application root
                       (e.g., 'resources/runtimes.json').

    Returns:
        Resolved absolute Path.
    """
    path_obj = Path(relative_path)
    if path_obj.is_absolute():
        return path_obj

    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS") and sys._MEIPASS:
            base_dir = Path(sys._MEIPASS)
        else:
            base_dir = Path(sys.executable).resolve().parent
    else:
        # Development checkout: repo root is the parent of the infrastructure directory
        base_dir = Path(__file__).resolve().parent.parent

    return (base_dir / path_obj).resolve()
