"""Unit tests for the pure canonical Markdown visual token mutator."""

import pytest

from core.exceptions.domain_exceptions import AmbiguousVisualTokenError
from core.markdown.visual_token_mutator import (
    find_canonical_tokens,
    remove_visual_token,
    upsert_visual_token,
)

REG_UUID_STR = "550e8400-e29b-41d4-a716-446655440000"
REG_HEX_STR = "550e8400e29b41d4a716446655440000"
OCC_UUID_STR = "6ba7b810-9dad-41d1-80b4-00c04fd430c8"
OCC_HEX_STR = "6ba7b8109dad41d180b400c04fd430c8"


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

    expected_token = f'![Fig](new.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert mutated == prefix + expected_token + suffix


def test_upsert_canonical_token_idempotent():
    token = f'![Chart](chart_v1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    doc = f"<!-- Page 1 -->\n{token}Some content."

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="chart_v1.jpg",
        page_number=1,
        alt_text="Chart",
    )
    assert mutated == doc


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
    assert "```python\n# Fake marker:\n<!-- Page 1 -->\nprint('hello')\n```" in mutated
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
    assert "~~~markdown\n<!-- Page 1 -->\n~~~" in mutated
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

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="real.jpg",
        page_number=1,
    )

    assert fake_token in mutated
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

    assert fake_token in mutated
    real_token = f'![](active.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert f"<!-- Page 1 -->\n{real_token}Real page body.\n" in mutated


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

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop_canonical.jpg",
        page_number=1,
    )

    assert unrelated_legacy in mutated
    new_token = f'![](crop_canonical.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    assert mutated == f"<!-- Page 1 -->\n{new_token}{unrelated_legacy}\nContent."


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
    assert "\n" not in mutated.replace("\r\n", "")
    assert mutated == (
        "# Title\r\n\r\n"
        "<!-- Page 1 -->\r\n"
        + expected_token +
        "Content line 1.\r\n"
    )


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

    doc1 = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop1.jpg",
        page_number=1,
        alt_text="Figure 1",
    )
    assert f'![Figure 1](crop1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")' in doc1

    doc2 = upsert_visual_token(
        text=doc1,
        region_id=reg2_uuid,
        occurrence_id=occ2_uuid,
        artifact_uri="crop2.jpg",
        page_number=1,
        alt_text="Figure 2",
    )

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

    assert f"polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}" in mutated

    removed = remove_visual_token(mutated, upper_reg)
    assert removed == doc


def test_remove_token_with_legacy_target():
    legacy_token = "![[crop_job42_p1_1.jpg]]"
    doc = f"Introduction\n{legacy_token}\nConclusion"

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

    expected_token = (
        '![Chart](new_crop%282%29.png '
        f'"polpo:region={REG_UUID_STR};occ=e06385b2-dc09-4ce4-897b-cf109c95eb48")'
    )
    assert mutated == f"Text before\n\n{expected_token}\n\nText after"


def test_upsert_leaves_preceding_ordinary_images_and_text_unchanged():
    prefix = (
        "# Document Header\n\n"
        "![Regular Image](pic.png)\n\n"
        "Some user text that must not be consumed or altered.\n\n"
    )
    token = f'![Crop](crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    suffix = "\n\nEnding notes."
    doc = prefix + token + suffix

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="new_crop.jpg",
        page_number=1,
        alt_text="Updated Crop",
    )

    expected_token = f'![Updated Crop](new_crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    assert mutated == prefix + expected_token + suffix
    assert mutated.startswith(prefix)
    assert mutated.endswith(suffix)


def test_remove_leaves_preceding_ordinary_images_and_text_unchanged():
    prefix = (
        "![Regular Image](pic.png)\n\n"
        "Some user text that must not be consumed or altered.\n\n"
    )
    token = f'![Crop](crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    doc = prefix + token

    mutated = remove_visual_token(doc, REG_UUID_STR)
    assert mutated == prefix


