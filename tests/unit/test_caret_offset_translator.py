import ast
import inspect
import pytest

from interfaces.desktop.coordinators.caret_offset_translator import (
    unicode_col_to_qt_utf16_offset,
    qt_utf16_offset_to_unicode_col,
)


class TestCaretOffsetTranslatorPureLogic:
    """Verify that caret_offset_translator is pure logic and imports zero Qt GUI modules."""

    def test_zero_qt_imports(self) -> None:
        import interfaces.desktop.coordinators.caret_offset_translator as mod

        source = inspect.getsource(mod)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(("PySide", "PyQt")), f"Unexpected import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert not node.module.startswith(("PySide", "PyQt")), f"Unexpected from-import: {node.module}"


class TestUnicodeColToQtUtf16Offset:
    """Tests for unicode_col_to_qt_utf16_offset(line_text, col_1indexed)."""

    def test_empty_string(self) -> None:
        assert unicode_col_to_qt_utf16_offset("", 1) == 0
        assert unicode_col_to_qt_utf16_offset("", 5) == 0
        assert unicode_col_to_qt_utf16_offset("", 0) == 0

    def test_ascii_text(self) -> None:
        text = "Hello World"
        # 1-indexed columns:
        # H:1, e:2, l:3, l:4, o:5, ' ':6, W:7, o:8, r:9, l:10, d:11, End:12
        assert unicode_col_to_qt_utf16_offset(text, 1) == 0
        assert unicode_col_to_qt_utf16_offset(text, 2) == 1
        assert unicode_col_to_qt_utf16_offset(text, 6) == 5
        assert unicode_col_to_qt_utf16_offset(text, 7) == 6
        assert unicode_col_to_qt_utf16_offset(text, 11) == 10
        assert unicode_col_to_qt_utf16_offset(text, 12) == 11
        # Out-of-bounds column clamped to end of line
        assert unicode_col_to_qt_utf16_offset(text, 99) == 11
        # Non-positive column clamped to start of line
        assert unicode_col_to_qt_utf16_offset(text, 0) == 0
        assert unicode_col_to_qt_utf16_offset(text, -5) == 0

    def test_single_tab_at_start(self) -> None:
        # "\tTarget"
        # Pandoc columns:
        # '\t' is at col 1, next tab stop is col 5.
        # 'T' is at col 5
        # 'a' is at col 6
        # Qt UTF-16:
        # '\t' is 1 UTF-16 code unit (offset 0).
        # 'T' is at offset 1.
        text = "\tTarget"
        assert unicode_col_to_qt_utf16_offset(text, 1) == 0  # start of line (before \t)
        # Inside the 4-space tab stop (cols 2, 3, 4) should map to before the tab (offset 0)
        assert unicode_col_to_qt_utf16_offset(text, 2) == 0
        assert unicode_col_to_qt_utf16_offset(text, 3) == 0
        assert unicode_col_to_qt_utf16_offset(text, 4) == 0
        # Immediately after tab, at 'T'
        assert unicode_col_to_qt_utf16_offset(text, 5) == 1
        assert unicode_col_to_qt_utf16_offset(text, 6) == 2  # at 'a'
        assert unicode_col_to_qt_utf16_offset(text, 11) == 7  # end of line

    def test_multiple_consecutive_tabs(self) -> None:
        # "\t\tTarget"
        # Tab 1: start_col=1, next=5. utf16=0
        # Tab 2: start_col=5, next=9. utf16=1
        # 'T': start_col=9. utf16=2
        text = "\t\tTarget"
        assert unicode_col_to_qt_utf16_offset(text, 1) == 0
        assert unicode_col_to_qt_utf16_offset(text, 4) == 0
        assert unicode_col_to_qt_utf16_offset(text, 5) == 1
        assert unicode_col_to_qt_utf16_offset(text, 8) == 1
        assert unicode_col_to_qt_utf16_offset(text, 9) == 2
        assert unicode_col_to_qt_utf16_offset(text, 10) == 3

    def test_text_before_and_after_tabs(self) -> None:
        # "a\tTarget"
        # 'a': col 1. next col 2. utf16 offset 0
        # '\t': col 2. next tab stop is col 5. utf16 offset 1
        # 'T': col 5. utf16 offset 2
        text = "a\tTarget"
        assert unicode_col_to_qt_utf16_offset(text, 1) == 0
        assert unicode_col_to_qt_utf16_offset(text, 2) == 1
        assert unicode_col_to_qt_utf16_offset(text, 3) == 1
        assert unicode_col_to_qt_utf16_offset(text, 4) == 1
        assert unicode_col_to_qt_utf16_offset(text, 5) == 2
        assert unicode_col_to_qt_utf16_offset(text, 6) == 3

        # "abc\tTarget"
        # 'a': col 1 (utf16: 0)
        # 'b': col 2 (utf16: 1)
        # 'c': col 3 (utf16: 2)
        # '\t': col 4. next tab stop is col 5. (utf16: 3)
        # 'T': col 5 (utf16: 4)
        text2 = "abc\tTarget"
        assert unicode_col_to_qt_utf16_offset(text2, 4) == 3
        assert unicode_col_to_qt_utf16_offset(text2, 5) == 4

        # "abcd\tTarget"
        # 'a','b','c','d': cols 1,2,3,4
        # '\t' is at col 5. next tab stop is col 9. (utf16: 4)
        # 'T' is at col 9. (utf16: 5)
        text3 = "abcd\tTarget"
        assert unicode_col_to_qt_utf16_offset(text3, 5) == 4
        assert unicode_col_to_qt_utf16_offset(text3, 8) == 4
        assert unicode_col_to_qt_utf16_offset(text3, 9) == 5

    def test_persian_arabic_rtl_text(self) -> None:
        # Persian characters are in BMP (U+0600 - U+06FF), each consuming 1 UTF-16 code unit
        # "سلام دنیا"
        # س:1(0), ل:2(1), ا:3(2), م:4(3), ' ':5(4), د:6(5), ن:7(6), ی:8(7), ا:9(8)
        text = "سلام دنیا"
        assert unicode_col_to_qt_utf16_offset(text, 1) == 0
        assert unicode_col_to_qt_utf16_offset(text, 2) == 1
        assert unicode_col_to_qt_utf16_offset(text, 5) == 4
        assert unicode_col_to_qt_utf16_offset(text, 6) == 5
        assert unicode_col_to_qt_utf16_offset(text, 10) == 9

    def test_combining_characters(self) -> None:
        # 'e' + combining acute '\u0301'
        # Both are BMP codepoints
        text = "e\u0301cole"
        # e:1(0), \u0301:2(1), c:3(2), o:4(3), l:5(4), e:6(5)
        assert unicode_col_to_qt_utf16_offset(text, 1) == 0
        assert unicode_col_to_qt_utf16_offset(text, 2) == 1
        assert unicode_col_to_qt_utf16_offset(text, 3) == 2

    def test_supplementary_plane_emojis(self) -> None:
        # 🎉 is U+1F389 (> U+FFFF), consuming 2 UTF-16 code units (surrogate pair)
        text = "Hi 🎉 Bye"
        # H: col 1, utf16 0
        # i: col 2, utf16 1
        # ' ': col 3, utf16 2
        # 🎉: col 4, utf16 3 (consumes utf16 offsets 3 and 4)
        # ' ': col 5, utf16 5
        # B: col 6, utf16 6
        # y: col 7, utf16 7
        # e: col 8, utf16 8
        assert unicode_col_to_qt_utf16_offset(text, 1) == 0
        assert unicode_col_to_qt_utf16_offset(text, 2) == 1
        assert unicode_col_to_qt_utf16_offset(text, 3) == 2
        assert unicode_col_to_qt_utf16_offset(text, 4) == 3
        assert unicode_col_to_qt_utf16_offset(text, 5) == 5
        assert unicode_col_to_qt_utf16_offset(text, 6) == 6
        assert unicode_col_to_qt_utf16_offset(text, 9) == 9

    def test_zwj_surrogate_sequences(self) -> None:
        # Family emoji: 👨‍👩‍👧‍👦
        # 👨 (U+1F468: 2 units) + ZWJ (U+200D: 1 unit) + 👩 (U+1F469: 2 units) + ZWJ (1 unit) + 👧 (U+1F467: 2 units) + ZWJ (1 unit) + 👦 (U+1F466: 2 units)
        # Total UTF-16 code units = 11.
        family = "👨‍👩‍👧‍👦"
        text = f"Family: {family} end"
        # "Family: " has 8 chars, 8 code units (offsets 0..7)
        # family starts at col 9 (utf16 offset 8)
        # family consists of 7 codepoints (cols 9, 10, 11, 12, 13, 14, 15)
        # ' ' after family starts at col 16 (utf16 offset 8 + 11 = 19)
        assert unicode_col_to_qt_utf16_offset(text, 1) == 0
        assert unicode_col_to_qt_utf16_offset(text, 9) == 8
        assert unicode_col_to_qt_utf16_offset(text, 16) == 19
        assert unicode_col_to_qt_utf16_offset(text, 17) == 20  # 'e'

    def test_legacy_normalized_line_positions(self) -> None:
        # A line with standard markdown or legacy token
        line = "![alt](polpo://crop/123) and more"
        assert unicode_col_to_qt_utf16_offset(line, 1) == 0
        assert unicode_col_to_qt_utf16_offset(line, 2) == 1
        assert unicode_col_to_qt_utf16_offset(line, 25) == 24


