# ============================================================
#  core/markdown/asset_rewriter.py
#  Pure Domain Markdown Asset Reference Scanner & Rewriter
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Optional

from core.domain.visual_token import unescape_alt_text
from core.markdown.visual_token_mutator import find_opaque_spans, is_opaque_span


@dataclass(frozen=True)
class AssetReference:
    """Immutable value object representing a scanned Markdown image asset reference.

    Attributes:
        start: 0-indexed character start position of the '!' delimiter in the string.
        end: 0-indexed character end position (exclusive) of the closing ')' in the string.
        dest_start: 0-indexed character start position of the destination URI substring.
        dest_end: 0-indexed character end position (exclusive) of the destination URI substring.
        destination: Unescaped/raw destination string value.
        alt_text: Semantic alt text with CommonMark escapes removed.
        title: Optional title attribute string value.
        is_angle_bracketed: True if the destination was enclosed in '<...>'.
    """
    start: int
    end: int
    dest_start: int
    dest_end: int
    destination: str
    alt_text: str = ""
    title: Optional[str] = None
    is_angle_bracketed: bool = False


def scan_asset_references(text: str) -> List[AssetReference]:
    """
    Scans raw Markdown character-by-character to discover all image references outside opaque spans:
    - ![alt](destination)
    - ![alt](destination "title")
    - ![alt](<destination>)
    - ![alt](<destination> "title")
    - ![alt](destination 'title')

    Handles escaped brackets, escaped quotes, balanced parentheses, and opaque code/comment contexts.
    """
    if not text or "![" not in text:
        return []

    opaque_spans = find_opaque_spans(text)
    references: List[AssetReference] = []
    n = len(text)
    i = 0

    while i < n:
        # Check for '![' starting an image reference
        if text[i] == "!" and i + 1 < n and text[i + 1] == "[":
            token_start = i

            # Verify '!' is not escaped by an odd number of backslashes
            bs_count = 0
            k = i - 1
            while k >= 0 and text[k] == "\\":
                bs_count += 1
                k -= 1
            if bs_count % 2 == 1:
                i += 2
                continue

            i += 2

            # Scan ALT text until unescaped ']'
            alt_chars: List[str] = []
            alt_escaped = False
            alt_closed = False

            while i < n:
                c = text[i]
                if alt_escaped:
                    alt_chars.append(c)
                    alt_escaped = False
                    i += 1
                elif c == "\\":
                    alt_escaped = True
                    alt_chars.append(c)
                    i += 1
                elif c == "]":
                    alt_closed = True
                    i += 1
                    break
                else:
                    alt_chars.append(c)
                    i += 1

            if not alt_closed:
                i = token_start + 1
                continue

            # Must be immediately followed by '('
            if i >= n or text[i] != "(":
                i = token_start + 1
                continue
            i += 1  # consume '('

            # Skip leading whitespace inside '('
            while i < n and text[i] in (" ", "\t", "\n", "\r"):
                i += 1

            if i >= n:
                i = token_start + 1
                continue

            # Scan DESTINATION
            is_angle_bracketed = False

            if text[i] == "<":
                is_angle_bracketed = True
                i += 1  # consume '<'
                dest_start = i
                dest_closed = False
                while i < n:
                    c = text[i]
                    if c == "\\":
                        if i + 1 < n:
                            i += 2
                            continue
                        i += 1
                    elif c == ">":
                        dest_end = i
                        dest_closed = True
                        i += 1  # consume '>'
                        break
                    elif c in ("\n", "\r"):
                        break
                    else:
                        i += 1

                if not dest_closed:
                    i = token_start + 1
                    continue
            else:
                dest_start = i
                paren_depth = 0
                dest_closed = False
                has_trailing_clause = False

                while i < n:
                    c = text[i]
                    if c == "\\":
                        if i + 1 < n:
                            i += 2
                            continue
                        i += 1
                    elif c == "(":
                        paren_depth += 1
                        i += 1
                    elif c == ")":
                        if paren_depth > 0:
                            paren_depth -= 1
                            i += 1
                        else:
                            # Closed without title attribute: ![alt](destination)
                            dest_end = i
                            dest_closed = True
                            i += 1  # consume ')'
                            break
                    elif c in (" ", "\t", "\n", "\r"):
                        if paren_depth == 0:
                            # Whitespace separating destination from title
                            dest_end = i
                            has_trailing_clause = True
                            break
                        else:
                            i += 1
                    else:
                        i += 1

                if not dest_closed and not has_trailing_clause:
                    i = token_start + 1
                    continue

            # If not already closed, scan optional title and closing ')'
            title_str: Optional[str] = None
            if not (not is_angle_bracketed and dest_closed):
                # Skip whitespace after destination
                while i < n and text[i] in (" ", "\t", "\n", "\r"):
                    i += 1

                if i >= n:
                    i = token_start + 1
                    continue

                if text[i] == ")":
                    # Closed without title: ![alt](<destination>) or ![alt](dest )
                    i += 1
                elif text[i] in ('"', "'"):
                    quote_char = text[i]
                    i += 1  # consume quote
                    title_chars: List[str] = []
                    title_escaped = False
                    title_closed = False

                    while i < n:
                        c = text[i]
                        if title_escaped:
                            title_chars.append(c)
                            title_escaped = False
                            i += 1
                        elif c == "\\":
                            title_escaped = True
                            title_chars.append(c)
                            i += 1
                        elif c == quote_char:
                            title_closed = True
                            i += 1  # consume quote
                            break
                        else:
                            title_chars.append(c)
                            i += 1

                    if not title_closed:
                        i = token_start + 1
                        continue

                    title_str = "".join(title_chars)

                    # Skip whitespace before closing ')'
                    while i < n and text[i] in (" ", "\t", "\n", "\r"):
                        i += 1

                    if i >= n or text[i] != ")":
                        i = token_start + 1
                        continue
                    i += 1  # consume ')'
                else:
                    i = token_start + 1
                    continue

            token_end = i

            # Verify entire token is not inside an opaque span (code block, inline code, html comment)
            if not is_opaque_span(token_start, token_end, opaque_spans):
                destination_val = text[dest_start:dest_end]
                raw_alt = "".join(alt_chars)
                semantic_alt = unescape_alt_text(raw_alt)

                references.append(
                    AssetReference(
                        start=token_start,
                        end=token_end,
                        dest_start=dest_start,
                        dest_end=dest_end,
                        destination=destination_val,
                        alt_text=semantic_alt,
                        title=title_str,
                        is_angle_bracketed=is_angle_bracketed,
                    )
                )
            continue

        i += 1

    return references


def rewrite_asset_references(
    markdown: str,
    uri_mapping: Mapping[str, str],
) -> str:
    """
    Pure domain function that rewrites destination URIs in Markdown image references
    according to uri_mapping, preserving all formatting, whitespace, comments,
    alt text, math, code blocks, and metadata titles character-for-character.

    Args:
        markdown: The canonical Markdown text string.
        uri_mapping: Mapping of original destination URI -> new relative destination path.

    Returns:
        The portable projection of Markdown text with rewritten asset URIs.
    """
    if not markdown or not uri_mapping:
        return markdown

    refs = scan_asset_references(markdown)
    if not refs:
        return markdown

    # Sort in descending order of destination character position so in-place edits do not invalidate preceding character offsets
    refs_to_rewrite = [ref for ref in refs if ref.destination in uri_mapping]
    if not refs_to_rewrite:
        return markdown

    refs_to_rewrite.sort(key=lambda r: r.dest_start, reverse=True)

    result_chars = list(markdown)
    for ref in refs_to_rewrite:
        new_dest = uri_mapping[ref.destination]
        result_chars[ref.dest_start : ref.dest_end] = list(new_dest)

    return "".join(result_chars)
