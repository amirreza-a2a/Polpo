# ============================================================
#  tests/unit/test_visual_token_mutator.py
#  Tests for Pure Canonical Markdown Visual Token Mutator
# ============================================================

import pytest

from core.exceptions.domain_exceptions import AmbiguousVisualTokenError
from core.markdown.visual_token_mutator import (
    remove_visual_token,
    upsert_visual_token,
)


REG_UUID_STR = "550e8400-e29b-41d4-a716-446655440000"
REG_HEX_STR = "550e8400e29b41d4a716446655440000"
OCC_UUID_STR = "6ba7b810-9dad-41d1-80b4-00c04fd430c8"
OCC_HEX_STR = "6ba7b8109dad41d180b400c04fd430c8"


# ------------------------------------------------------------
# 1. Existing Canonical Token Replacement & Byte Preservation
# ------------------------------------------------------------

def test_upsert_replaces_existing_canonical_token_in_place():
    prefix = "# Heading\n\nSome introductory paragraph.\n\n"
    old_token = f'![Old Alt](crop_old.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    suffix = "\n\nFollowing paragraph text that must remain byte-identical."
    doc = prefix + old_token + suffix

    new_artifact = "crop_550e8400e29b41d4a716446655440000_v2.jpg"
    new_alt = "New Figure Title"

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri=new_artifact,
        page_number=1,
        alt_text=new_alt,
    )

    expected_token = f'![{new_alt}]({new_artifact} "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    assert mutated == prefix + expected_token + suffix
    # Strict byte preservation check outside mutation slice
    assert mutated[:len(prefix)] == doc[:len(prefix)]
    assert mutated[len(prefix) + len(expected_token):] == doc[len(prefix) + len(old_token):]


def test_upsert_accepts_32_hex_region_and_occurrence_ids():
    prefix = "<!-- Page 1 -->\n"
    old_token = f'![Fig](old.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    suffix = "Paragraph."
    doc = prefix + old_token + suffix

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_HEX_STR,
        occurrence_id=OCC_HEX_STR,
        artifact_uri="new.jpg",
        page_number=1,
        alt_text="Fig",
    )

    # Output must be normalized to canonical 36-char hyphenated UUIDv4
    expected_token = f'![Fig](new.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert mutated == prefix + expected_token + suffix


def test_upsert_canonical_token_idempotent():
    token = f'![Chart](chart_v1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    doc = f"<!-- Page 1 -->\n{token}Some content."

    # Running upsert with identical metadata produces byte-identical output
    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="chart_v1.jpg",
        page_number=1,
        alt_text="Chart",
    )
    assert mutated == doc


# ------------------------------------------------------------
# 2. Insertion After Real Page Markers
# ------------------------------------------------------------

def test_upsert_inserts_immediately_after_page_marker():
    doc = (
        "# Document\n\n"
        "<!-- Page 1 -->\n"
        "Page 1 first paragraph.\n\n"
        "<!-- Page 2 -->\n"
        "Page 2 first paragraph.\n"
    )

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop_p2.jpg",
        page_number=2,
        alt_text="Page 2 Diagram",
    )

    expected_token = f'![Page 2 Diagram](crop_p2.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    expected_doc = (
        "# Document\n\n"
        "<!-- Page 1 -->\n"
        "Page 1 first paragraph.\n\n"
        "<!-- Page 2 -->\n"
        + expected_token +
        "Page 2 first paragraph.\n"
    )
    assert mutated == expected_doc


def test_upsert_page_marker_at_document_start():
    doc = "<!-- Page 1 -->\nContent goes here."

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop_p1.jpg",
        page_number=1,
    )

    expected_token = f'![](crop_p1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert mutated == f"<!-- Page 1 -->\n{expected_token}Content goes here."


def test_upsert_page_marker_at_document_end_without_trailing_newline():
    doc = "Some header\n<!-- Page 1 -->"

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop_p1.jpg",
        page_number=1,
    )

    expected_token = f'![](crop_p1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert mutated == f"Some header\n<!-- Page 1 -->\n{expected_token}"


def test_upsert_fallback_appends_when_page_marker_missing():
    doc = "# Doc Without Markers\n\nParagraph text."

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop_orphan.jpg",
        page_number=3,
        alt_text="Orphan Crop",
    )

    expected_token = f'![Orphan Crop](crop_orphan.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert mutated == f"# Doc Without Markers\n\nParagraph text.\n\n{expected_token}"
    assert mutated.startswith(doc)


