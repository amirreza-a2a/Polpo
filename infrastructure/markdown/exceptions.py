"""Exceptions for markdown processing and Pandoc external subprocess execution.

All exceptions provide structured, actionable English diagnostics for missing or
incompatible external binaries.
"""

from __future__ import annotations

from pathlib import Path


class PandocError(Exception):
    """Base exception for all Pandoc-related execution and discovery errors."""


class PandocNotFoundError(PandocError):
    """Raised when the Pandoc binary cannot be located across all search paths."""


class PandocIncompatibleError(PandocError):
    """Raised when a Pandoc binary is found but fails capability or version verification."""

    def __init__(self, message: str, binary_path: Path | str | None = None) -> None:
        super().__init__(message)
        self.binary_path = Path(binary_path) if binary_path is not None else None
