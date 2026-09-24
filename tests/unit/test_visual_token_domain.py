# ============================================================
#  tests/unit/test_visual_token_domain.py
#  Tests for Canonical Visual Token Grammar & Immutable Value Objects
# ============================================================

import uuid
from uuid import UUID
import pytest

from core.domain.visual_token import (
    VisualOccurrenceToken,
    TokenDiagnosticType,
    serialize_canonical_token,
    unescape_alt_text,
    validate_token_uuid,
    validate_token_metadata,
    classify_token_metadata,
    parse_fields,
)
from application.dto.token_dto import VisualOccurrenceTokenDTO
import core.domain as domain_pkg


def test_canonical_token_serialization():
    region_id = UUID("12345678-1234-4234-8234-123456789abc")
    occ_id = UUID("abcdef12-abcd-4bcd-8bcd-abcdef123456")
    token = VisualOccurrenceToken(
        region_id=region_id,
        occurrence_id=occ_id,
        uri="file:///path/to/crop.jpg",
        alt_text="Figure 1",
    )
    serialized = serialize_canonical_token(token)
    assert serialized == (
        '![Figure 1](file:///path/to/crop.jpg "polpo:region=12345678-1234-4234-8234-123456789abc;occ=abcdef12-abcd-4bcd-8bcd-abcdef123456")'
    )


def test_token_metadata_key_ordering():
    region_id = UUID("11111111-1111-4111-8111-111111111111")
    occ_id = UUID("22222222-2222-4222-8222-222222222222")
    token = VisualOccurrenceToken(
        region_id=region_id,
        occurrence_id=occ_id,
        uri="file:///img.png",
        alt_text="test",
    )
    result = serialize_canonical_token(token)
    # Must enforce region first, then occ
    expected_title = 'polpo:region=11111111-1111-4111-8111-111111111111;occ=22222222-2222-4222-8222-222222222222'
    assert f'"{expected_title}"' in result


def test_token_persian_and_unicode_alt():
    region_id = UUID("33333333-3333-4333-8333-333333333333")
    occ_id = UUID("44444444-4444-4444-8444-444444444444")
    token = VisualOccurrenceToken(
        region_id=region_id,
        occurrence_id=occ_id,
        uri="file:///test.png",
        alt_text="نمودار مدار الکتریکی و جریان (I_1)",
    )
    serialized = serialize_canonical_token(token)
    assert "نمودار مدار الکتریکی و جریان (I_1)" in serialized


def test_token_escaped_quotes_and_brackets():
    region_id = UUID("55555555-5555-4555-8555-555555555555")
    occ_id = UUID("66666666-6666-4666-8666-666666666666")
    # Alt text contains brackets, newlines, and quotes; URI contains space and parens
    token = VisualOccurrenceToken(
        region_id=region_id,
        occurrence_id=occ_id,
        uri="file:///path/to/my image (1).jpg",
        alt_text='Sample [bracketed] "quoted"\nmultiline',
    )
    serialized = serialize_canonical_token(token)
    # Brackets escaped in alt, newlines normalized to space, quotes preserved
    assert r'Sample \[bracketed\] "quoted" multiline' in serialized
    # Spaces in URI percent-encoded, literal parentheses percent-encoded
    assert "file:///path/to/my%20image%20%281%29.jpg" in serialized


def test_token_hashability_and_equality():
    region_id = UUID("77777777-7777-4777-8777-777777777777")
    occ_id = UUID("88888888-8888-4888-8888-888888888888")
    token1 = VisualOccurrenceToken(
        region_id=region_id,
        occurrence_id=occ_id,
        uri="file:///a.jpg",
        alt_text="alt",
    )
    token2 = VisualOccurrenceToken(
        region_id=region_id,
        occurrence_id=occ_id,
        uri="file:///a.jpg",
        alt_text="alt",
    )
    token3 = VisualOccurrenceToken(
        region_id=region_id,
        occurrence_id=uuid.uuid4(),
        uri="file:///a.jpg",
        alt_text="alt",
    )
    assert token1 == token2
    assert token1 != token3
    assert hash(token1) == hash(token2)
    s = {token1}
    assert token2 in s
    assert token3 not in s