# ------------------------------------------------------------
# 3. Opaque Context Masking (Fenced Code, Inline Code, Comments)
# ------------------------------------------------------------

def test_upsert_ignores_fake_page_markers_inside_fenced_code():
    doc = (
        "# Code Sample\n\n"
        "```python\n"
        "# Fake marker:\n"
        "<!-- Page 1 -->\n"
        "print('hello')\n"
        "```\n\n"
        "<!-- Page 1 -->\n"
        "Real Page 1 text.\n"
    )

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="real_crop.jpg",
        page_number=1,
    )

    token_str = f'![](real_crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    # Must NOT insert inside python code fence!
    assert "```python\n# Fake marker:\n<!-- Page 1 -->\nprint('hello')\n```" in mutated
    # Must insert after the real <!-- Page 1 -->
    assert f"<!-- Page 1 -->\n{token_str}Real Page 1 text." in mutated


def test_upsert_ignores_fake_page_markers_inside_tilde_fences():
    doc = (
        "~~~markdown\n"
        "<!-- Page 1 -->\n"
        "~~~\n\n"
        "<!-- Page 1 -->\n"
        "Real content.\n"
    )

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop.jpg",
        page_number=1,
    )

    token_str = f'![](crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert f"~~~markdown\n<!-- Page 1 -->\n~~~" in mutated
    assert f"<!-- Page 1 -->\n{token_str}Real content." in mutated


def test_upsert_ignores_fake_page_markers_inside_inline_code():
    doc = (
        "Check this command `cat <!-- Page 1 -->` for details.\n\n"
        "<!-- Page 1 -->\n"
        "Actual page.\n"
    )

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop.jpg",
        page_number=1,
    )

    token_str = f'![](crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert "`cat <!-- Page 1 -->`" in mutated
    assert f"<!-- Page 1 -->\n{token_str}Actual page." in mutated


def test_upsert_ignores_tokens_inside_fenced_code():
    fake_token = f'![Fake](fake.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = (
        "Here is documentation:\n\n"
        "```markdown\n"
        f"Example token: {fake_token}\n"
        "```\n\n"
        "<!-- Page 1 -->\n"
        "Text.\n"
    )

    # Since the token inside code is opaque, upsert treats the region as not yet existing in document!
    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="real.jpg",
        page_number=1,
    )

    # The code block must remain 100% byte-identical
    assert fake_token in mutated
    # A real token must be inserted after <!-- Page 1 -->
    real_token = f'![](real.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert f"<!-- Page 1 -->\n{real_token}Text.\n" in mutated


def test_upsert_ignores_tokens_inside_inline_code():
    fake_token = f'![Fake](fake.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = f"See inline `{fake_token}` in docs.\n\n<!-- Page 1 -->\n"

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="real.jpg",
        page_number=1,
    )

    assert f"`{fake_token}`" in mutated
    real_token = f'![](real.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert f"<!-- Page 1 -->\n{real_token}" in mutated


def test_upsert_ignores_tokens_and_markers_inside_non_polpo_html_comments():
    fake_token = f'![Commented](comment.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = (
        "<!-- TODO: non-polpo comment with fake page marker: Page 1\n"
        f"Also note: {fake_token}\n"
        "-->\n\n"
        "<!-- Page 1 -->\n"
        "Real page body.\n"
    )

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="active.jpg",
        page_number=1,
    )

    # HTML comment untouched
    assert fake_token in mutated
    real_token = f'![](active.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert f"<!-- Page 1 -->\n{real_token}Real page body.\n" in mutated


# ------------------------------------------------------------
# 4. Strict Legacy Token Migration
# ------------------------------------------------------------

def test_upsert_migrates_legacy_token_with_explicit_uuid():
    legacy_token = f"![[crop_{REG_HEX_STR}_v1.jpg]]"
    doc = f"<!-- Page 1 -->\nBefore {legacy_token} After."

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop_v2.jpg",
        page_number=1,
        alt_text="Migrated",
    )

    expected_canonical = f'![Migrated](crop_v2.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    assert mutated == f"<!-- Page 1 -->\nBefore {expected_canonical} After."