def test_upsert_with_multiple_ordinary_images_before_and_after_polpo_token():
    doc = (
        "![First](first.png)\n\n"
        "![Second](second.png \"With Title\")\n\n"
        f"![Target](crop.jpg \"polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}\")\n\n"
        "![Third](third.png)\n"
    )

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id="e06385b2-dc09-4ce4-897b-cf109c95eb48",
        artifact_uri="new_crop.jpg",
        page_number=1,
        alt_text="Target Updated",
    )

    expected = (
        "![First](first.png)\n\n"
        "![Second](second.png \"With Title\")\n\n"
        f"![Target Updated](new_crop.jpg \"polpo:region={REG_UUID_STR};occ=e06385b2-dc09-4ce4-897b-cf109c95eb48\")\n\n"
        "![Third](third.png)\n"
    )
    assert mutated == expected


def test_upsert_multiple_polpo_tokens_interleaved_with_ordinary_images():
    reg2 = "11111111-2222-4333-8444-555555555555"
    occ2 = "66666666-7777-4888-8999-000000000000"

    doc = (
        "![Photo](pic.jpg)\n\n"
        f"![Crop1](crop1.jpg \"polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}\")\n\n"
        "Middle paragraph text.\n\n"
        f"![Crop2](crop2.jpg \"polpo:region={reg2};occ={occ2}\")\n\n"
        "![Footer](footer.jpg)\n"
    )

    doc1 = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop1_v2.jpg",
        page_number=1,
        alt_text="Crop1 Updated",
    )

    assert f'![Crop1 Updated](crop1_v2.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")' in doc1
    assert f'![Crop2](crop2.jpg "polpo:region={reg2};occ={occ2}")' in doc1
    assert "![Photo](pic.jpg)" in doc1
    assert "Middle paragraph text." in doc1
    assert "![Footer](footer.jpg)" in doc1

    doc2 = upsert_visual_token(
        text=doc1,
        region_id=reg2,
        occurrence_id=occ2,
        artifact_uri="crop2_v2.jpg",
        page_number=1,
        alt_text="Crop2 Updated",
    )

    assert f'![Crop1 Updated](crop1_v2.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")' in doc2
    assert f'![Crop2 Updated](crop2_v2.jpg "polpo:region={reg2};occ={occ2}")' in doc2
    assert "![Photo](pic.jpg)" in doc2
    assert "Middle paragraph text." in doc2
    assert "![Footer](footer.jpg)" in doc2


def test_repeated_upsert_alt_text_stability():
    initial_alt = "Figure [A] & [B] \\ Path 'C:\\Data'"
    doc = "<!-- Page 1 -->\n\nParagraph\n"

    doc = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=OCC_UUID_STR,
        artifact_uri="crop.jpg",
        page_number=1,
        alt_text=initial_alt,
    )

    first_pass = doc
    # Repeated updates with alt_text=None must remain 100% byte-identical
    for _ in range(5):
        doc = upsert_visual_token(
            text=doc,
            region_id=REG_UUID_STR,
            occurrence_id=OCC_UUID_STR,
            artifact_uri="crop.jpg",
            page_number=1,
            alt_text=None,
        )
        assert doc == first_pass


def test_upsert_masks_multiline_inline_code_span():
    doc = (
        "Here is text.\n"
        "`code line 1\n"
        "<!-- Page 1 -->\n"
        f'![Fake](fake.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
        "code line 2`\n\n"
        "<!-- Page 1 -->\n"
        "Real content line.\n"
    )

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id="e06385b2-dc09-4ce4-897b-cf109c95eb48",
        artifact_uri="real_crop.jpg",
        page_number=1,
        alt_text="Real Crop",
    )

    code_block = (
        "`code line 1\n"
        "<!-- Page 1 -->\n"
        f'![Fake](fake.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
        "code line 2`"
    )
    assert code_block in mutated
    real_token = f'![Real Crop](real_crop.jpg "polpo:region={REG_UUID_STR};occ=e06385b2-dc09-4ce4-897b-cf109c95eb48")\n'
    assert f"<!-- Page 1 -->\n{real_token}Real content line.\n" in mutated