class TestQtUtf16OffsetToUnicodeCol:
    """Tests for qt_utf16_offset_to_unicode_col(line_text, utf16_offset_0indexed)."""

    def test_empty_string(self) -> None:
        assert qt_utf16_offset_to_unicode_col("", 0) == 1
        assert qt_utf16_offset_to_unicode_col("", 5) == 1
        assert qt_utf16_offset_to_unicode_col("", -1) == 1

    def test_ascii_text(self) -> None:
        text = "Hello World"
        assert qt_utf16_offset_to_unicode_col(text, 0) == 1
        assert qt_utf16_offset_to_unicode_col(text, 1) == 2
        assert qt_utf16_offset_to_unicode_col(text, 5) == 6
        assert qt_utf16_offset_to_unicode_col(text, 10) == 11
        assert qt_utf16_offset_to_unicode_col(text, 11) == 12  # end
        assert qt_utf16_offset_to_unicode_col(text, 99) == 12  # clamped

    def test_tabs_reverse(self) -> None:
        text = "\tTarget"
        # utf16 offset 0 is '\t', column 1
        assert qt_utf16_offset_to_unicode_col(text, 0) == 1
        # utf16 offset 1 is 'T', column 5
        assert qt_utf16_offset_to_unicode_col(text, 1) == 5
        # utf16 offset 2 is 'a', column 6
        assert qt_utf16_offset_to_unicode_col(text, 2) == 6

        # Consecutive tabs
        text2 = "\t\tTarget"
        assert qt_utf16_offset_to_unicode_col(text2, 0) == 1
        assert qt_utf16_offset_to_unicode_col(text2, 1) == 5
        assert qt_utf16_offset_to_unicode_col(text2, 2) == 9

    def test_supplementary_plane_reverse(self) -> None:
        text = "Hi 🎉 Bye"
        # H: 0 -> col 1
        # i: 1 -> col 2
        # ' ': 2 -> col 3
        # 🎉: 3 -> col 4
        # 🎉 second surrogate: 4 -> col 4
        # ' ': 5 -> col 5
        # B: 6 -> col 6
        assert qt_utf16_offset_to_unicode_col(text, 0) == 1
        assert qt_utf16_offset_to_unicode_col(text, 2) == 3
        assert qt_utf16_offset_to_unicode_col(text, 3) == 4
        assert qt_utf16_offset_to_unicode_col(text, 4) == 4
        assert qt_utf16_offset_to_unicode_col(text, 5) == 5
        assert qt_utf16_offset_to_unicode_col(text, 6) == 6

    def test_persian_arabic_reverse(self) -> None:
        text = "سلام دنیا"
        assert qt_utf16_offset_to_unicode_col(text, 0) == 1
        assert qt_utf16_offset_to_unicode_col(text, 4) == 5
        assert qt_utf16_offset_to_unicode_col(text, 5) == 6


