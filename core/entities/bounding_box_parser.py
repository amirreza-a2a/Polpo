# ============================================================
#  core/entities/bounding_box_parser.py
#  AI Vision Bounding Box Parsing & Positional Substitution
# ============================================================

import re
from dataclasses import dataclass
from typing import List, Tuple
from core.entities.bounding_box import BoundingBox, InvalidBoundingBoxError


@dataclass(frozen=True)
class ParsedBoundingBoxMatch:
    """
    Represents a successfully parsed bounding box and its exact character span in the source text.
    """
    box: BoundingBox
    start: int
    end: int
    raw_match: str


class BoundingBoxParser:
    """
    Parses canonical normalized bounding box tags ([[ymin, xmin, ymax, xmax]]) from AI vision text
    and performs deterministic reverse-positional substitutions.
    """

    TAG_PATTERN = re.compile(
        r"\[\[\s*(?:([a-zA-Z_]+)\s*=\s*)?(-?\d+(?:\.\d+)?)\s*,\s*(?:([a-zA-Z_]+)\s*=\s*)?(-?\d+(?:\.\d+)?)\s*,\s*(?:([a-zA-Z_]+)\s*=\s*)?(-?\d+(?:\.\d+)?)\s*,\s*(?:([a-zA-Z_]+)\s*=\s*)?(-?\d+(?:\.\d+)?)\s*\]\]"
    )

    @classmethod
    def parse_matches(cls, markdown_text: str) -> List[ParsedBoundingBoxMatch]:
        """
        Extracts all valid, normalized BoundingBox instances with their character spans.

        Deterministic parsing policy:
          - Floats are rounded to the nearest integer.
          - Negative coordinates (< 0) or out-of-range coordinates (> 1000) are skipped.
          - Inverted coordinates (ymin > ymax or xmin > xmax) are skipped.
          - Zero-area boxes (ymin == ymax or xmin == xmax) are skipped.
          - Handles both positional [[ymin, xmin, ymax, xmax]] and keyed
            [[ymin=..., xmin=..., ymax=..., xmax=...]] formats (case-insensitive, arbitrary order).
        """
        if not markdown_text:
            return []

        results: List[ParsedBoundingBoxMatch] = []

        for match in cls.TAG_PATTERN.finditer(markdown_text):
            try:
                k1, v1, k2, v2, k3, v3, k4, v4 = match.groups()

                if all(k is not None for k in (k1, k2, k3, k4)):
                    key_dict = {
                        k1.lower(): v1,
                        k2.lower(): v2,
                        k3.lower(): v3,
                        k4.lower(): v4,
                    }
                    if {"ymin", "xmin", "ymax", "xmax"} <= set(key_dict.keys()):
                        raw_y1 = key_dict["ymin"]
                        raw_x1 = key_dict["xmin"]
                        raw_y2 = key_dict["ymax"]
                        raw_x2 = key_dict["xmax"]
                    else:
                        raw_y1, raw_x1, raw_y2, raw_x2 = v1, v2, v3, v4
                else:
                    raw_y1, raw_x1, raw_y2, raw_x2 = v1, v2, v3, v4

                ymin = int(round(float(raw_y1)))
                xmin = int(round(float(raw_x1)))
                ymax = int(round(float(raw_y2)))
                xmax = int(round(float(raw_x2)))

                # Strict range checks: 0..1000
                if not (0 <= ymin <= 1000 and 0 <= xmin <= 1000 and 0 <= ymax <= 1000 and 0 <= xmax <= 1000):
                    continue

                # Skip zero-area and inverted geometry
                if ymin >= ymax or xmin >= xmax:
                    continue

                box = BoundingBox(ymin=ymin, xmin=xmin, ymax=ymax, xmax=xmax)
                results.append(
                    ParsedBoundingBoxMatch(
                        box=box,
                        start=match.start(),
                        end=match.end(),
                        raw_match=match.group(0),
                    )
                )
            except (ValueError, InvalidBoundingBoxError):
                continue

        return results

    @staticmethod
    def substitute_positional(
        markdown_text: str,
        replacements: List[Tuple[int, int, str]],
    ) -> str:
        """
        Substitutes text at exact character offsets [start:end] in descending order.

        Args:
          markdown_text: Original source markdown text.
          replacements: List of (start_offset, end_offset, replacement_string) tuples.

        Guarantees:
          - Replaces strictly by character span rather than ambiguous string lookup.
          - Handles duplicate coordinate strings and adjacent tags without collision.
          - Completely preserves surrounding text.
        """
        if not replacements or not markdown_text:
            return markdown_text

        # Sort replacements by start index descending (reverse order)
        sorted_replacements = sorted(replacements, key=lambda r: r[0], reverse=True)

        result = markdown_text
        for start, end, repl_str in sorted_replacements:
            if 0 <= start <= end <= len(result):
                result = result[:start] + repl_str + result[end:]

        return result
