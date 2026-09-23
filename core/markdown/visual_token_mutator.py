# ============================================================
#  core/markdown/visual_token_mutator.py
#  Pure Canonical Markdown Visual Token Mutator
# ============================================================

"""Pure domain component for format-preserving visual token mutation in CommonMark.

Performs format-preserving in-place update, insertion, and removal of canonical visual tokens:
    ![alt](artifact_uri "polpo:region=<region_uuid>;occ=<occurrence_uuid>")
and strict legacy token migration (![[...]]) without AST re-serialization, line reflow,
or global whitespace alteration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union
from uuid import UUID

from core.domain.visual_token import (
    TokenDiagnosticType,
    VisualOccurrenceToken,
    classify_token_metadata,
    serialize_canonical_token,
    validate_token_uuid,
)
from core.exceptions.domain_exceptions import AmbiguousVisualTokenError

__all__ = [
    "AmbiguousVisualTokenError",
    "upsert_visual_token",
    "remove_visual_token",
]

_FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_MD_IMAGE_RE = re.compile(
    r"!\[(?P<alt>.*?(?<!\\)(?:\\\\)*)\]\((?P<uri>.+?)\s+\"(?P<title>polpo:[^\"]*)\"\)",
    re.DOTALL,
)
_LEGACY_TOKEN_RE = re.compile(r"(?P<bs>\\+)?!\[\[(?P<target>[^\]\r\n]+)\]\]")
_PAGE_MARKER_EXACT_RE = re.compile(r"^<!--\s*Page\s+(?P<num>\d+)\s*-->\r?$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"(?P<fence>`+)(?P<code>.*?)(?<!`)(?P=fence)(?!`)")


@dataclass(frozen=True)
class _TokenMatch:
    start: int
    end: int
    existing_alt: str = ""


def _normalize_uuid(val: Union[str, UUID], name: str = "UUID") -> UUID:
    """Normalizes and validates a UUIDv4 from hyphenated string, hex string, or UUID object."""
    if isinstance(val, UUID):
        parsed = val
    elif isinstance(val, str):
        cleaned = val.strip()
        try:
            parsed = UUID(cleaned)
        except (ValueError, AttributeError, TypeError) as e:
            raise ValueError(f"Invalid {name} format: {val}") from e
    else:
        raise TypeError(f"{name} must be a str or UUID, got {type(val).__name__}")

    if parsed.version != 4:
        raise ValueError(f"Invalid UUIDv4: version is {parsed.version}, expected 4 ({val})")

    # Strictly validate against canonical lowercase hyphenated UUIDv4 representation
    validate_token_uuid(str(parsed))
    return parsed


def _find_opaque_spans(text: str) -> List[Tuple[int, int]]:
    """Identifies character spans that must be treated as opaque literal contexts:

    - Fenced code blocks (``` and ~~~)
    - Indented code blocks (4 spaces / 1 tab preceded by blank line or doc start)
    - Non-Polpo HTML comments (<!-- ... --> not matching <!-- Page N -->)
    - Inline code spans (`...`)
    """
    opaque_spans: List[Tuple[int, int]] = []

    # 1. Identify line boundaries and line-oriented blocks (fences and indented code)
    lines_with_offsets: List[Tuple[str, int, int]] = []
    idx = 0
    raw_lines = text.split("\n")
    for rline in raw_lines:
        start = idx
        end = idx + len(rline)
        lines_with_offsets.append((rline, start, end))
        idx = end + 1  # Account for \n separator

    # Scan for fenced code blocks
    in_fence_close_re: Optional[re.Pattern[str]] = None
    fence_start = 0

    for rline, lstart, lend in lines_with_offsets:
        line_clean = rline[:-1] if rline.endswith("\r") else rline

        if in_fence_close_re is not None:
            if in_fence_close_re.match(line_clean):
                opaque_spans.append((fence_start, lend))
                in_fence_close_re = None
            continue

        fence_open = _FENCE_OPEN_RE.match(line_clean)
        if fence_open:
            fstr = fence_open.group(1)
            fchar = fstr[0]
            flen = len(fstr)
            close_pat = r"^[ \t]{0,3}" + re.escape(fchar) + "{" + str(flen) + r",}[ \t]*$"
            in_fence_close_re = re.compile(close_pat)
            fence_start = lstart
            continue

    if in_fence_close_re is not None:
        opaque_spans.append((fence_start, len(text)))

    # Scan for indented code blocks (outside fenced code)
    in_indented = False
    indented_start = 0
    prev_blank = True

    for rline, lstart, lend in lines_with_offsets:
        line_clean = rline[:-1] if rline.endswith("\r") else rline
        is_blank = (len(line_clean.strip()) == 0)

        # Skip lines already enclosed in fenced code
        if any(s <= lstart and lend <= e for s, e in opaque_spans):
            prev_blank = is_blank
            in_indented = False
            continue

        if not in_indented:
            if prev_blank and not is_blank and (line_clean.startswith("    ") or line_clean.startswith("\t")):
                in_indented = True
                indented_start = lstart
        else:
            if is_blank:
                # Blank lines inside indented blocks are permitted
                pass
            elif line_clean.startswith("    ") or line_clean.startswith("\t"):
                pass
            else:
                # End of indented block
                opaque_spans.append((indented_start, lstart - 1))
                in_indented = False

        prev_blank = is_blank

    if in_indented:
        opaque_spans.append((indented_start, len(text)))

    # 2. Scan for Non-Polpo HTML comments (outside existing opaque spans)
    for m in _HTML_COMMENT_RE.finditer(text):
        c_start, c_end = m.start(), m.end()
        # Skip if within existing opaque span
        if any(s <= c_start and c_end <= e for s, e in opaque_spans):
            continue

        comment_content = m.group(0).strip()
        # If it is a structural page marker, it is NOT opaque
        if _PAGE_MARKER_EXACT_RE.match(comment_content):
            continue

        opaque_spans.append((c_start, c_end))

    # 3. Scan for Inline Code Spans (outside existing opaque spans)
    for m in _INLINE_CODE_RE.finditer(text):
        b_start, b_end = m.start(), m.end()
        if any(s <= b_start and b_end <= e for s, e in opaque_spans):
            continue
        opaque_spans.append((b_start, b_end))

    # Sort and merge intervals
    opaque_spans.sort(key=lambda span: span[0])
    merged: List[Tuple[int, int]] = []
    for span in opaque_spans:
        if not merged:
            merged.append(span)
        else:
            prev_s, prev_e = merged[-1]
            if span[0] <= prev_e:
                merged[-1] = (prev_s, max(prev_e, span[1]))
            else:
                merged.append(span)

    return merged


def _is_opaque(start: int, end: int, opaque_spans: List[Tuple[int, int]]) -> bool:
    """Checks whether the character interval [start, end) intersects with any opaque span."""
    for o_start, o_end in opaque_spans:
        if max(start, o_start) < min(end, o_end):
            return True
        if o_start >= end:
            break
    return False


def _find_matching_tokens(
    text: str,
    target_region_uuid: UUID,
    legacy_target: Optional[str],
    opaque_spans: List[Tuple[int, int]],
) -> List[_TokenMatch]:
    """Finds all canonical or eligible legacy visual tokens matching target_region_uuid outside opaque spans."""
    matches: List[_TokenMatch] = []
    target_hex = target_region_uuid.hex.lower()
    target_hyphen = str(target_region_uuid).lower()

    # 1. Search for canonical CommonMark tokens
    for match in _MD_IMAGE_RE.finditer(text):
        m_start, m_end = match.start(), match.end()
        if _is_opaque(m_start, m_end, opaque_spans):
            continue

        title = match.group("title")
        diag, r_id, _ = classify_token_metadata(title)
        if diag == TokenDiagnosticType.CANONICAL and r_id == target_region_uuid:
            alt = match.group("alt") or ""
            matches.append(_TokenMatch(m_start, m_end, existing_alt=alt))

    # 2. Search for eligible legacy Obsidian-style ![[...]] tokens
    for match in _LEGACY_TOKEN_RE.finditer(text):
        bs = match.group("bs") or ""
        if len(bs) % 2 == 1:
            # Escaped \![[
            continue

        m_start = match.start() + len(bs)
        m_end = match.end()
        if _is_opaque(m_start, m_end, opaque_spans):
            continue

        target = match.group("target").strip()
        target_clean = target.replace(r"\|", "|")
        parts = target_clean.split("|", 1)
        url_part = parts[0].strip()
        alt = parts[1].strip() if len(parts) > 1 else ""
        target_lower = target.lower()

        # Evidence criteria: contains UUID OR exactly matches caller-supplied legacy_target
        has_uuid = (target_hex in target_lower or target_hyphen in target_lower)
        matches_target = (legacy_target is not None and (url_part == legacy_target or target == legacy_target))

        if has_uuid or matches_target:
            matches.append(_TokenMatch(m_start, m_end, existing_alt=alt))

    matches.sort(key=lambda m: m.start)
    return matches


def upsert_visual_token(
    text: str,
    region_id: str,
    occurrence_id: str,
    artifact_uri: str,
    page_number: int,
    alt_text: Optional[str] = None,
    legacy_target: Optional[str] = None,
) -> str:
    """Inserts or updates a canonical visual token in Markdown text.

    Args:
        text: Original raw Markdown document text.
        region_id: Region UUID string (36-char hyphenated or 32-char hex).
        occurrence_id: Occurrence UUID string (36-char hyphenated or 32-char hex).
        artifact_uri: Relative or destination URI for the crop artifact.
        page_number: 1-indexed target page number for new token insertion.
        alt_text: Optional alt text for image. If None during update of an existing
            token, preserves the existing alt text.
        legacy_target: Optional resolved filename for legacy token migration.

    Returns:
        Mutated Markdown text strictly format-preserved outside the mutation span.

    Raises:
        ValueError: If page_number <= 0 or UUIDs are invalid.
        AmbiguousVisualTokenError: If multiple tokens match the target region_id.
    """
    if page_number <= 0:
        raise ValueError(f"page_number must be >= 1, got {page_number}")

    target_region_uuid = _normalize_uuid(region_id, "region_id")
    target_occ_uuid = _normalize_uuid(occurrence_id, "occurrence_id")

    opaque_spans = _find_opaque_spans(text)
    matches = _find_matching_tokens(text, target_region_uuid, legacy_target, opaque_spans)

    if len(matches) > 1:
        raise AmbiguousVisualTokenError(
            f"Multiple ({len(matches)}) visual occurrence tokens found matching region {target_region_uuid}."
        )

    # Case 1: In-place update of existing canonical or eligible legacy token
    if len(matches) == 1:
        match = matches[0]
        effective_alt = match.existing_alt if alt_text is None else alt_text
        canonical_token_obj = VisualOccurrenceToken(
            region_id=target_region_uuid,
            occurrence_id=target_occ_uuid,
            uri=artifact_uri,
            alt_text=effective_alt,
        )
        canonical_token_str = serialize_canonical_token(canonical_token_obj)
        return text[:match.start] + canonical_token_str + text[match.end:]

    # Case 2: Insertion of new token
    effective_alt = "" if alt_text is None else alt_text
    canonical_token_obj = VisualOccurrenceToken(
        region_id=target_region_uuid,
        occurrence_id=target_occ_uuid,
        uri=artifact_uri,
        alt_text=effective_alt,
    )
    canonical_token_str = serialize_canonical_token(canonical_token_obj)

    # Locate structural <!-- Page {page_number} --> outside opaque contexts
    page_marker_pat = re.compile(
        r"^[ \t]*<!--\s*Page\s+" + str(page_number) + r"\s*-->[ \t]*\r?$",
        re.MULTILINE,
    )

    marker_match: Optional[re.Match[str]] = None
    for m in page_marker_pat.finditer(text):
        if not _is_opaque(m.start(), m.end(), opaque_spans):
            marker_match = m
            break

    if marker_match is not None:
        m_end = marker_match.end()
        # Marker pattern matched `\r?$` so any `\r` immediately before line break is part of marker_match
        if m_end < len(text) and text[m_end] == "\n":
            newline = "\r\n" if (m_end > 0 and text[m_end - 1] == "\r") else "\n"
            insert_pos = m_end + 1
            return text[:insert_pos] + canonical_token_str + newline + text[insert_pos:]

        # Marker is at end of text without trailing newline
        newline = "\r\n" if "\r\n" in text else "\n"
        insert_pos = m_end
        if insert_pos > 0 and text[insert_pos - 1] not in ("\r", "\n"):
            return text[:insert_pos] + newline + canonical_token_str + newline + text[insert_pos:]

        return text[:insert_pos] + canonical_token_str + newline + text[insert_pos:]

    # Fallback: Page marker missing, append to document end
    newline = "\r\n" if "\r\n" in text else "\n"
    if not text:
        return canonical_token_str + newline
    if text.endswith("\r\n\r\n") or text.endswith("\n\n"):
        return text + canonical_token_str + newline
    if text.endswith("\r\n") or text.endswith("\n"):
        return text + newline + canonical_token_str + newline

    return text + newline + newline + canonical_token_str + newline


def remove_visual_token(
    text: str,
    region_id: str,
    legacy_target: Optional[str] = None,
) -> str:
    """Removes the visual token matching region_id from Markdown text.

    Args:
        text: Original raw Markdown document text.
        region_id: Region UUID string (36-char hyphenated or 32-char hex).
        legacy_target: Optional resolved filename for legacy token migration.

    Returns:
        Mutated Markdown text without the specified token. If 0 matches, returns text unchanged.

    Raises:
        ValueError: If region_id is an invalid UUID format.
        AmbiguousVisualTokenError: If multiple tokens match the target region_id.
    """
    target_region_uuid = _normalize_uuid(region_id, "region_id")
    opaque_spans = _find_opaque_spans(text)
    matches = _find_matching_tokens(text, target_region_uuid, legacy_target=legacy_target, opaque_spans=opaque_spans)

    if len(matches) > 1:
        raise AmbiguousVisualTokenError(
            f"Multiple ({len(matches)}) visual occurrence tokens found matching region {target_region_uuid}."
        )

    if len(matches) == 0:
        return text

    match = matches[0]

    # Find the line containing the token to determine if it is standalone
    line_start = text.rfind("\n", 0, match.start)
    line_start = 0 if line_start == -1 else line_start + 1

    line_end = text.find("\n", match.end)
    line_end = len(text) if line_end == -1 else line_end

    prefix_on_line = text[line_start:match.start]
    suffix_on_line = text[match.end:line_end]
    if suffix_on_line.endswith("\r"):
        suffix_on_line = suffix_on_line[:-1]

    # If the token is on a standalone line with only whitespace, remove the entire line
    if prefix_on_line.strip() == "" and suffix_on_line.strip() == "":
        if line_end < len(text):
            # Consume trailing newline
            end_pos = line_end + 1
            return text[:line_start] + text[end_pos:]
        if line_start > 0:
            # Standalone line at end of document; consume preceding newline
            prev_nl = line_start - 2 if text[line_start - 2:line_start] == "\r\n" else line_start - 1
            return text[:prev_nl]
        return ""

    # Inline token: remove only the exact token span
    return text[:match.start] + text[match.end:]
