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


class MarkdownParserError(PandocError):
    """Raised when Markdown parsing via external subprocess fails."""

    def __init__(self, message: str, returncode: int | None = None, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class MarkdownParserTimeoutError(MarkdownParserError):
    """Raised when Markdown parsing subprocess times out."""

    def __init__(self, message: str, timeout_seconds: float = 5.0, stderr: str = "") -> None:
        super().__init__(message, returncode=None, stderr=stderr)
        self.timeout_seconds = timeout_seconds
