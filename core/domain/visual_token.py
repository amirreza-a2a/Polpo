# ============================================================
#  core/domain/visual_token.py
#  Canonical Visual Occurrence Token Grammar & Immutable Value Objects
# ============================================================

from dataclasses import dataclass
from enum import Enum
import re
from typing import Optional, Tuple, Union
from uuid import UUID

# Strict lowercase 36-character hyphenated UUIDv4 regex
_UUIDV4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

__all__ = [
    "TokenDiagnosticType",
    "VisualOccurrenceToken",
    "classify_token_metadata",
    "parse_fields",
    "serialize_canonical_token",
    "unescape_alt_text",
    "validate_token_metadata",
    "validate_token_uuid",
]


class TokenDiagnosticType(str, Enum):
    """Classification of visual occurrence token syntax / diagnostic state."""
    CANONICAL = "canonical"
    MALFORMED_SYNTAX = "malformed_syntax"
    INVALID_UUID = "invalid_uuid"
    DUPLICATE_OCCURRENCE = "duplicate_occurrence"
    NON_MANAGED_IMAGE = "non_managed_image"


def validate_token_uuid(val: Union[str, UUID]) -> UUID:
    """
    Validates that a string or UUID strictly conforms to lowercase 36-character hyphenated UUIDv4.
    Raises ValueError if invalid, wrong version, uppercase, unhyphenated, or contains whitespace.
    """
    str_val = str(val)
    if not _UUIDV4_PATTERN.match(str_val):
        raise ValueError(f"Invalid UUIDv4 format (must be 36-character lowercase hyphenated UUIDv4): {val}")

    if isinstance(val, UUID):
        parsed = val
    else:
        try:
            parsed = UUID(str_val)
        except (ValueError, AttributeError, TypeError) as e:
            raise ValueError(f"Invalid UUIDv4 format: {val}") from e

    if parsed.version != 4:
        raise ValueError(f"Invalid UUIDv4: version is {parsed.version}, expected 4 ({val})")

    return parsed


def classify_token_metadata(title: str) -> Tuple[TokenDiagnosticType, Optional[UUID], Optional[UUID]]:
    """
    Validates and classifies the title attribute of a visual occurrence token.
    Enforces exact key ordering: 'region' first, 'occ' second. No extra or missing keys.
    Returns (diagnostic_type, region_id, occ_id).
    """
    if not title.startswith("polpo:"):
        return (TokenDiagnosticType.NON_MANAGED_IMAGE, None, None)

    metadata_str = title[len("polpo:"):]
    pairs = metadata_str.split(";")
    if len(pairs) != 2:
        return (TokenDiagnosticType.MALFORMED_SYNTAX, None, None)

    k1, _, v1 = pairs[0].partition("=")
    k2, _, v2 = pairs[1].partition("=")

    # Enforce exact key ordering: region first, occ second
    if k1 != "region" or k2 != "occ":
        return (TokenDiagnosticType.MALFORMED_SYNTAX, None, None)

    try:
        region_uuid = validate_token_uuid(v1)
        occ_uuid = validate_token_uuid(v2)
    except ValueError:
        return (TokenDiagnosticType.INVALID_UUID, None, None)

    return (TokenDiagnosticType.CANONICAL, region_uuid, occ_uuid)


def validate_token_metadata(title: str) -> Tuple[UUID, UUID]:
    """
    Validates that a title attribute conforms to canonical 'polpo:region=<...>;occ=<...>' format.
    Raises ValueError on any syntax error, incorrect key ordering, or invalid UUID.
    Returns (region_id, occurrence_id).
    """
    diag, region_id, occ_id = classify_token_metadata(title)
    if diag == TokenDiagnosticType.NON_MANAGED_IMAGE:
        raise ValueError(f"Not a managed polpo token: {title}")
    if diag == TokenDiagnosticType.MALFORMED_SYNTAX:
        raise ValueError(f"Malformed token metadata (must have 'region' then 'occ' separated by ';'): {title}")
    if diag == TokenDiagnosticType.INVALID_UUID:
        raise ValueError(f"Invalid UUIDv4 in token metadata: {title}")
    assert region_id is not None and occ_id is not None
    return region_id, occ_id


