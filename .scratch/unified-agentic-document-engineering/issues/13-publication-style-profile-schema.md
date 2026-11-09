# TICK-ADE-13: Publication Style Profile Domain Schema, Built-in Templates & Validation

- **Ticket ID:** `TICK-ADE-13`
- **Milestone:** Milestone E (Publication Style System)
- **Priority:** P2
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Blocked By:** None (can start in parallel with Milestone A/B)
- **Authoritative ADR:** ADR-010 (Publication Style Profile System & Rendering Governance)

---

## 1. Objective

Implement the declarative `PublicationStyleProfile` domain entity, typed typography/geometry schemas, and the 4 standard built-in profile definitions (`Academic`, `Technical Report`, `Textbook`, `Minimal Article`) in `core/entities/publication_style_profile.py` and `resources/styles/`.

---

## 2. User Value

Enables authors to choose and apply publication-grade typography, margins, headers, and color schemes suited for academic papers, engineering reports, or textbooks with reproducible vector quality.

---

## 3. Scope

- Implement pure Python dataclasses in `core/entities/publication_style_profile.py`:
  - `TypographyConfig`: `mainfont: str`, `sansfont: str`, `monofont: str`, `mathfont: Optional[str]`, `fontsize: str`, `linestretch: float`.
  - `GeometryConfig`: `papersize: str`, `margin_top: str`, `margin_bottom: str`, `margin_left: str`, `margin_right: str`.
  - `ColorSchemeConfig`: `colorlinks: bool`, `linkcolor: str`, `urlcolor: str`, `citecolor: str`.
  - `SectionConfig`: `number_sections: bool`, `number_depth: int`, `toc: bool`, `toc_depth: int`, `toc_title: str`.
  - `PublicationStyleProfile` aggregate: `profile_id: str`, `version: str`, `schema_version: int`, `display_name: str`, `description: str`, `is_builtin: bool`, `typography: TypographyConfig`, `geometry: GeometryConfig`, `colors: ColorSchemeConfig`, `sections: SectionConfig`, `documentclass: str`, `classoptions: Tuple[str, ...]`.
- Author 4 standard built-in profile JSON resource files under `resources/styles/`:
  - `academic.json`: A4, `scrartcl`, tight margins ($2\text{ cm}$), numbered sections, high-contrast links.
  - `technical_report.json`: A4, `scrreprt`, $2.5\text{ cm}$ margins, table of contents, shaded code blocks.
  - `textbook.json`: A4, `scrbook`, $3\text{ cm}$ outer reading margins, large headers.
  - `minimal.json`: A4, `article`, clean unnumbered sections, monochrome links.
- Implement strict schema validator in `core/policies/style_profile_policy.py`:
  - Validates font size strings (`10pt`, `11pt`, `12pt`).
  - Validates linestretch bounds ($1.0 \le v \le 1.6$).
  - Validates margin dimension strings (e.g. `^\d+(?:\.\d+)?(?:cm|mm|in|pt)$`).
  - Validates documentclass allowlist (`scrartcl`, `article`, `report`, `scrreprt`, `book`, `scrbook`).

---

## 4. Explicit Non-Goals

- No unrestricted raw LaTeX injection fields in the profile schema (all settings must be declarative and typed).
- No SQLite persistence (handled in `TICK-ADE-14`).

---

## 5. Codebase References

- `application/services/metadata_normalizer.py` (Allowlist schema keys).
- `resources/templates/polpo_preamble.tex` (Base preamble compatibility).

---

## 6. Proposed Files & Components

- Create: `core/entities/publication_style_profile.py`
- Create: `core/policies/style_profile_policy.py`
- Create: `resources/styles/academic.json`
- Create: `resources/styles/technical_report.json`
- Create: `resources/styles/textbook.json`
- Create: `resources/styles/minimal.json`
- Create: `tests/unit/test_publication_style_profile.py`

---

## 7. Domain & Architectural Constraints

- **Rule 6.1 Compliance:** `core/entities/` and `core/policies/` remain 100% free of Qt, SQLite, or network dependencies.
- **D-USER-03 Compliance:** The profile schema must remain distinct from desktop application themes (`ThemePalette`).

---

## 8. Detailed Implementation Requirements

1. Schema validation must reject non-whitelisted document classes or negative margin values.
2. Built-in profiles must have `is_builtin = True` and be marked immutable.
3. Loading profiles from JSON must validate the `schema_version == 1` header.

---

## 9. Acceptance Criteria

- [ ] All 4 standard built-in profile JSON files parse cleanly and pass schema validation.
- [ ] Attempting to construct a profile with invalid margins (e.g. `"negative"`) raises `InvalidStyleProfileError`.
- [ ] Profile serialization to and from JSON is completely lossless.
- [ ] Zero Qt or SQLite imports in `core/entities/publication_style_profile.py`.

---

## 10. Required Tests

- Unit tests in `tests/unit/test_publication_style_profile.py` verifying:
  - Validation of all 4 built-in profiles.
  - Rejection of invalid fontsize, linestretch, documentclass, and margin formats.
  - Correct JSON serialization and deserialization.

---

## 11. Security Requirements

- Prevent arbitrary macro execution by strictly disallowing unvalidated raw TeX strings in profile definitions.

---

## 12. Completion Evidence & Follow-up

- Unit test execution evidence passing 100%.
- Unblocks: `TICK-ADE-14`.
