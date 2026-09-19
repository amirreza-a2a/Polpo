# ============================================================
#  tests/unit/test_visual_occurrence_token_scanner.py
#  Tests for Lossless Character-Level Visual Token Micro-Lexer (FSM)
# ============================================================

from uuid import UUID
import pytest

from core.domain.visual_token import (
    VisualOccurrenceToken,
    TokenDiagnosticType,
    serialize_canonical_token,
)
from application.ports.token_scanner import (
    ITokenScanner,
    ScanResultDTO,
    ScannedOccurrenceTokenDTO,
    MalformedOccurrenceTokenDTO,
)
from infrastructure.document.visual_occurrence_token_scanner import (
    VisualOccurrenceTokenScanner,
)


@pytest.fixture
def scanner() -> ITokenScanner:
    return VisualOccurrenceTokenScanner()


def test_scanner_basic_discovery(scanner: ITokenScanner):
    reg_id = "12345678-1234-4234-8234-123456789abc"
    occ_id = "abcdef12-abcd-4bcd-8bcd-abcdef123456"
    raw_token = f'![Figure 1](file:///path/crop.jpg "polpo:region={reg_id};occ={occ_id}")'
    doc = f"# Title\n\nSome text before {raw_token} and text after."

    result: ScanResultDTO = scanner.scan(doc)

    assert len(result.tokens) == 1
    scanned = result.tokens[0]
    assert scanned.token.region_id == UUID(reg_id)
    assert scanned.token.occurrence_id == UUID(occ_id)
    assert scanned.token.uri == "file:///path/crop.jpg"
    assert scanned.token.alt_text == "Figure 1"
    assert scanned.raw_text == raw_token
    assert doc[scanned.start_char:scanned.end_char] == raw_token


def test_scanner_multiple_tokens_single_line(scanner: ITokenScanner):
    r1, o1 = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    r2, o2 = "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444"
    tok1 = f'![One](file:///1.jpg "polpo:region={r1};occ={o1}")'
    tok2 = f'![Two](file:///2.jpg "polpo:region={r2};occ={o2}")'
    line = f"Start {tok1} middle text {tok2} end."

    result = scanner.scan(line)
    assert len(result.tokens) == 2
    assert result.tokens[0].token.occurrence_id == UUID(o1)
    assert line[result.tokens[0].start_char:result.tokens[0].end_char] == tok1
    assert result.tokens[1].token.occurrence_id == UUID(o2)
    assert line[result.tokens[1].start_char:result.tokens[1].end_char] == tok2


def test_scanner_character_spans_exact(scanner: ITokenScanner):
    r, o = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    tok = f'![Exact](file:///img.png "polpo:region={r};occ={o}")'
    prefix = "A" * 15
    suffix = "B" * 25
    doc = prefix + tok + suffix

    result = scanner.scan(doc)
    assert len(result.tokens) == 1
    scanned = result.tokens[0]
    assert scanned.start_char == 15
    assert scanned.end_char == 15 + len(tok)
    assert doc[scanned.start_char:scanned.end_char] == tok


def test_scanner_escaped_brackets_in_alt(scanner: ITokenScanner):
    r, o = "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    raw_token = f'![Formula \\[A\\] and \\[B\\]](file:///formula.png "polpo:region={r};occ={o}")'
    doc = f"See {raw_token} for details."

    result = scanner.scan(doc)
    assert len(result.tokens) == 1
    assert result.tokens[0].token.alt_text == "Formula [A] and [B]"
    assert doc[result.tokens[0].start_char:result.tokens[0].end_char] == raw_token


def test_scanner_escaped_quotes_in_title(scanner: ITokenScanner):
    # In canonical polpo titles quotes aren't allowed inside UUIDs, but if someone has escaped quotes in standard syntax
    r, o = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "ffffffff-ffff-4fff-8fff-ffffffffffff"
    # Malformed title with quotes inside metadata
    raw = f'![Test](file:///test.png "polpo:region={r};occ=\\"bad\\"")'
    result = scanner.scan(raw)
    # Should diagnose as malformed or invalid UUID without crashing
    assert len(result.tokens) == 0
    assert len(result.malformed_tokens) == 1
    assert result.malformed_tokens[0].diagnostic_type in (
        TokenDiagnosticType.MALFORMED_SYNTAX,
        TokenDiagnosticType.INVALID_UUID,
    )


def test_scanner_balanced_parentheses_in_uri(scanner: ITokenScanner):
    r, o = "12121212-1212-4212-8212-121212121212", "34343434-3434-4434-8434-343434343434"
    raw_token = f'![Parens](file:///path/to/image(1).jpg "polpo:region={r};occ={o}")'
    doc = f"Image: {raw_token} here."

    result = scanner.scan(doc)
    assert len(result.tokens) == 1
    assert result.tokens[0].token.uri == "file:///path/to/image(1).jpg"
    assert doc[result.tokens[0].start_char:result.tokens[0].end_char] == raw_token


def test_scanner_multiline_image_syntax(scanner: ITokenScanner):
    r, o = "56565656-5656-4656-8656-565656565656", "78787878-7878-4788-8788-787878787878"
    raw_token = f'![Multiline\nAlt\nText](file:///multi.jpg\n"polpo:region={r};occ={o}")'
    doc = f"Start\n{raw_token}\nEnd"

    result = scanner.scan(doc)
    assert len(result.tokens) == 1
    assert "Multiline Alt Text" in result.tokens[0].token.alt_text
    assert doc[result.tokens[0].start_char:result.tokens[0].end_char] == raw_token


