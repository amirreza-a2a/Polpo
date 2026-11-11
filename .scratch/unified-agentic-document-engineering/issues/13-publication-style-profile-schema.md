# TICK-ADE-13: Publication Style Profile Domain Schema, Built-in Templates & Validation

- **Ticket ID:** `TICK-ADE-13`
- **Title:** Publication Style Profile Domain Schema, Built-in Templates & Validation
- **Milestone:** Milestone E (Publication Style System)
- **Priority:** P2
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Implement the declarative `PublicationStyleProfile` domain entity, typed typography/geometry schemas, 4-scope visual separation, and the 4 standard built-in profile definitions (`Academic`, `Technical Report`, `Textbook`, `Minimal Article`) in `core/entities/publication_style_profile.py` and `resources/styles/`.
- **Explicit Non-Goals:**
  - No unrestricted raw LaTeX injection fields in the profile schema (Finding F).
  - No SQLite persistence (handled in `TICK-ADE-14`).
  - No deferred features (custom headers/footers, callout theme packages, custom syntax highlighting).
- **Prerequisites:** `TICK-ADE-05` completed; ADR-010 approved.
- **Dependencies (Blocked By):** `TICK-ADE-05`.
- **Unblocks:** `TICK-ADE-14`.

---

## 1. Existing Source-Code Integration Points

- `application/services/metadata_normalizer.py`: Versioned YAML allowlist keys (`ALLOWED_ROOT_KEYS_V1`).
- `resources/templates/polpo_preamble.tex`: Base typesetting preamble compatibility.

---

## 2. Proposed Domain Interfaces & DTOs

```python
@dataclass(frozen=True)
class TypographyConfig:
    mainfont: str
    sansfont: str
    monofont: str
    mathfont: Optional[str]
    fontsize: str                      # "10pt", "11pt", "12pt"
    linestretch: float                 # 1.0 to 1.6
    strikethrough_engine: str = "default" # "default", "soul", or proposed "ulem" (awaiting D-PROP-08 approval)

@dataclass(frozen=True)
class GeometryConfig:
    papersize: str                     # "a4", "letter"
    margin_top: str
    margin_bottom: str
    margin_left: str
    margin_right: str

@dataclass(frozen=True)
class ColorSchemeConfig:
    colorlinks: bool
    linkcolor: str
    urlcolor: str
    citecolor: str

@dataclass(frozen=True)
class SectionConfig:
    number_sections: bool
    number_depth: int                  # 1 to 4
    toc: bool
    toc_depth: int                     # 1 to 3
    toc_title: str

@dataclass(frozen=True)
class PublicationStyleProfile:
    profile_id: str                    # "builtin:academic", "user:custom_report_v1"
    version: str                       # "1.0.0"
    schema_version: int                # 1
    display_name: str
    description: str
    is_builtin: bool
    typography: TypographyConfig
    geometry: GeometryConfig
    colors: ColorSchemeConfig
    sections: SectionConfig
    documentclass: str                 # "scrartcl", "article", "report", "scrreprt", "book", "scrbook"
    classoptions: Tuple[str, ...]
```

---

## 3. Detailed Implementation Requirements

1. **Four-Scope Separation (Finding F):**
   - Strictly isolates Scope 2 (Publication Theme) from Scope 1 (Application Theme), Scope 3 (Content Style), and Scope 4 (Product Template).
2. **Strict Schema Validation (`core/policies/style_profile_policy.py`):**
   - Font sizes: strictly allowlisted (`"10pt"`, `"11pt"`, `"12pt"`).
   - Linestretch: bounded ($1.0 \le v \le 1.6$).
   - Margin dimensions: regex `^\d+(\.\d+)?(cm|mm|in|pt)$`, bounded between $1.0\text{ cm}$ and $5.0\text{ cm}$.
   - Documentclass: allowlisted enum (`"scrartcl"`, `"article"`, `"report"`, `"scrreprt"`, `"book"`, `"scrbook"`).
   - Class options: allowlisted tokens (`"DIV=12"`, `"parskip=half"`, `"twocolumn"`, `"oneside"`, `"twoside"`).
   - Strikethrough engine: typed enum (`"default"`, `"soul"`, with proposed `"ulem"` subject to explicit maintainer approval of D-PROP-08).
3. **Built-in Profile Packaging (D-PROP-06 — Awaiting Maintainer Approval):**
   - Evaluates Option A (shipped version-controlled built-in JSON assets under `resources/styles/`) vs Option B (database-seeded built-ins). Option A provides immutable default styles:
     - `academic.json`: A4, `scrartcl`, $2.0\text{ cm}$ margins, numbered sections, high-contrast links, strikethrough: `"default"`.
     - `technical_report.json`: A4, `scrreprt`, $2.5\text{ cm}$ margins, table of contents, strikethrough: `"default"`.
     - `textbook.json`: A4, `scrbook`, $3.0\text{ cm}$ margins, large headings, strikethrough: `"default"`.
     - `minimal.json`: A4, `article`, clean unnumbered sections, monochrome links, strikethrough: `"default"`.
   - Decisions `D-PROP-06` and `D-PROP-08` remain `PROPOSED — AWAITING EXPLICIT MAINTAINER APPROVAL`. Built-in profiles do not prematurely treat an unapproved rendering engine as active product behavior.

---

## 4. Acceptance Criteria

- [ ] All 4 standard built-in profile JSON files parse cleanly and pass schema validation.
- [ ] Constructing a profile with invalid margins (e.g. `"negative"`) raises `InvalidStyleProfileError`.
- [ ] Attempting to inject raw TeX commands (`\usepackage`, `\newcommand`) is rejected by the schema validator.
- [ ] Profile serialization to and from JSON is completely lossless.
- [ ] AST check confirms zero imports of `PySide6`, `Qt`, or `sqlite3` in `core/entities/publication_style_profile.py`.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_publication_style_profile.py`:
  - `test_builtin_profiles_validation()`: Validates all 4 shipped JSON profiles.
  - `test_reject_invalid_font_size()`: Asserts `"14pt"` raises error.
  - `test_reject_invalid_margin_syntax()`: Asserts non-dimension strings raise error.
  - `test_reject_unallowlisted_documentclass()`: Asserts `"standalone"` raises error.
  - `test_json_roundtrip_fidelity()`: Confirms lossless serialization.

### 5.2 Negative & Security Tests
- `test_reject_raw_tex_macro_injection()`: Ensures profiles cannot contain arbitrary LaTeX macro strings.

### 5.3 Concurrency & Persistence Considerations
- Pure domain entities; immutable once instantiated.

---

## 6. Error and Recovery Behavior

- Validation failures raise `InvalidStyleProfileError` with detailed field paths explaining the violation.

---

## 7. Observability & Audit Requirements

- Built-in profile files are validated at application test time; hashes are checked for tampering.

---

## 8. Documentation Updates

- Document 4-scope visual separation and schema in ADR-010.

---

## 9. Required Project Skills

- `domain-modeling`: Establishing profile domain entities.
- `tdd`: Test-first implementation of schema validation.

---

## 10. Verification Commands

```bash
pytest tests/unit/test_publication_style_profile.py -v
python3 -c "import json, glob; [json.load(open(f)) for f in glob.glob('resources/styles/*.json')]; print('All style JSONs valid')"
```

---

## 11. Definition of Done

- All unit tests pass with 100% coverage.
- All 4 built-in JSON profiles authored and validated.
