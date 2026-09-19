# ============================================================
#  infrastructure/document/visual_occurrence_token_scanner.py
#  Lossless Character-Level Visual Occurrence Token Micro-Lexer (FSM)
# ============================================================

from typing import List, Set
from core.domain.visual_token import (
    TokenDiagnosticType,
    VisualOccurrenceToken,
    classify_token_metadata,
    serialize_canonical_token,
)
from application.ports.token_scanner import (
    ITokenScanner,
    MalformedOccurrenceTokenDTO,
    ScanResultDTO,
    ScannedOccurrenceTokenDTO,
)


def _unescape_scanned_alt(alt: str) -> str:
    """
    Reverses CommonMark alt text escaping:
    \\] -> ], \\[ -> [, \\\\ -> \\
    Normalizes internal newlines to spaces for consistency with canonical value object representation.
    """
    unescaped = alt.replace("\\]", "]").replace("\\[", "[")
    unescaped = unescaped.replace("\\\\", "\\")
    # Replace multiline breaks with spaces
    lines = [line.strip() for line in unescaped.splitlines() if line.strip()]
    return " ".join(lines)


class VisualOccurrenceTokenScanner(ITokenScanner):
    """
    Deterministic finite-state micro-lexer scanning raw Markdown character-by-character
    to discover all managed visual occurrence tokens with exact source spans [start_char, end_char).
    Does NOT use regex for parsing architecture.
    """

    def scan(self, text: str) -> ScanResultDTO:
        scanned_tokens: List[ScannedOccurrenceTokenDTO] = []
        malformed_tokens: List[MalformedOccurrenceTokenDTO] = []
        seen_occurrences: Set[str] = set()
        duplicate_occurrence_ids: List[str] = []

        n = len(text)
        i = 0

        while i < n:
            # 1. Look for '![' starting an image token
            if text[i] == "!" and i + 1 < n and text[i + 1] == "[":
                token_start = i
                i += 2

                # 2. Scan ALT text until unescaped ']'
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
                    # Unterminated alt text; backtrack to token_start + 1
                    i = token_start + 1
                    continue

                # 3. Check for immediate '(' opening the destination part
                if i >= n or text[i] != "(":
                    i = token_start + 1
                    continue
                i += 1  # consume '('

                # 4. Scan DESTINATION (URI)
                dest_chars: List[str] = []
                dest_escaped = False
                paren_depth = 0
                has_title = False
                destination_closed_immediately = False

                while i < n:
                    c = text[i]
                    if dest_escaped:
                        dest_chars.append(c)
                        dest_escaped = False
                        i += 1
                    elif c == "\\":
                        dest_escaped = True
                        dest_chars.append(c)
                        i += 1
                    elif c == "(":
                        paren_depth += 1
                        dest_chars.append(c)
                        i += 1
                    elif c == ")":
                        if paren_depth > 0:
                            paren_depth -= 1
                            dest_chars.append(c)
                            i += 1
                        else:
                            # Closed without title attribute: ![alt](destination)
                            destination_closed_immediately = True
                            i += 1  # consume ')'
                            break
                    elif c in (" ", "\t", "\n", "\r"):
                        if paren_depth == 0:
                            # Whitespace separating destination from title
                            has_title = True
                            break
                        else:
                            dest_chars.append(c)
                            i += 1
                    else:
                        dest_chars.append(c)
                        i += 1

                if destination_closed_immediately:
                    # Standard image without title attribute; ignored per spec
                    continue

                if not has_title:
                    # Unterminated destination
                    i = token_start + 1
                    continue

                # 5. Skip whitespace between destination and title quote
                while i < n and text[i] in (" ", "\t", "\n", "\r"):
                    i += 1

                if i >= n:
                    i = token_start + 1
                    continue

                if text[i] == ")":
                    # Trailing whitespace before ')', no title attribute
                    i += 1
                    continue

                if text[i] != '"':
                    # Non-standard title or malformed syntax; skip
                    i = token_start + 1
                    continue
                i += 1  # consume opening '"'

                # 6. Scan TITLE attribute inside "..."
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
                    elif c == '"':
                        title_closed = True
                        i += 1  # consume closing '"'
                        break
                    else:
                        title_chars.append(c)
                        i += 1

                if not title_closed:
                    i = token_start + 1
                    continue

                # 7. Skip optional whitespace after title until ')'
                while i < n and text[i] in (" ", "\t", "\n", "\r"):
                    i += 1

                if i >= n or text[i] != ")":
                    i = token_start + 1
                    continue
                i += 1  # consume final ')'

                token_end = i
                raw_token_text = text[token_start:token_end]
                raw_alt = "".join(alt_chars)
                raw_dest = "".join(dest_chars)
                raw_title = "".join(title_chars)

                # 8. Process Title Metadata
                if not raw_title.startswith("polpo:"):
                    # Standard non-managed image with non-polpo title; ignore
                    continue

                diag, reg_id, occ_id = classify_token_metadata(raw_title)

                if diag == TokenDiagnosticType.CANONICAL:
                    assert reg_id is not None and occ_id is not None
                    unescaped_alt = _unescape_scanned_alt(raw_alt)

                    try:
                        token_obj = VisualOccurrenceToken(
                            region_id=reg_id,
                            occurrence_id=occ_id,
                            uri=raw_dest,
                            alt_text=unescaped_alt,
                        )
                    except (ValueError, TypeError) as e:
                        malformed_tokens.append(
                            MalformedOccurrenceTokenDTO(
                                diagnostic_type=TokenDiagnosticType.MALFORMED_SYNTAX,
                                start_char=token_start,
                                end_char=token_end,
                                raw_text=raw_token_text,
                                error_message=str(e),
                            )
                        )
                        continue

                    occ_str = str(occ_id)
                    if occ_str in seen_occurrences and occ_str not in duplicate_occurrence_ids:
                        duplicate_occurrence_ids.append(occ_str)
                    seen_occurrences.add(occ_str)

                    scanned_tokens.append(
                        ScannedOccurrenceTokenDTO(
                            token=token_obj,
                            start_char=token_start,
                            end_char=token_end,
                            raw_text=raw_token_text,
                        )
                    )
                else:
                    # Non-canonical polpo title (malformed syntax, invalid UUID, etc.)
                    malformed_tokens.append(
                        MalformedOccurrenceTokenDTO(
                            diagnostic_type=diag,
                            start_char=token_start,
                            end_char=token_end,
                            raw_text=raw_token_text,
                            error_message=f"Malformed visual token title: {diag.value}",
                        )
                    )
            else:
                i += 1

        return ScanResultDTO(
            tokens=tuple(scanned_tokens),
            malformed_tokens=tuple(malformed_tokens),
            duplicate_occurrence_ids=tuple(duplicate_occurrence_ids),
        )

    def replace_span(
        self,
        text: str,
        start_char: int,
        end_char: int,
        replacement: VisualOccurrenceToken,
    ) -> str:
        """
        Replaces the source span [start_char:end_char] with the serialized replacement token,
        guaranteeing character fidelity for all characters outside the span.
        """
        if not (0 <= start_char <= end_char <= len(text)):
            raise ValueError(
                f"Invalid span [{start_char}:{end_char}] for text of length {len(text)}"
            )
        serialized = serialize_canonical_token(replacement)
        return text[:start_char] + serialized + text[end_char:]
