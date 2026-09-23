"""
Standalone Pure-Logic Caret Offset Translator.

Translates between Pandoc 1-indexed Unicode column coordinates and Qt QTextCursor
0-indexed UTF-16 code unit offsets, accounting for Pandoc tab-stop expansion and
supplementary-plane characters (surrogate pairs).

This module contains pure logic and does NOT depend on Qt GUI modules.
"""

from typing import Final

TAB_STOP_WIDTH: Final[int] = 4


def unicode_col_to_qt_utf16_offset(line_text: str, col_1indexed: int) -> int:
    """
    Translates a 1-indexed Pandoc column coordinate into a 0-indexed UTF-16 code unit offset.

    Pandoc column rules:
    - 1-indexed: the first column on a line is 1.
    - Each standard Unicode codepoint advances the column by 1.
    - A literal tab ('\\t') expands to the next 4-space tab stop (columns 1, 5, 9, 13, ...).

    Qt UTF-16 code unit rules:
    - 0-indexed: offset 0 is before the first character.
    - Characters in the Basic Multilingual Plane (BMP, <= U+FFFF), including '\\t',
      consume exactly 1 UTF-16 code unit.
    - Characters in Supplementary Planes (> U+FFFF, e.g. emojis) consume 2 UTF-16 code units.

    Coordinates falling inside a tab stop return the UTF-16 offset of the tab character.
    Coordinates beyond the line boundary are clamped to [0, total_utf16_units].
    """
    if col_1indexed <= 1:
        return 0

    current_col = 1
    utf16_offset = 0

    for ch in line_text:
        # Determine Pandoc column advance for this character
        if ch == "\t":
            # Tab expands to the next 4-space tab stop
            col_advance = TAB_STOP_WIDTH - ((current_col - 1) % TAB_STOP_WIDTH)
        else:
            col_advance = 1

        next_col = current_col + col_advance
        if next_col > col_1indexed:
            # Target column falls before or within this character's column span
            return utf16_offset

        # Advance to the next character
        current_col = next_col
        # Supplementary plane characters (> U+FFFF) require 2 UTF-16 code units (surrogate pair)
        utf16_offset += 2 if ord(ch) > 0xFFFF else 1

    return utf16_offset


def qt_utf16_offset_to_unicode_col(line_text: str, utf16_offset_0indexed: int) -> int:
    """
    Translates a 0-indexed Qt UTF-16 code unit offset into a 1-indexed Pandoc column coordinate.

    If the UTF-16 offset points to the low surrogate of a surrogate pair, it maps to
    the beginning column of that character.
    Offsets beyond the line boundary return the column immediately following the last character.
    """
    if utf16_offset_0indexed <= 0:
        return 1

    current_utf16 = 0
    current_col = 1

    for ch in line_text:
        ch_utf16_len = 2 if ord(ch) > 0xFFFF else 1

        if current_utf16 + ch_utf16_len > utf16_offset_0indexed:
            # Offset points inside or at this character
            return current_col

        current_utf16 += ch_utf16_len
        if ch == "\t":
            current_col += TAB_STOP_WIDTH - ((current_col - 1) % TAB_STOP_WIDTH)
        else:
            current_col += 1

    return current_col
