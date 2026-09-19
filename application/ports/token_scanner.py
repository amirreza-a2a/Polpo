# ============================================================
#  application/ports/token_scanner.py
#  Application port interface & DTOs for Visual Token Scanner
# ============================================================

from dataclasses import dataclass
from typing import Protocol, Tuple
from core.domain.visual_token import TokenDiagnosticType, VisualOccurrenceToken


@dataclass(frozen=True)
class ScannedOccurrenceTokenDTO:
    """Represents a successfully scanned canonical visual occurrence token with source offsets."""
    token: VisualOccurrenceToken
    start_char: int
    end_char: int
    raw_text: str


@dataclass(frozen=True)
class MalformedOccurrenceTokenDTO:
    """Represents a malformed or non-canonical visual occurrence token with diagnostic information."""
    diagnostic_type: TokenDiagnosticType
    start_char: int
    end_char: int
    raw_text: str
    error_message: str


@dataclass(frozen=True)
class ScanResultDTO:
    """Aggregated result of scanning a Markdown document for visual occurrence tokens."""
    tokens: Tuple[ScannedOccurrenceTokenDTO, ...]
    malformed_tokens: Tuple[MalformedOccurrenceTokenDTO, ...]
    duplicate_occurrence_ids: Tuple[str, ...]


class ITokenScanner(Protocol):
    """
    Port for character-level lexical discovery and replacement of visual occurrence tokens.
    """

    def scan(self, text: str) -> ScanResultDTO:
        """
        Scans raw Markdown text character-by-character and returns all discovered tokens,
        malformed token diagnostics, and duplicate occurrence IDs.
        """
        ...

    def replace_span(
        self,
        text: str,
        start_char: int,
        end_char: int,
        replacement: VisualOccurrenceToken,
    ) -> str:
        """
        Replaces the source span [start_char:end_char] with the serialized replacement token,
        guaranteeing character fidelity for all characters outside the span.
        """
        ...
