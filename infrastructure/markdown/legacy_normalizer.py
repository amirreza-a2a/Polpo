"""Syntax-aware legacy markdown normalizer and position mapper.

Translates legacy wiki-link image syntax (![[target]] and ![[target|alt]]) into
valid CommonMark image syntax (![alt](url) or ![alt](<url>)) while preserving:
1. Strict line invariance (original_line == normalized_line).
2. Code isolation (protects fenced code blocks, inline code, and escaped syntax).
3. Exact reverse source-coordinate mapping via line-scoped ColumnShift records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class ColumnShift:
    """Represents a column shift caused by syntax normalization on a single line.

    Attributes:
        norm_start_col: 1-indexed column where replacement starts in normalized text.
        norm_end_col: 1-indexed column where replacement ends in normalized text (inclusive).
        orig_start_col: 1-indexed column where replacement started in original text.
        orig_end_col: 1-indexed column where replacement ended in original text (inclusive).
        delta: Column offset difference, defined as len(normalized) - len(original).
    """

    norm_start_col: int
    norm_end_col: int
    orig_start_col: int
    orig_end_col: int
    delta: int


class PositionMapper:
    """Reverse coordinate mapper from normalized coordinates to original source coordinates."""

    def __init__(self, shifts_by_line: Dict[int, List[ColumnShift]] | None = None) -> None:
        self._shifts_by_line: Dict[int, List[ColumnShift]] = shifts_by_line or {}

    def map_to_original(self, norm_line: int, norm_col: int) -> Tuple[int, int]:
        """Map a normalized (line, col) coordinate back to the original source coordinate.

        Contract:
        1. Before replacement: If norm_col < shift.norm_start_col, orig_col = norm_col - accumulated_delta.
        2. Inside replacement: If shift.norm_start_col <= norm_col <= shift.norm_end_col, map to shift.orig_start_col.
        3. After replacement: If norm_col > shift.norm_end_col, accumulate shift.delta.
        4. Multiple replacements: Accumulate deltas in sequence across shifts on the line.
        5. Unmodified lines: 1:1 identity mapping.

        Args:
            norm_line: 1-indexed line number in normalized document.
            norm_col: 1-indexed column number in normalized document.

        Returns:
            Tuple of (orig_line, orig_col) in 1-indexed coordinates.
        """
        shifts = self._shifts_by_line.get(norm_line)
        if not shifts:
            return (norm_line, norm_col)

        accumulated_delta = 0
        for shift in shifts:
            # 1. Before replacement
            if norm_col < shift.norm_start_col:
                orig_col = norm_col - accumulated_delta
                return (norm_line, orig_col)

            # 2. Inside replacement (inclusive of boundaries)
            if shift.norm_start_col <= norm_col <= shift.norm_end_col:
                return (norm_line, shift.orig_start_col)

            # 3. After replacement
            if norm_col > shift.norm_end_col:
                accumulated_delta += shift.delta

        orig_col = norm_col - accumulated_delta
        return (norm_line, orig_col)


_FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})")
_LINE_TOKEN_RE = re.compile(
    r"(?P<code>(?P<fence>`+).*?(?P=fence))"
    r"|(?P<bs>\\+)?(?P<legacy>!\[\[(?P<target>[^\]\r\n]+)\]\])"
)


def _format_replacement(target: str) -> str:
    """Format CommonMark image syntax from legacy target string."""
    target_clean = target.replace(r"\|", "|")
    if "|" in target_clean:
        url_part, alt_part = target_clean.split("|", 1)
        url = url_part.strip()
        alt = alt_part.strip()
    else:
        url = target.strip()
        alt = ""

    # CommonMark specification requires angle brackets around destinations containing spaces
    if " " in url and not (url.startswith("<") and url.endswith(">")):
        formatted_url = f"<{url}>"
    else:
        formatted_url = url

    return f"![{alt}]({formatted_url})"


def syntax_aware_normalize_legacy(text: str) -> Tuple[str, PositionMapper]:
    """Normalize legacy ![[...]] syntax to CommonMark ![](...) while preserving positions.

    Skips:
    - Fenced code blocks (```...``` and ~~~...~~~).
    - Inline code backticks (`...`).
    - Escaped syntax (\\!\\[\\[).

    Guarantees:
    - Strict line count invariance (orig_line == norm_line).
    - Returns a PositionMapper configured with line-scoped column shifts.
    """
    lines = text.split("\n")
    normalized_lines: List[str] = []
    shifts_by_line: Dict[int, List[ColumnShift]] = {}

    in_fence_close_re: re.Pattern[str] | None = None

    for line_idx, raw_line in enumerate(lines, start=1):
        # Preserve Windows CRLF if present
        if raw_line.endswith("\r"):
            line = raw_line[:-1]
            ending = "\r"
        else:
            line = raw_line
            ending = ""

        # Handle fenced code block state
        if in_fence_close_re is not None:
            if in_fence_close_re.match(line):
                in_fence_close_re = None
            normalized_lines.append(raw_line)
            continue

        fence_open_match = _FENCE_OPEN_RE.match(line)
        if fence_open_match:
            fence_str = fence_open_match.group("fence")
            fence_char = fence_str[0]
            fence_len = len(fence_str)
            close_pattern = r"^[ \t]{0,3}" + re.escape(fence_char) + "{" + str(fence_len) + r",}[ \t]*$"
            in_fence_close_re = re.compile(close_pattern)
            normalized_lines.append(raw_line)
            continue

        # Process single line outside code fence
        line_shifts: List[ColumnShift] = []
        new_line_parts: List[str] = []
        last_orig_idx = 0
        norm_cursor = 0

        for match in _LINE_TOKEN_RE.finditer(line):
            if match.group("code"):
                # Inline backtick span: skip without modification
                continue

            if match.group("legacy"):
                bs = match.group("bs") or ""
                # An odd number of preceding backslashes means the token is escaped (\![[)
                if len(bs) % 2 == 1:
                    continue

                target = match.group("target")
                if not target.strip():
                    continue

                legacy_orig_start = match.start("legacy")
                legacy_orig_end = match.end("legacy")
                orig_match_str = match.group("legacy")

                replacement = _format_replacement(target)

                # Append preceding unmodified text
                prefix_text = line[last_orig_idx:legacy_orig_start]
                new_line_parts.append(prefix_text)
                norm_cursor += len(prefix_text)

                orig_start_col = legacy_orig_start + 1
                orig_end_col = legacy_orig_end
                norm_start_col = norm_cursor + 1
                norm_end_col = norm_cursor + len(replacement)
                delta = len(replacement) - len(orig_match_str)

                shift = ColumnShift(
                    norm_start_col=norm_start_col,
                    norm_end_col=norm_end_col,
                    orig_start_col=orig_start_col,
                    orig_end_col=orig_end_col,
                    delta=delta,
                )
                line_shifts.append(shift)

                new_line_parts.append(replacement)
                norm_cursor += len(replacement)
                last_orig_idx = legacy_orig_end

        if line_shifts:
            trailing_text = line[last_orig_idx:]
            new_line_parts.append(trailing_text)
            normalized_line = "".join(new_line_parts) + ending
            shifts_by_line[line_idx] = line_shifts
        else:
            normalized_line = raw_line

        normalized_lines.append(normalized_line)

    normalized_text = "\n".join(normalized_lines)
    return normalized_text, PositionMapper(shifts_by_line)