def test_upsert_handles_angle_bracket_destination_uri():
    token = f'![Bracket URI](<crop(complex).jpg> "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'
    doc = f"Header\n\n{token}\n\nFooter"

    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id="e06385b2-dc09-4ce4-897b-cf109c95eb48",
        artifact_uri="updated.jpg",
        page_number=1,
        alt_text=None,
    )

    expected_token = f'![Bracket URI](updated.jpg "polpo:region={REG_UUID_STR};occ=e06385b2-dc09-4ce4-897b-cf109c95eb48")'
    assert mutated == f"Header\n\n{expected_token}\n\nFooter"


def test_upsert_ignores_unmatched_backtick_inside_html_comment():
    comment = "<!-- comment containing an unmatched ` backtick -->"
    preceding_content = "normal Markdown content\n\n"
    page_marker = "<!-- Page 1 -->\n"
    canonical_token = f'![Figure](crops/crop1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    following_content = "Later paragraph with `inline code` backtick.\n"

    doc = f"{comment}\n\n{preceding_content}{page_marker}{canonical_token}\n{following_content}"

    new_occ = "e06385b2-dc09-4ce4-897b-cf109c95eb48"
    new_uri = "crops/crop1_updated.jpg"
    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=new_occ,
        artifact_uri=new_uri,
        page_number=1,
        alt_text="Updated Figure",
    )

    expected_token = f'![Updated Figure]({new_uri} "polpo:region={REG_UUID_STR};occ={new_occ}")\n'
    assert mutated == f"{comment}\n\n{preceding_content}{page_marker}{expected_token}\n{following_content}"

    doc_no_token = f"{comment}\n\n{preceding_content}{page_marker}\n{following_content}"
    inserted = upsert_visual_token(
        text=doc_no_token,
        region_id=REG_UUID_STR,
        occurrence_id=new_occ,
        artifact_uri=new_uri,
        page_number=1,
        alt_text="New Figure",
    )
    expected_inserted = f'![New Figure]({new_uri} "polpo:region={REG_UUID_STR};occ={new_occ}")\n'
    assert inserted == f"{comment}\n\n{preceding_content}{page_marker}{expected_inserted}\n{following_content}"

    removed = remove_visual_token(doc, REG_UUID_STR)
    assert removed == f"{comment}\n\n{preceding_content}{page_marker}\n{following_content}"


def test_upsert_ignores_unmatched_backtick_inside_fenced_code():
    fence = "```\nCode containing an unmatched ` backtick\n```"
    preceding_content = "normal Markdown content outside fence\n\n"
    page_marker = "<!-- Page 1 -->\n"
    canonical_token = f'![Figure](crops/crop1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    following_content = "Later paragraph with `inline code` backtick.\n"

    doc = f"{fence}\n\n{preceding_content}{page_marker}{canonical_token}\n{following_content}"

    new_occ = "e06385b2-dc09-4ce4-897b-cf109c95eb48"
    new_uri = "crops/crop1_updated.jpg"
    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=new_occ,
        artifact_uri=new_uri,
        page_number=1,
        alt_text="Updated Figure",
    )

    expected_token = f'![Updated Figure]({new_uri} "polpo:region={REG_UUID_STR};occ={new_occ}")\n'
    assert mutated == f"{fence}\n\n{preceding_content}{page_marker}{expected_token}\n{following_content}"

    removed = remove_visual_token(doc, REG_UUID_STR)
    assert removed == f"{fence}\n\n{preceding_content}{page_marker}\n{following_content}"