def test_scanner_unicode_persian_alt(scanner: ITokenScanner):
    r, o = "90909090-9090-4090-8090-909090909090", "abababab-abab-4bab-8bab-abababababab"
    raw_token = f'![شکل ۱: مدار تقویت‌کننده](file:///circuit.jpg "polpo:region={r};occ={o}")'
    doc = f"در اینجا {raw_token} آورده شده است."

    result = scanner.scan(doc)
    assert len(result.tokens) == 1
    assert result.tokens[0].token.alt_text == "شکل ۱: مدار تقویت‌کننده"
    assert doc[result.tokens[0].start_char:result.tokens[0].end_char] == raw_token


def test_scanner_malformed_diagnostics(scanner: ITokenScanner):
    # Inverted key ordering
    r, o = "12345678-1234-4234-8234-123456789abc", "abcdef12-abcd-4bcd-8bcd-abcdef123456"
    inverted = f'![Bad Order](file:///bad.jpg "polpo:occ={o};region={r}")'
    # Bad UUID
    bad_uuid = f'![Bad UUID](file:///bad2.jpg "polpo:region=not-a-uuid;occ={o}")'
    doc = f"{inverted}\n{bad_uuid}"

    result = scanner.scan(doc)
    assert len(result.tokens) == 0
    assert len(result.malformed_tokens) == 2
    assert result.malformed_tokens[0].diagnostic_type == TokenDiagnosticType.MALFORMED_SYNTAX
    assert result.malformed_tokens[1].diagnostic_type == TokenDiagnosticType.INVALID_UUID


def test_scanner_duplicate_occurrence_detection(scanner: ITokenScanner):
    r1, o = "11111111-1111-4111-8111-111111111111", "99999999-9999-4999-8999-999999999999"
    r2 = "22222222-2222-4222-8222-222222222222"
    tok1 = f'![First](file:///1.jpg "polpo:region={r1};occ={o}")'
    tok2 = f'![Second](file:///2.jpg "polpo:region={r2};occ={o}")'
    doc = f"{tok1}\n\n{tok2}"

    result = scanner.scan(doc)
    assert len(result.tokens) == 2
    assert str(UUID(o)) in result.duplicate_occurrence_ids


def test_scanner_ignores_non_managed_images(scanner: ITokenScanner):
    standard1 = "![Standard](https://example.com/pic.jpg)"
    standard2 = '![Titled](https://example.com/pic.jpg "A standard title")'
    r, o = "12345678-1234-4234-8234-123456789abc", "abcdef12-abcd-4bcd-8bcd-abcdef123456"
    managed = f'![Managed](file:///crop.jpg "polpo:region={r};occ={o}")'
    doc = f"{standard1}\n\n{managed}\n\n{standard2}"

    result = scanner.scan(doc)
    assert len(result.tokens) == 1
    assert result.tokens[0].token.occurrence_id == UUID(o)
    assert len(result.malformed_tokens) == 0


def test_scanner_lossless_span_replacement(scanner: ITokenScanner):
    r1, o1 = "11111111-1111-4111-8111-111111111111", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    r2, o2 = "22222222-2222-4222-8222-222222222222", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    tok1 = f'![Old](file:///old.jpg "polpo:region={r1};occ={o1}")'
    doc = f"Prefix text with formatting *bold* and _italic_.\n\n{tok1}\n\nSuffix text."

    result = scanner.scan(doc)
    assert len(result.tokens) == 1
    scanned = result.tokens[0]

    replacement_token = VisualOccurrenceToken(
        region_id=UUID(r2),
        occurrence_id=UUID(o2),
        uri="file:///new.jpg",
        alt_text="New Alt",
    )

    new_doc = scanner.replace_span(
        doc,
        start_char=scanned.start_char,
        end_char=scanned.end_char,
        replacement=replacement_token,
    )

    # Character fidelity invariant:
    assert new_doc[:scanned.start_char] == doc[:scanned.start_char]
    new_token_str = serialize_canonical_token(replacement_token)
    assert new_doc[scanned.start_char:scanned.start_char + len(new_token_str)] == new_token_str
    assert new_doc[scanned.start_char + len(new_token_str):] == doc[scanned.end_char:]


def test_scanner_replacement_multiple_tokens(scanner: ITokenScanner):
    r1, o1 = "11111111-1111-4111-8111-111111111111", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    r2, o2 = "22222222-2222-4222-8222-222222222222", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    tok1 = f'![One](file:///1.jpg "polpo:region={r1};occ={o1}")'
    tok2 = f'![Two](file:///2.jpg "polpo:region={r2};occ={o2}")'
    doc = f"Head\n{tok1}\nMiddle\n{tok2}\nTail"

    result = scanner.scan(doc)
    assert len(result.tokens) == 2

    # Replace token 2 only
    new_tok2 = VisualOccurrenceToken(
        region_id=UUID(r2),
        occurrence_id=UUID(o2),
        uri="file:///2_recropped.jpg",
        alt_text="Two Recropped",
    )
    new_doc = scanner.replace_span(
        doc,
        start_char=result.tokens[1].start_char,
        end_char=result.tokens[1].end_char,
        replacement=new_tok2,
    )

    # Re-scan to verify
    re_result = scanner.scan(new_doc)
    assert len(re_result.tokens) == 2
    assert re_result.tokens[0].raw_text == tok1
    assert re_result.tokens[1].token.uri == "file:///2_recropped.jpg"
    assert re_result.tokens[1].token.alt_text == "Two Recropped"
    assert "Head\n" in new_doc
    assert "\nMiddle\n" in new_doc
    assert "\nTail" in new_doc