class TestRoundTripFidelity:
    """Verify round-trip consistency between forward and reverse transformations."""

    @pytest.mark.parametrize(
        "text",
        [
            "Hello World",
            "\tTarget after tab",
            "\t\tDouble tab start",
            "Prefix\tTabbed\tColumns",
            "سلام دنیا و فارسی",
            "Emoji 🎉 and rockets 🚀 and family 👨‍👩‍👧‍👦",
            "Mixed \t فارسی 🎉 text with \t tabs",
            "e\u0301cole accentue\u0301e",
        ],
    )
    def test_round_trip_for_valid_character_boundaries(self, text: str) -> None:
        # Iterating across all characters in text
        # Compute exact column and verify forward and backward mappings
        col = 1
        utf16_offset = 0
        for ch in text:
            # Forward test:
            calc_utf16 = unicode_col_to_qt_utf16_offset(text, col)
            assert calc_utf16 == utf16_offset, f"Forward mismatch for '{ch}' at col {col}"

            # Reverse test:
            calc_col = qt_utf16_offset_to_unicode_col(text, utf16_offset)
            assert calc_col == col, f"Reverse mismatch for '{ch}' at utf16 {utf16_offset}"

            # Advance
            if ch == "\t":
                col += 4 - ((col - 1) % 4)
            else:
                col += 1
            utf16_offset += 2 if ord(ch) > 0xFFFF else 1