def test_token_strict_uuidv4_validation():
    with pytest.raises(ValueError, match="Invalid UUIDv4"):
        validate_token_uuid("not-a-uuid")

    # UUIDv1, not v4
    with pytest.raises(ValueError, match="Invalid UUIDv4"):
        validate_token_uuid("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

    # Unhyphenated 32-char hex must be rejected
    with pytest.raises(ValueError, match="Invalid UUIDv4"):
        validate_token_uuid("12345678123442348234123456789abc")

    # Uppercase hex must be rejected
    with pytest.raises(ValueError, match="Invalid UUIDv4"):
        validate_token_uuid("12345678-1234-4234-8234-123456789ABC")

    # Leading/trailing whitespace must be strictly rejected
    with pytest.raises(ValueError, match="Invalid UUIDv4"):
        validate_token_uuid(" 12345678-1234-4234-8234-123456789abc ")

    with pytest.raises(ValueError, match="Invalid UUIDv4"):
        validate_token_uuid("12345678-1234-4234-8234-123456789abc\n")

    valid_v4 = "12345678-1234-4234-8234-123456789abc"
    parsed = validate_token_uuid(valid_v4)
    assert isinstance(parsed, UUID)
    assert str(parsed) == valid_v4


def test_token_metadata_validation_and_classification():
    reg_str = "12345678-1234-4234-8234-123456789abc"
    occ_str = "abcdef12-abcd-4bcd-8bcd-abcdef123456"

    # Canonical
    valid_title = f"polpo:region={reg_str};occ={occ_str}"
    diag, r_id, o_id = classify_token_metadata(valid_title)
    assert diag == TokenDiagnosticType.CANONICAL
    assert str(r_id) == reg_str
    assert str(o_id) == occ_str

    r_parsed, o_parsed = validate_token_metadata(valid_title)
    assert str(r_parsed) == reg_str
    assert str(o_parsed) == occ_str

    # Whitespace in key-value pairs must be rejected (Zero whitespace rule)
    whitespace_title = f"polpo:region= {reg_str};occ={occ_str}"
    diag, _, _ = classify_token_metadata(whitespace_title)
    assert diag == TokenDiagnosticType.INVALID_UUID
    with pytest.raises(ValueError, match="Invalid UUIDv4"):
        validate_token_metadata(whitespace_title)

    whitespace_title_2 = f"polpo:region={reg_str};occ= {occ_str}"
    diag, _, _ = classify_token_metadata(whitespace_title_2)
    assert diag == TokenDiagnosticType.INVALID_UUID

    # Non-managed (no polpo: prefix)
    diag, _, _ = classify_token_metadata("Standard Image Title")
    assert diag == TokenDiagnosticType.NON_MANAGED_IMAGE
    with pytest.raises(ValueError, match="Not a managed polpo token"):
        validate_token_metadata("Standard Image Title")

    # Reversed key ordering: occ before region (MUST REJECT)
    reversed_title = f"polpo:occ={occ_str};region={reg_str}"
    diag, _, _ = classify_token_metadata(reversed_title)
    assert diag == TokenDiagnosticType.MALFORMED_SYNTAX
    with pytest.raises(ValueError, match="Malformed token metadata"):
        validate_token_metadata(reversed_title)

    # Missing occ key
    missing_key_title = f"polpo:region={reg_str}"
    diag, _, _ = classify_token_metadata(missing_key_title)
    assert diag == TokenDiagnosticType.MALFORMED_SYNTAX

    # Extra key
    extra_key_title = f"polpo:region={reg_str};occ={occ_str};extra=val"
    diag, _, _ = classify_token_metadata(extra_key_title)
    assert diag == TokenDiagnosticType.MALFORMED_SYNTAX

    # Invalid UUID in region
    invalid_uuid_title = f"polpo:region=bad-uuid;occ={occ_str}"
    diag, _, _ = classify_token_metadata(invalid_uuid_title)
    assert diag == TokenDiagnosticType.INVALID_UUID
    with pytest.raises(ValueError, match="Invalid UUIDv4"):
        validate_token_metadata(invalid_uuid_title)


def test_token_round_trip():
    region_id = UUID("12345678-1234-4234-8234-123456789abc")
    occ_id = UUID("abcdef12-abcd-4bcd-8bcd-abcdef123456")

    # Test cases including brackets followed by parens
    test_alts = [
        "Figure [1]: Schematic Description",
        "Diagram [1](overview)",
        "Alt with [bracketed] and (parentheses) and [more]",
        'Alt with "quotes" and [brackets]',
    ]

    for alt in test_alts:
        original_token = VisualOccurrenceToken(
            region_id=region_id,
            occurrence_id=occ_id,
            uri="file:///path/to/image.jpg",
            alt_text=alt,
        )

        # 1. Serialize domain token
        serialized = serialize_canonical_token(original_token)

        # 2. Parse serialized string back into domain token
        parsed_token = parse_fields(serialized)

        # 3. Assert equality
        assert parsed_token == original_token
        assert parsed_token.alt_text == alt
        assert parsed_token.uri == "file:///path/to/image.jpg"
        assert parsed_token.region_id == region_id
        assert parsed_token.occurrence_id == occ_id

        # 4. Re-serializing parsed token produces identical canonical output
        re_serialized = serialize_canonical_token(parsed_token)
        assert re_serialized == serialized


def test_parse_fields_validation_failures():
    # Non-image markdown string
    with pytest.raises(ValueError, match="Invalid Markdown image token syntax"):
        parse_fields("[Link Text](file:///doc.pdf)")

    # Image missing title attribute
    with pytest.raises(ValueError, match="Missing required title metadata in token"):
        parse_fields("![Alt Text](file:///crop.jpg)")

    # Image with non-polpo title
    with pytest.raises(ValueError, match="Not a managed polpo token"):
        parse_fields('![Alt Text](file:///crop.jpg "Standard Title")')

    # Image with malformed metadata keys
    with pytest.raises(ValueError, match="Malformed token metadata"):
        parse_fields('![Alt Text](file:///crop.jpg "polpo:occ=abcdef12-abcd-4bcd-8bcd-abcdef123456;region=12345678-1234-4234-8234-123456789abc")')

    # Image with invalid UUID in metadata
    with pytest.raises(ValueError, match="Invalid UUIDv4"):
        parse_fields('![Alt Text](file:///crop.jpg "polpo:region=invalid;occ=abcdef12-abcd-4bcd-8bcd-abcdef123456")')


def test_token_dto_mapping():
    region_id = UUID("99999999-9999-4999-8999-999999999999")
    occ_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    token = VisualOccurrenceToken(
        region_id=region_id,
        occurrence_id=occ_id,
        uri="file:///img.png",
        alt_text="alt",
    )
    dto = VisualOccurrenceTokenDTO.from_domain(token)
    assert dto.region_id == str(region_id)
    assert dto.occurrence_id == str(occ_id)
    assert dto.uri == "file:///img.png"
    assert dto.alt_text == "alt"

    restored = dto.to_domain()
    assert restored == token


def test_unescape_alt_text():
    assert unescape_alt_text(r"Figure \[A\] and \[B\]") == "Figure [A] and [B]"
    assert unescape_alt_text(r"Path\\to\\file") == r"Path\to\file"
    assert unescape_alt_text(r"Simple Title") == "Simple Title"


def test_package_all_exports():
    expected_symbols = [
        "TokenDiagnosticType",
        "VisualOccurrenceToken",
        "classify_token_metadata",
        "parse_fields",
        "serialize_canonical_token",
        "validate_token_metadata",
        "validate_token_uuid",
    ]
    for sym in expected_symbols:
        assert hasattr(domain_pkg, sym), f"Missing public export: {sym}"
    assert set(domain_pkg.__all__) == set(expected_symbols)
