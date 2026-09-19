# 02: Lossless Character-Level Micro-Lexer (FSM)

**What to build:** A deterministic finite-state micro-lexer that scans raw Markdown text character-by-character and discovers all managed visual occurrence tokens with exact character-span offsets (`[start_char, end_char)`), plus a source-preserving span-replacement helper. After this ticket, any service can locate every managed image token in a Markdown document and replace one without disturbing a single character outside the replaced span.

**Blocked by:** 01: Canonical Visual Token Grammar & Immutable Value Objects.

**Status:** ready-for-agent

## Scope

- Application port interface `ITokenScanner` (Protocol).
- Infrastructure implementation `VisualOccurrenceTokenScanner` — a character-by-character finite-state machine, NOT a regex-based parser architecture.
- `ScanResultDTO` containing discovered tokens (with exact spans) and diagnostics.
- Source-preserving `replace_span()` helper guaranteeing: `result[:start_char] == original[:start_char]` and `result[end_char:] == original[end_char:]`.
- Malformed-token diagnostics emitted as `MalformedOccurrenceTokenDTO` without exceptions.
- Duplicate `occ_id` detection across the scanned document.

## Non-Goals

- Full Markdown parsing or AST construction.
- Any modification of source text during scanning (scan is read-only).
- Controllers, QML, publication, or staging.
- Support for reference-style images (`![alt][ref]`) — these are ignored.
- Images without `"polpo:"` title — ignored as `NON_MANAGED_IMAGE`.

## Files Likely Impacted

- Create: `application/ports/token_scanner.py`
- Create: `infrastructure/document/visual_occurrence_token_scanner.py`
- Create: `tests/unit/test_visual_occurrence_token_scanner.py`

## Public Interfaces / Contracts

```python
class ITokenScanner(Protocol):
    def scan(self, text: str) -> ScanResultDTO: ...
    def replace_span(self, text: str, start_char: int, end_char: int,
                     replacement: VisualOccurrenceToken) -> str: ...
```

### Scanner State Machine States

`SCAN_TEXT` → `EXCLAMATION` → `ALT_TEXT` → `ALT_CLOSE` → `DESTINATION` → `TITLE_SEARCH` → `POLPO_TITLE` → `CLOSE_PAREN`

Handles: escaped `\]`, escaped `\"`, balanced parentheses in destinations, multiline image syntax, multiple tokens on one line, Unicode/Persian alt text.

### Replacement Invariant (Character Fidelity)

For any replacement at span `[s, e)`:
- `result[:s] == original[:s]`
- `result[e_original:] == original[e_original:]` (where `e_original` is the end offset in the original text)

## Architectural Invariants

- The scanner is a narrow lexical layer, NOT a second Markdown parser.
- Regex may be used only as local primitives inside state transitions, never as the parser architecture.
- The scanner resides in `infrastructure/document/` and implements the `ITokenScanner` port from `application/ports/`.
- Application code consumes `ITokenScanner`, never the concrete scanner directly.
- Zero AST-to-text serialization or round-tripping.

## Acceptance Criteria

- [ ] Scanner operates as a character-by-character FSM, not `re.finditer` over the whole document.
- [ ] Discovers all canonical `polpo:` tokens with exact `[start_char, end_char)` offsets.
- [ ] `text[token.start_char:token.end_char]` matches the exact token substring in source.
- [ ] Yields `MalformedOccurrenceTokenDTO` for malformed `polpo:` titles without raising.
- [ ] Detects and reports duplicate `occ_id` values within a single document.
- [ ] Ignores standard Markdown images lacking `"polpo:"` title attribute.
- [ ] `replace_span()` preserves all characters outside the target span (character fidelity).
- [ ] Handles escaped brackets, escaped quotes, balanced parentheses, multiline syntax, Unicode/Persian text.
- [ ] All new tests pass. No existing tests broken.

## Tests Required

- `test_scanner_basic_discovery()`: single token, exact span offsets.
- `test_scanner_multiple_tokens_single_line()`: multiple managed tokens on one line.
- `test_scanner_character_spans_exact()`: verify `text[start:end]` matches.
- `test_scanner_escaped_brackets_in_alt()`: `\]` and `\[` handling.
- `test_scanner_escaped_quotes_in_title()`: `\"` handling.
- `test_scanner_balanced_parentheses_in_uri()`: `%28`/`%29` and balanced parens.
- `test_scanner_multiline_image_syntax()`: image spanning multiple lines.
- `test_scanner_unicode_persian_alt()`: RTL and Persian characters.
- `test_scanner_malformed_diagnostics()`: invalid polpo syntax yields diagnostics.
- `test_scanner_duplicate_occurrence_detection()`: same `occ_id` twice.
- `test_scanner_ignores_non_managed_images()`: standard `![alt](url)` without polpo title.
- `test_scanner_lossless_span_replacement()`: character fidelity invariant.
- `test_scanner_replacement_multiple_tokens()`: replace one token among several without disturbing others.

## Commit Boundary

Single commit: `feat(document): implement lossless character-level visual token micro-lexer`

## Rollback

Reverting removes the scanner and port. No existing code depends on them yet.

## Out of Scope

- Publication, staging, commands, controllers (later tickets).
- Reference-style image support.
- AST construction or document-level semantic analysis.
