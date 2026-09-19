# ============================================================
#  application/dto/token_dto.py
#  Transport-neutral DTOs for Visual Occurrence Tokens
# ============================================================

from dataclasses import dataclass
from uuid import UUID
from core.domain.visual_token import VisualOccurrenceToken


@dataclass(frozen=True)
class VisualOccurrenceTokenDTO:
    """
    Transport-neutral DTO representation of a VisualOccurrenceToken.
    Uses primitive strings for cross-boundary serialization (controllers, workers, UI).
    """
    region_id: str
    occurrence_id: str
    uri: str
    alt_text: str

    @classmethod
    def from_domain(cls, token: VisualOccurrenceToken) -> "VisualOccurrenceTokenDTO":
        return cls(
            region_id=str(token.region_id),
            occurrence_id=str(token.occurrence_id),
            uri=token.uri,
            alt_text=token.alt_text,
        )

    def to_domain(self) -> VisualOccurrenceToken:
        return VisualOccurrenceToken(
            region_id=UUID(self.region_id),
            occurrence_id=UUID(self.occurrence_id),
            uri=self.uri,
            alt_text=self.alt_text,
        )
