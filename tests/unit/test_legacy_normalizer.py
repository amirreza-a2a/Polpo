from infrastructure.markdown.legacy_normalizer import (
    ColumnShift,
    PositionMapper,
    syntax_aware_normalize_legacy,
)


def test_column_shift_properties_and_position_mapper_direct():
    """Verify ColumnShift value object and direct PositionMapper usage."""
    shift = ColumnShift(
        norm_start_col=5,
        norm_end_col=15,
        orig_start_col=5,
        orig_end_col=12,
        delta=3,
    )
    assert shift.norm_start_col == 5
    assert shift.delta == 3

    mapper = PositionMapper({1: [shift]})
    assert mapper.map_to_original(1, 4) == (1, 4)
    assert mapper.map_to_original(1, 10) == (1, 5)
    assert mapper.map_to_original(1, 20) == (1, 17)


def test_unmodified_text_identity():
    """Text without legacy syntax undergoes 1:1 identity mapping."""
    original = "# Heading\n\nThis is a standard paragraph with **bold** text.\n"
    normalized, mapper = syntax_aware_normalize_legacy(original)

    assert normalized == original
    assert mapper.map_to_original(1, 1) == (1, 1)
    assert mapper.map_to_original(1, 10) == (1, 10)
    assert mapper.map_to_original(3, 5) == (3, 5)
    assert mapper.map_to_original(99, 1) == (99, 1)


def test_line_count_strict_invariance():
    """Line count is strictly identical before and after normalization."""
    original = (
        "Line 1: ![[img1.png]]\n"
        "Line 2: standard\n"
        "Line 3: ![[img2.png]] and ![[img3.png]]\n"
        "Line 4: end\n"
    )
    normalized, mapper = syntax_aware_normalize_legacy(original)

    orig_lines = original.split("\n")
    norm_lines = normalized.split("\n")
    assert len(norm_lines) == len(orig_lines)
    for i in range(1, len(orig_lines) + 1):
        # Line numbers are strictly invariant
        mapped_line, _ = mapper.map_to_original(i, 1)
        assert mapped_line == i


def test_single_replacement_all_5_position_contracts():
    """Verify position mapping across before, inside, boundary, after, and identity."""
    # Line: "Prefix ![[crop file.png]] Suffix"
    # Orig:
    #   'Prefix ' -> cols 1..7 (len 7)
    #   '![[crop file.png]]' -> cols 8..25 (len 18)
    #   ' Suffix' -> cols 26..32 (len 7)
    # Norm:
    #   'Prefix ' -> cols 1..7
    #   '![](<crop file.png>)' -> cols 8..27 (len 20, delta = +2)
    #   ' Suffix' -> cols 28..34
    text = "Prefix ![[crop file.png]] Suffix"
    normalized, mapper = syntax_aware_normalize_legacy(text)

    assert normalized == "Prefix ![](<crop file.png>) Suffix"

    # 1. Before replacement (cols 1..7): identity
    assert mapper.map_to_original(1, 1) == (1, 1)
    assert mapper.map_to_original(1, 7) == (1, 7)

    # 2. Inside replacement (cols 8..27): all map to orig_start_col (8)
    assert mapper.map_to_original(1, 8) == (1, 8)    # start boundary
    assert mapper.map_to_original(1, 15) == (1, 8)   # middle
    assert mapper.map_to_original(1, 27) == (1, 8)   # end boundary

    # 3. After replacement (cols 28..34): col - delta (delta = +2)
    # Col 28 in norm is ' ' right after image -> maps to col 26 in orig (' ')
    assert mapper.map_to_original(1, 28) == (1, 26)
    assert mapper.map_to_original(1, 34) == (1, 32)


def test_multiple_replacements_on_single_line():
    """Cumulative delta accumulation across multiple replacements on one line."""
    # Orig: "A ![[a 1.png]] B ![[b 2.png]] C"
    #   'A ' -> 1..2 (len 2)
    #   '![[a 1.png]]' -> 3..14 (len 12)
    #   ' B ' -> 15..17 (len 3)
    #   '![[b 2.png]]' -> 18..29 (len 12)
    #   ' C' -> 30..31 (len 2)
    # Norm: "A ![](<a 1.png>) B ![](<b 2.png>) C"
    #   'A ' -> 1..2
    #   '![](<a 1.png>)' -> 3..16 (len 14, delta1 = +2)
    #   ' B ' -> 17..19 (shifted by +2)
    #   '![](<b 2.png>)' -> 20..33 (len 14, delta2 = +2, accumulated = +4)
    #   ' C' -> 34..35 (shifted by +4)
    text = "A ![[a 1.png]] B ![[b 2.png]] C"
    normalized, mapper = syntax_aware_normalize_legacy(text)

    assert normalized == "A ![](<a 1.png>) B ![](<b 2.png>) C"

    # Before first replacement
    assert mapper.map_to_original(1, 1) == (1, 1)
    assert mapper.map_to_original(1, 2) == (1, 2)

    # Inside first replacement (cols 3..16)
    assert mapper.map_to_original(1, 3) == (1, 3)
    assert mapper.map_to_original(1, 10) == (1, 3)
    assert mapper.map_to_original(1, 16) == (1, 3)

    # Between first and second (' B ' at norm cols 17..19 -> orig cols 15..17)
    assert mapper.map_to_original(1, 17) == (1, 15)
    assert mapper.map_to_original(1, 18) == (1, 16)  # 'B'
    assert mapper.map_to_original(1, 19) == (1, 17)

    # Inside second replacement (cols 20..33)
    assert mapper.map_to_original(1, 20) == (1, 18)
    assert mapper.map_to_original(1, 26) == (1, 18)
    assert mapper.map_to_original(1, 33) == (1, 18)

    # After second replacement (' C' at norm cols 34..35 -> orig cols 30..31)
    assert mapper.map_to_original(1, 34) == (1, 30)
    assert mapper.map_to_original(1, 35) == (1, 31)