@dataclass(frozen=True)
class VisualOccurrenceToken:
    """
    Immutable value object representing a canonical visual occurrence in Markdown.
    Contains strictly the four core fields: region_id, occurrence_id, uri, alt_text.
    No mutable collections; fully hashable and comparable.
    """
    region_id: UUID
    occurrence_id: UUID
    uri: str
    alt_text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "region_id", validate_token_uuid(self.region_id))
        object.__setattr__(self, "occurrence_id", validate_token_uuid(self.occurrence_id))
        if not isinstance(self.uri, str):
            raise TypeError("uri must be a str")
        if not isinstance(self.alt_text, str):
            raise TypeError("alt_text must be a str")


def _escape_alt_text(alt: str) -> str:
    """
    Escapes alt text for canonical CommonMark serialization:
    - Normalizes newlines to spaces.
    - Escapes literal backslashes.
    - Escapes literal opening and closing brackets.
    """
    normalized = re.sub(r"[\r\n]+", " ", alt)
    escaped = normalized.replace("\\", "\\\\")
    escaped = escaped.replace("[", "\\[").replace("]", "\\]")
    return escaped


def unescape_alt_text(alt: str) -> str:
    """
    Reverses escaping applied to alt text:
    \\] -> ], \\[ -> [, \\\\ -> \\
    """
    # Replace escaped brackets and backslashes
    unescaped = alt.replace("\\]", "]").replace("\\[", "[")
    unescaped = unescaped.replace("\\\\", "\\")
    return unescaped


_unescape_alt_text = unescape_alt_text


def _escape_uri(uri: str) -> str:
    """
    Escapes URI for canonical CommonMark destination:
    - Percent-encodes spaces.
    - Percent-encodes literal parentheses to avoid CommonMark destination truncation.
    """
    cleaned = uri.strip()
    cleaned = cleaned.replace(" ", "%20")
    cleaned = cleaned.replace("(", "%28").replace(")", "%29")
    return cleaned


def serialize_canonical_token(token: VisualOccurrenceToken) -> str:
    """
    Deterministically serializes a VisualOccurrenceToken into the canonical CommonMark syntax:
    ![<alt_text>](<destination_uri> "polpo:region=<region_uuid>;occ=<occ_uuid>")
    """
    escaped_alt = _escape_alt_text(token.alt_text)
    escaped_uri = _escape_uri(token.uri)
    title = f"polpo:region={token.region_id};occ={token.occurrence_id}"
    return f'![{escaped_alt}]({escaped_uri} "{title}")'


# Pattern to extract components from a token string: ![alt](destination "title")
# Alt delimiter must be a closing bracket ']' that is not escaped (preceded by an even number of backslashes)
_TOKEN_STRING_PATTERN = re.compile(
    r"^!\[(?P<alt>.*?(?<!\\)(?:\\\\)*)\]\((?P<uri>\S+?)(?:\s+\"(?P<title>[^\"]*)\")?\)$",
    re.DOTALL,
)


def parse_fields(token_string: str) -> VisualOccurrenceToken:
    """
    Parses a CommonMark image token string and extracts its canonical fields.
    Validates strict key ordering ('region' first, 'occ' second) and UUIDv4 format.
    Returns a valid VisualOccurrenceToken.
    Raises ValueError if syntax is invalid or required metadata is missing.
    """
    match = _TOKEN_STRING_PATTERN.match(token_string.strip())
    if not match:
        raise ValueError(f"Invalid Markdown image token syntax: {token_string}")

    raw_alt = match.group("alt")
    raw_uri = match.group("uri")
    raw_title = match.group("title")

    if not raw_title:
        raise ValueError(f"Missing required title metadata in token: {token_string}")

    region_id, occ_id = validate_token_metadata(raw_title)
    unescaped_alt = unescape_alt_text(raw_alt)

    return VisualOccurrenceToken(
        region_id=region_id,
        occurrence_id=occ_id,
        uri=raw_uri,
        alt_text=unescaped_alt,
    )