def test_upsert_migrates_legacy_token_with_resolved_target():
    legacy_token = "![[crop_job42_p1_1.jpg]]"
    doc = f"<!-- Page 1 -->\n{legacy_token}\nContent."

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop_canonical.jpg",
        page_number=1,
        alt_text="Figure 1",
        legacy_target="crop_job42_p1_1.jpg",
    )

    expected_canonical = f'![Figure 1](crop_canonical.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    assert mutated == f"<!-- Page 1 -->\n{expected_canonical}\nContent."


def test_upsert_does_not_migrate_unrelated_legacy_token_without_evidence():
    unrelated_legacy = "![[random_diagram.jpg]]"
    doc = f"<!-- Page 1 -->\n{unrelated_legacy}\nContent."

    # No legacy_target passed and unrelated image does not contain region UUID
    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop_canonical.jpg",
        page_number=1,
    )

    # Unrelated legacy token remains completely untouched!
    assert unrelated_legacy in mutated
    # A new token is inserted after the page marker
    new_token = f'![](crop_canonical.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert mutated == f"<!-- Page 1 -->\n{new_token}{unrelated_legacy}\nContent."


# ------------------------------------------------------------
# 5. Ambiguity Handling & Duplicate Tokens
# ------------------------------------------------------------

def test_upsert_raises_ambiguous_on_duplicate_canonical_tokens():
    tok1 = f'![Alt1](crop1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    tok2 = f'![Alt2](crop2.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = f"<!-- Page 1 -->\n{tok1}\nSome text\n{tok2}\n"

    with pytest.raises(AmbiguousVisualTokenError) as exc_info:
        upsert_visual_token(
            text=doc,
            region_id=REG_UUID_STR,
            occurrence_id=OCC_UUID_STR,
            artifact_uri="crop_new.jpg",
            page_number=1,
        )
    assert REG_UUID_STR in str(exc_info.value) or "Multiple" in str(exc_info.value)


def test_upsert_raises_ambiguous_on_duplicate_eligible_legacy_and_canonical():
    tok1 = f'![Alt](crop1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    tok2 = f"![[crop_{REG_HEX_STR}_v1.jpg]]"
    doc = f"{tok1}\n{tok2}"

    with pytest.raises(AmbiguousVisualTokenError):
        upsert_visual_token(
            text=doc,
            region_id=REG_UUID_STR,
            occurrence_id=OCC_UUID_STR,
            artifact_uri="crop_new.jpg",
            page_number=1,
        )


# ------------------------------------------------------------
# 6. Token Removal (Idempotence & Precision)
# ------------------------------------------------------------

def test_remove_token_standalone_line():
    token = f'![Diagram](crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = f"# Title\n\n<!-- Page 1 -->\n{token}\nParagraph text.\n"

    mutated = remove_visual_token(doc, REG_UUID_STR)

    assert token not in mutated
    assert mutated == "# Title\n\n<!-- Page 1 -->\nParagraph text.\n"


def test_remove_token_inline():
    token = f'![Diagram](crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = f"See figure {token} above."

    mutated = remove_visual_token(doc, REG_UUID_STR)

    assert token not in mutated
    assert mutated == "See figure  above."


def test_remove_token_idempotent_when_absent():
    doc = "# Title\n\n<!-- Page 1 -->\nSome text.\n"
    mutated = remove_visual_token(doc, REG_UUID_STR)
    assert mutated == doc


def test_remove_token_raises_on_duplicates():
    token1 = f'![D1](crop1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    token2 = f'![D2](crop2.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = f"{token1}\n{token2}"

    with pytest.raises(AmbiguousVisualTokenError):
        remove_visual_token(doc, REG_UUID_STR)


# ------------------------------------------------------------
# 7. Preservation of Multilingual Text, Emojis, LaTeX, Lists
# ------------------------------------------------------------

def test_multilingual_persian_and_math_byte_preservation():
    persian_text = "این یک متن فارسی برای آزمون است. شامل ریاضی: $E = mc^2$ و فرمول نمایش:\n$$\\sum_{i=1}^n i = \\frac{n(n+1)}{2}$$\n"
    emojis_and_code = "Emoji check: 🚀 🎯 📝 ✨ | Special symbols: «» — –\n"
    nested_list = (
        "- Item 1\n"
        "  - Nested item 1.1\n"
        "    - Deep item 1.1.1\n"
        "  - Nested item 1.2\n"
        "- Item 2\n"
    )
    old_token = f'![Old](old.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = persian_text + emojis_and_code + nested_list + f"\n{old_token}\n\nFooter note."

    new_token_str = f'![New](new.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="new.jpg",
        page_number=1,
        alt_text="New",
    )

    # Everything before old_token is byte-identical
    prefix = persian_text + emojis_and_code + nested_list + "\n"
    assert mutated[:len(prefix)] == doc[:len(prefix)]
    assert mutated == prefix + new_token_str + "\n\nFooter note."


def test_crlf_line_ending_preservation():
    doc = (
        "# Title\r\n\r\n"
        "<!-- Page 1 -->\r\n"
        "Content line 1.\r\n"
    )

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop.jpg",
        page_number=1,
    )

    expected_token = f'![](crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\r\n'
    assert "\r\n" in mutated
    # Ensures no single \n corrupts the CRLF document
    assert "\n" not in mutated.replace("\r\n", "")
    assert mutated == (
        "# Title\r\n\r\n"
        "<!-- Page 1 -->\r\n"
        + expected_token +
        "Content line 1.\r\n"
    )