def test_urls_with_spaces_wrapped_in_angle_brackets():
    """URLs with spaces must be wrapped in <...> per CommonMark specification."""
    text1 = "Image: ![[my picture 2026.png]]"
    norm1, _ = syntax_aware_normalize_legacy(text1)
    assert norm1 == "Image: ![](<my picture 2026.png>)"

    text2 = "Image: ![[my picture 2026.png|Caption With Spaces]]"
    norm2, _ = syntax_aware_normalize_legacy(text2)
    assert norm2 == "Image: ![Caption With Spaces](<my picture 2026.png>)"


def test_fenced_code_blocks_preserved_untouched():
    """Backtick and tilde fenced code blocks protect legacy tokens from normalization."""
    text = (
        "```python\n"
        "# This should not be normalized:\n"
        "![[code_sample.png]]\n"
        "```\n"
        "Outside: ![[real_image.png]]\n"
        "~~~\n"
        "Tilde fence: ![[tilde_sample.png]]\n"
        "~~~\n"
    )
    normalized, mapper = syntax_aware_normalize_legacy(text)

    assert "![[code_sample.png]]" in normalized
    assert "![[tilde_sample.png]]" in normalized
    assert "Outside: ![](real_image.png)" in normalized

    # Line 3 (inside backtick fence) is unmodified -> 1:1 mapping
    assert mapper.map_to_original(3, 5) == (3, 5)
    # Line 7 (inside tilde fence) is unmodified -> 1:1 mapping
    assert mapper.map_to_original(7, 5) == (7, 5)


def test_inline_code_spans_preserved_untouched():
    """Inline code backticks protect legacy tokens from normalization."""
    text = "Run `![[inline_code.png]]` to view image, or see ![[real.png]]."
    normalized, _ = syntax_aware_normalize_legacy(text)

    assert "`![[inline_code.png]]`" in normalized
    assert "or see ![](real.png)." in normalized


def test_escaped_brackets_preserved():
    """Escaped backslash brackets protect legacy tokens from normalization."""
    # Escaped: \![[not_image.png]] -> untouched
    text1 = r"Here is \!\[\[not_image.png\]\] text"
    norm1, _ = syntax_aware_normalize_legacy(text1)
    assert r"\!\[\[not_image.png\]\]" in norm1

    # Escaped backslash: \\![[real.png]] -> backslash escaped, ![[real.png]] normalized
    text2 = "Here is \\\\![[real.png]] text"
    norm2, _ = syntax_aware_normalize_legacy(text2)
    assert "Here is \\\\![](real.png) text" in norm2


def test_pipe_metadata_alt_text():
    """Pipes in wiki-links are parsed as [alt](url)."""
    text1 = "![[figure.jpg|Fig 1: Circuit Diagram]]"
    norm1, _ = syntax_aware_normalize_legacy(text1)
    assert norm1 == "![Fig 1: Circuit Diagram](figure.jpg)"

    text2 = "![[figure.jpg|region_id=018f2d5a-1234-7a8b-9cde-567812345678]]"
    norm2, _ = syntax_aware_normalize_legacy(text2)
    assert norm2 == "![region_id=018f2d5a-1234-7a8b-9cde-567812345678](figure.jpg)"


def test_empty_or_whitespace_target_ignored():
    """Empty or whitespace-only target tags are ignored."""
    text = "Empty: ![[]] and Whitespace: ![[   ]]"
    norm, _ = syntax_aware_normalize_legacy(text)
    assert norm == text


def test_negative_delta_shrinking_replacement():
    """Verify position mapper correctly handles negative delta (replacement shorter than legacy tag)."""
    # Orig: "A ![[img.png|alt]] B"
    #   'A ' -> cols 1..2 (len 2)
    #   '![[img.png|alt]]' -> cols 3..18 (len 16)
    #   ' ' -> col 19 (len 1)
    #   'B' -> col 20 (len 1)
    # Norm: "A ![alt](img.png) B"
    #   'A ' -> cols 1..2 (len 2)
    #   '![alt](img.png)' -> cols 3..17 (len 15, delta = -1)
    #   ' ' -> col 18
    #   'B' -> col 19
    text = "A ![[img.png|alt]] B"
    norm, mapper = syntax_aware_normalize_legacy(text)
    assert norm == "A ![alt](img.png) B"

    # Before replacement
    assert mapper.map_to_original(1, 1) == (1, 1)
    assert mapper.map_to_original(1, 2) == (1, 2)

    # Inside replacement (norm cols 3..17)
    assert mapper.map_to_original(1, 3) == (1, 3)
    assert mapper.map_to_original(1, 10) == (1, 3)
    assert mapper.map_to_original(1, 17) == (1, 3)

    # After replacement:
    # norm col 18 (' ') -> orig col 18 - (-1) = 19 (' ')
    assert mapper.map_to_original(1, 18) == (1, 19)
    # norm col 19 ('B') -> orig col 19 - (-1) = 20 ('B')
    assert mapper.map_to_original(1, 19) == (1, 20)
