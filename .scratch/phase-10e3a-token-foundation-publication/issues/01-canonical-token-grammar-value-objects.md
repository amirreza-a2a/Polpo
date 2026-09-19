# 01: Canonical Visual Token Grammar & Immutable Value Objects

**What to build:** Define the formal CommonMark title-attribute grammar specification and immutable, hashable pure-domain value objects for visual occurrence tokens (`![alt](uri "polpo:region=R;occ=O")`). After this ticket, the project has a single authoritative, testable grammar contract and frozen Python value types that every downstream consumer (scanner, commands, publication) will depend on.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

## Scope

- Pure domain value object `VisualOccurrenceToken` (frozen dataclass, four fields: `region_id: UUID`, `occurrence_id: UUID`, `uri: str`, `alt_text: str`).
- Canonical serialization function producing the deterministic CommonMark image string.
- Grammar validation helpers (UUID format, key ordering).
- Application DTO `VisualOccurrenceTokenDTO` as a transport-neutral representation.
- Diagnostic classification enum: `CANONICAL`, `MALFORMED_SYNTAX`, `INVALID_UUID`, `DUPLICATE_OCCURRENCE`, `NON_MANAGED_IMAGE`.

## Non-Goals

- Scanner/lexer implementation (ticket 02).
- Source-span discovery (ticket 02).
- Any runtime, controller, or QML changes.
- Extra metadata fields or extension mechanisms — the grammar is strictly `region` + `occ` only for this phase.

## Files Likely Impacted

- Create: `core/domain/visual_token.py`
- Create: `application/dto/token_dto.py`
- Create: `tests/unit/test_visual_token_domain.py`

## Public Interfaces / Contracts

### Canonical Token Grammar

```
![<alt_text>](<destination_uri> "polpo:region=<region_uuid>;occ=<occ_uuid>")
```

- **Mandatory keys:** `region` first, `occ` second. No extra keys in Phase 10E.3a.
- **Separators:** `;` between pairs, `=` between key and value. Zero whitespace.
- **UUID format:** Lowercase 36-char hyphenated UUIDv4.
- **Alt text escaping:** `\]` for literal `]`, `\[` for literal `[`, `\\` for literal `\`. Newlines normalized to spaces in canonical serialization.
- **URI:** `file://` absolute URI. Spaces percent-encoded as `%20`. Literal parentheses percent-encoded.
- **Title quotes:** Double quotes. Internal quotes escaped as `\"`.

### Value Object

```python
@dataclass(frozen=True)
class VisualOccurrenceToken:
    region_id: UUID
    occurrence_id: UUID
    uri: str
    alt_text: str
    # No extra_metadata. No mutable Dict. Fully hashable.
```

## Architectural Invariants

- No mutable fields or generic extension maps inside the frozen value object.
- `hash(token)` succeeds. Two tokens are equal iff all four fields are identical.
- Canonical serialization enforces deterministic key order (`region` then `occ`).
- The value object belongs to `core/domain/` with zero infrastructure or framework imports.
- The DTO belongs to `application/dto/` with zero infrastructure imports.

## Acceptance Criteria

- [ ] `VisualOccurrenceToken` is frozen, hashable, and contains no mutable fields.
- [ ] Canonical serialization produces exact CommonMark image syntax with title attribute.
- [ ] Round-trip: `serialize(parse_fields(token_string))` produces a canonical form (not necessarily byte-identical to arbitrary source, but deterministic).
- [ ] UUID validation rejects non-UUID4 strings.
- [ ] Unicode, Persian characters, and escaped brackets/quotes in alt text are handled correctly.
- [ ] Diagnostic classification enum covers all five categories.
- [ ] All new tests pass. No existing tests broken.

## Tests Required

- `test_canonical_token_serialization()`: format correctness.
- `test_token_metadata_key_ordering()`: `region` first, `occ` second.
- `test_token_persian_and_unicode_alt()`: complex alt text.
- `test_token_escaped_quotes_and_brackets()`: escaping rules.
- `test_token_hashability_and_equality()`: frozen dataclass semantics.
- `test_token_rejects_invalid_uuid()`: validation.

## Commit Boundary

Single commit: `feat(domain): define canonical visual occurrence token grammar and value objects`

## Rollback

Reverting this commit removes the value objects and grammar. No existing code depends on them yet.

## Out of Scope

- Scanner implementation, source spans, span replacement (ticket 02).
- Staging, publication, controllers, QML (later tickets).
- Any `extra_metadata` or extension mechanism.