# ------------------------------------------------------------
# 8. Boundary Conditions & Validations
# ------------------------------------------------------------

def test_invalid_page_number_raises_value_error():
    with pytest.raises(ValueError):
        upsert_visual_token(
            text="<!-- Page 1 -->\n",
            region_id=REG_UUID_STR,
            occurrence_id=OCC_UUID_STR,
            artifact_uri="crop.jpg",
            page_number=0,
        )

    with pytest.raises(ValueError):
        upsert_visual_token(
            text="<!-- Page 1 -->\n",
            region_id=REG_UUID_STR,
            occurrence_id=OCC_UUID_STR,
            artifact_uri="crop.jpg",
            page_number=-5,
        )


def test_invalid_uuid_raises_value_error():
    with pytest.raises(ValueError):
        upsert_visual_token(
            text="<!-- Page 1 -->\n",
            region_id="not-a-valid-uuid",
            occurrence_id=OCC_UUID_STR,
            artifact_uri="crop.jpg",
            page_number=1,
        )


def test_upsert_multiple_regions_same_page():
    reg2_uuid = "11111111-2222-4333-8444-555555555555"
    occ2_uuid = "66666666-7777-4888-8999-000000000000"

    doc = "<!-- Page 1 -->\nBody paragraph.\n"

    # Insert first region
    doc1 = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop1.jpg",
        page_number=1,
        alt_text="Figure 1",
    )
    assert f'![Figure 1](crop1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")' in doc1

    # Insert second region on same page
    doc2 = upsert_visual_token(
        text=doc1,
        region_id=reg2_uuid,
        occurrence_id=occ2_uuid,
        artifact_uri="crop2.jpg",
        page_number=1,
        alt_text="Figure 2",
    )

    # Both tokens must be present, no duplicate marker, no corrupted text
    assert f'![Figure 1](crop1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")' in doc2
    assert f'![Figure 2](crop2.jpg "polpo:region={reg2_uuid};occ={occ2_uuid}")' in doc2
    assert "Body paragraph." in doc2


def test_upsert_ignores_tokens_inside_indented_code_block():
    fake_token = f'![Indented](fake.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = (
        "Intro text.\n\n"
        "    # Indented code block\n"
        f"    echo '{fake_token}'\n\n"
        "<!-- Page 1 -->\n"
        "Actual text.\n"
    )

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="active.jpg",
        page_number=1,
    )

    assert fake_token in mutated
    real_token = f'![](active.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert f"<!-- Page 1 -->\n{real_token}Actual text.\n" in mutated


def test_upsert_ignores_non_standalone_inline_page_markers():
    # If a line has text before the comment, it is not a structural marker
    doc = (
        "Inline comment here <!-- Page 1 --> not a standalone marker\n\n"
        "<!-- Page 1 -->\n"
        "Target page.\n"
    )

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop.jpg",
        page_number=1,
    )

    token_str = f'![](crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert "Inline comment here <!-- Page 1 --> not a standalone marker" in mutated
    assert f"<!-- Page 1 -->\n{token_str}Target page.\n" in mutated


def test_upsert_escapes_brackets_in_alt_text():
    mutated = upsert_visual_token(
        text="<!-- Page 1 -->\n",
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop.jpg",
        page_number=1,
        alt_text="Figure [A] & [B]",
    )

    # Brackets must be escaped as \[ and \]
    assert r"![Figure \[A\] & \[B\]](crop.jpg" in mutated