def test_upsert_ignores_html_comment_opener_inside_fenced_code():
    fence = "```\n<!-- unmatched comment start inside code fence\n```"
    preceding_content = "normal Markdown content outside fence\n\n"
    page_marker = "<!-- Page 1 -->\n"
    canonical_token = f'![Figure](crops/crop1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    following_content = "Later paragraph with stray comment closer --> here.\n"

    doc = f"{fence}\n\n{preceding_content}{page_marker}{canonical_token}\n{following_content}"

    new_occ = "e06385b2-dc09-4ce4-897b-cf109c95eb48"
    new_uri = "crops/crop1_updated.jpg"
    mutated = upsert_visual_token(
        text=doc,
        region_id=REG_UUID_STR,
        occurrence_id=new_occ,
        artifact_uri=new_uri,
        page_number=1,
        alt_text="Updated Figure",
    )

    expected_token = f'![Updated Figure]({new_uri} "polpo:region={REG_UUID_STR};occ={new_occ}")\n'
    assert mutated == f"{fence}\n\n{preceding_content}{page_marker}{expected_token}\n{following_content}"

    removed = remove_visual_token(doc, REG_UUID_STR)
    assert removed == f"{fence}\n\n{preceding_content}{page_marker}\n{following_content}"


def test_find_canonical_tokens_matching_single_and_multiple():
    doc = (
        f'<!-- Page 1 -->\n'
        f'![Figure 1](crops/crop1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n\n'
        f'Paragraph.\n\n'
        f'![Figure 2](<crops/crop2.jpg> "polpo:region={REG_UUID_STR};occ=e06385b2-dc09-4ce4-897b-cf109c95eb48")\n'
    )
    tokens = find_canonical_tokens(doc, REG_UUID_STR)
    assert len(tokens) == 2
    assert tokens[0].uri == "crops/crop1.jpg"
    assert tokens[0].alt_text == "Figure 1"
    assert str(tokens[0].region_id) == REG_UUID_STR
    assert str(tokens[0].occurrence_id) == OCC_UUID_STR

    assert tokens[1].uri == "crops/crop2.jpg"
    assert tokens[1].alt_text == "Figure 2"
    assert str(tokens[1].occurrence_id) == "e06385b2-dc09-4ce4-897b-cf109c95eb48"


def test_find_canonical_tokens_normalization():
    import uuid
    doc = f'![Alt](crops/crop.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")'

    # Test with 36-char hyphenated string
    t1 = find_canonical_tokens(doc, REG_UUID_STR)
    assert len(t1) == 1

    # Test with 32-char hex string
    t2 = find_canonical_tokens(doc, REG_HEX_STR)
    assert len(t2) == 1

    # Test with UUID object
    t3 = find_canonical_tokens(doc, uuid.UUID(REG_UUID_STR))
    assert len(t3) == 1


def test_find_canonical_tokens_ignores_opaque_contexts():
    doc = (
        "```markdown\n"
        f'![In Fence](crop1.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
        "```\n\n"
        f'`![In Code](crop2.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")`\n\n'
        f'<!-- ![In Comment](crop3.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}") -->\n\n'
        f'![Outside](crop4.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    )
    tokens = find_canonical_tokens(doc, REG_UUID_STR)
    assert len(tokens) == 1
    assert tokens[0].uri == "crop4.jpg"


def test_find_canonical_tokens_ignores_other_regions_and_non_managed():
    other_uuid = "e06385b2-dc09-4ce4-897b-cf109c95eb48"
    doc = (
        f'![Other Region](crop1.jpg "polpo:region={other_uuid};occ={OCC_UUID_STR}")\n\n'
        f'![Standard Image](standard.jpg)\n\n'
        f'![Alt](crop2.jpg "polpo:region={REG_UUID_STR};occ={OCC_UUID_STR}")\n'
    )
    tokens = find_canonical_tokens(doc, REG_UUID_STR)
    assert len(tokens) == 1
    assert tokens[0].uri == "crop2.jpg"


def test_find_canonical_tokens_invalid_uuid():
    with pytest.raises(ValueError):
        find_canonical_tokens("text", "not-a-uuid")
    with pytest.raises(TypeError):
        find_canonical_tokens("text", 12345)  # type: ignore[arg-type]