def test_upsert_empty_document():
    mutated = upsert_visual_token(
        text="",
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop.jpg",
        page_number=1,
    )

    expected = f'![](crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert mutated == expected


def test_remove_token_first_line_of_document():
    token = f'![First](crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = f"{token}\nSecond line."

    mutated = remove_visual_token(doc, REG_UUID_STR)
    assert mutated == "Second line."


def test_remove_token_last_line_of_document_without_trailing_newline():
    token = f'![Last](crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = f"First line.\n{token}"

    mutated = remove_visual_token(doc, REG_UUID_STR)
    assert mutated == "First line."


def test_upsert_preserves_existing_alt_text_when_none_provided():
    original = f'![Figure 42: Original Description](old_crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = f"Heading\n\n{original}\n\nParagraph text."

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id="e06385b2-dc09-4ce4-897b-cf109c95eb48",
        artifact_uri="new_crop.jpg",
        page_number=1,
        alt_text=None,
    )

    expected_token = (
        '![Figure 42: Original Description](new_crop.jpg '
        f'"polpo:region={REG_UUID_STR};occ=e06385b2-dc09-4ce4-897b-cf109c95eb48")'
    )
    assert mutated == f"Heading\n\n{expected_token}\n\nParagraph text."


def test_upsert_overwrites_alt_text_when_explicitly_provided():
    original = f'![Figure 42: Old Description](old_crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = f"Heading\n\n{original}\n\nParagraph text."

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id="e06385b2-dc09-4ce4-897b-cf109c95eb48",
        artifact_uri="new_crop.jpg",
        page_number=1,
        alt_text="Figure 42: New Updated Description",
    )

    expected_token = (
        '![Figure 42: New Updated Description](new_crop.jpg '
        f'"polpo:region={REG_UUID_STR};occ=e06385b2-dc09-4ce4-897b-cf109c95eb48")'
    )
    assert mutated == f"Heading\n\n{expected_token}\n\nParagraph text."


def test_upsert_migrates_legacy_token_and_preserves_alt_text():
    legacy_token = f"![[crop_{REG_UUID_STR}.jpg|Diagram of Flow]]"
    doc = f"Before\n\n{legacy_token}\n\nAfter"

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crops/crop_canonical.jpg",
        page_number=1,
        alt_text=None,
    )

    expected_token = (
        '![Diagram of Flow](crops/crop_canonical.jpg '
        f'"polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    )
    assert mutated == f"Before\n\n{expected_token}\n\nAfter"


def test_upsert_and_remove_accepts_uppercase_uuid():
    upper_reg = REG_UUID_STR.upper()
    upper_occ = OCC_UUID_STR.upper()

    doc = "<!-- Page 1 -->\n\nContent"
    mutated = upsert_visual_token(
        text=doc,
        region_id=upper_reg,
        occurrence_id=upper_occ,
        artifact_uri="crop.jpg",
        page_number=1,
    )

    # In canonical form, serialized UUIDs are lowercase
    assert f"polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}" in mutated

    # Remove with uppercase UUID
    removed = remove_visual_token(mutated, upper_reg)
    assert removed == doc


def test_remove_token_with_legacy_target():
    legacy_token = "![[crop_job42_p1_1.jpg]]"
    doc = f"Introduction\n{legacy_token}\nConclusion"

    # Remove by matching legacy_target filename
    removed = remove_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        legacy_target="crop_job42_p1_1.jpg",
    )
    assert removed == "Introduction\nConclusion"


def test_upsert_handles_artifact_uri_containing_parentheses():
    token = f'![Chart](crop(1).jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = f"Text before\n\n{token}\n\nText after"

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id="e06385b2-dc09-4ce4-897b-cf109c95eb48",
        artifact_uri="new_crop(2).png",
        page_number=1,
        alt_text=None,
    )

    # serialize_canonical_token encodes parentheses in URI per CommonMark spec
    expected_token = (
        '![Chart](new_crop%282%29.png '
        f'"polpo:region={REG_UUID_STR};occ=e06385b2-dc09-4ce4-897b-cf109c95eb48")'
    )
    assert mutated == f"Text before\n\n{expected_token}\n\nText after"
