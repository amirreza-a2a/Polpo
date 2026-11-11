# ADR-010: Publication Style Profile System, Declarative Rendering Governance, and Visual Identity Boundaries

- **Status:** Proposed (Awaiting Maintainer Approval)
- **Date:** 2026-11-09 (Revised: 2026-11-11)
- **Scope:** Publication Style Profiles, Declarative Typesetting Schema, Four-Scope Visual Separation, Profile Persistence, Built-in Profiles, Font Resolution, Quarto Metadata Injection, Preamble Macro Prohibition
- **Authoritative Baseline:** Commit `25bb95d198c34c035b5d66513166e06494d82cdb`
- **Related ADRs:** ADR-003 (Quarto PDF Export), ADR-005 (Unified Diagram Preview & Authoring), ADR-007 (Agent Orchestration & Tool Governance), ADR-008 (Working Copy & Patch Architecture), ADR-009 (Quarto Diagnostics & PDF Repair)
- **Confirmed Requirements:** D-USER-01 (Unified Assistant Scope), D-USER-02 (Supervised Agent Autonomy), D-USER-03 (Document vs Rendering-System Repair)

---

## 1. Context

Polpo produces publication-grade vector PDF documents via Quarto CLI and XeLaTeX ([ADR-003](docs/adr/2026-10-12-adr-003-quarto-pdf-export-integration.md)). Currently, all compiled PDF documents inherit typographic styling from a single, static preamble template ([`resources/templates/polpo_preamble.tex`](resources/templates/polpo_preamble.tex)) and YAML front matter validated against versioned allowlists ([`ALLOWED_ROOT_KEYS_V1`](application/services/metadata_normalizer.py)).

Meanwhile, the desktop user interface provides application-level visual themes (Dark, Light, High Contrast) managed by `ThemePalette` ([`interfaces/desktop/theme.py`](interfaces/desktop/theme.py)).

When authors request the AI Assistant to "make this document look professional" or "apply an academic style," there is no structured mechanism for customizing or switching document publication styles. Furthermore, without clear architectural boundaries, AI models often confuse the **Application Theme** (desktop GUI appearance) with the **Publication Theme** (PDF output typography), or attempt to inject raw, arbitrary LaTeX macros into document front matter, creating security risks and compilation breakage.

---

## 2. Problem Statement

1. **Confusion of Visual Scopes:** Desktop themes, publication styles, authoring conventions, and internal engine templates are conflated if not separated by explicit domain boundaries.
2. **Unstructured Style Customization:** Authors currently have no way to choose between distinct publication formats (e.g. Academic Paper, Technical Report, Textbook, Minimal Article) other than manually editing YAML front matter or hacking raw LaTeX headers.
3. **Security Risks from Unvetted LaTeX Injection (Finding A, Finding F):** If an AI assistant generates raw, arbitrary LaTeX commands in `header-includes` to fulfill style requests, it bypasses allowlists, introduces package conflicts, or risks shell-escape and path traversal exploits.
4. **Reproducibility & Versioning:** Document formatting must be deterministic and reproducible across machines and application upgrades. Style definitions must be versioned and stored cleanly without corrupting document Markdown source.

---

## 3. Architectural Forces & Constraints

1. **Four-Scope Visual Separation (Finding F):**
   - *Scope 1: Application Theme:* Desktop QML appearance (surface colors, text contrast, window chrome).
   - *Scope 2: Publication Theme (Style Profile):* Output PDF appearance (typography, margins, font families, headers, footers).
   - *Scope 3: Document Content Style:* Semantic structure, tone, and authoring guidelines in Markdown.
   - *Scope 4: Product Rendering Template:* Polpo-managed engine code (`polpo_preamble.tex`, barrier `_quarto.yml`).
2. **Declarative, Controlled Token Schema:** Publication styles must be expressed via validated, typed settings, NOT arbitrary AI-generated TeX scripts.
3. **Cross-Platform Portability ([`AGENTS.md`](AGENTS.md) Rule 28):** Font families specified in profiles must resolve cleanly across Windows, macOS, and Linux without crashing XeLaTeX when a font is missing.
4. **Bilingual / RTL First-Class Support:** Persian and Latin typography, directional spacing, and HarfBuzz font configurations must remain robust.
5. **Clean Architecture ([`AGENTS.md`](AGENTS.md) Rule 6):** Profile models reside in `core/`; rendering and validation reside in `application/`; disk injection resides in `infrastructure/`.

---

## 4. Confirmed Requirements (Maintainer Settled)

- **D-USER-01 (Unified Assistant Scope):** Publication theme and document style management is an explicit capability of the Unified AI Assistant.
- **D-USER-03 (Scope Separation):** Document-specific style adjustments must never modify the global product template (`polpo_preamble.tex`).

---

## 5. Considered Alternatives

### Alternative A: Raw AI-Generated LaTeX Preamble Injection
- *Description:* Allow the assistant to generate arbitrary TeX commands and inject them into `header-includes`.
- *Rejection Rationale:* Extremely dangerous. Bypasses metadata allowlists, risks LaTeX package collisions (e.g. re-declaring geometry or microtype), and breaks cross-platform portability.

### Alternative B: Hardcoded Document Styles in Python Code
- *Description:* Embed a fixed switch statement in `QuartoPdfExportService` selecting different hardcoded preamble files.
- *Rejection Rationale:* Inflexible; prevents user customization, community profiles, or AI-assisted parameter tuning (e.g. adjusting margins or font sizes).

### Alternative C: Proposed Decision — Versioned Declarative `PublicationStyleProfile` System
- *Description:* Define a typed, declarative schema for publication profiles. Ship built-in profiles as read-only JSON resources; persist user-customized profiles in SQLite. Inject validated profile tokens into Quarto staging metadata under strict allowlist governance.
- *Selection Rationale:* Completely safe and declarative; provides full reproducibility; allows supervised AI adjustments of concrete style tokens without raw code injection.

---

## 6. Proposed Decision

We propose establishing the **Publication Style Profile System** consisting of:
1. A **Strict Four-Scope Visual Separation Model** distinguishing Application Theme, Publication Theme, Content Style, and Product Template.
2. A typed, declarative **`PublicationStyleProfile` Schema** in `core/entities/publication_style_profile.py` with explicit initial release capabilities vs. deferred features (Finding F).
3. Four standard **Built-in Profiles**: `Academic`, `Technical Report`, `Textbook`, and `Minimal Article`.
4. A persistence and resolution service in `application/services/publication_style_service.py` supporting SQLite storage for user-customized profiles under migration `006_agent_task_tables.sql` (Finding I).
5. A deterministic **Staging Workspace Injector** translating profile tokens into normalized Quarto front-matter settings under strict allowlists.
6. Absolute prohibition of arbitrary raw LaTeX macro generation by the Agent (Finding A, Finding F).

---

## 7. Detailed Design

### 7.1 The Four Visual Identity Scopes

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        Scope 1: Application Theme                      │
│   (Desktop GUI Colors, QML ThemePalette: Dark / Light / High Contrast) │
└────────────────────────────────────────────────────────────────────────┘
                                    │
                               (Independent)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     Scope 2: Publication Style Profile                 │
│  (PDF Vector Typography, Geometry, Fonts, Spacing, Headers & Footers)   │
└────────────────────────────────────────────────────────────────────────┘
                                    │
                               (Independent)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      Scope 3: Document Content Style                   │
│   (Markdown Prose, Heading Levels, Callouts, Technical Vocabulary)     │
└────────────────────────────────────────────────────────────────────────┘
                                    │
                               (Independent)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                    Scope 4: Product Rendering Template                 │
│      (Polpo Engine Infrastructure: polpo_preamble.tex, barrier YAML)    │
└────────────────────────────────────────────────────────────────────────┘
```

The Assistant is strictly instructed that a request to "change theme" must be clarified or inferred based on context:
- "Make the editor darker" $\to$ Scope 1 (Application Theme).
- "Format this document for publication in IEEE style" $\to$ Scope 2 (Publication Style Profile).
- "Make the writing more formal" $\to$ Scope 3 (Document Content Style).

### 7.2 Scope Reconciliation: Initial Release vs. Deferred Features (Finding F)

To ensure the system remains 100% implementable and safe, the initial release scope is strictly delineated:

#### Capabilities Supported in Initial Release:
- **Typography:**
  - `mainfont`, `sansfont`, `monofont`: validated against system font resolution.
  - `fontsize`: strictly enumerated (`"10pt"`, `"11pt"`, `"12pt"`).
  - `linestretch`: bounded floating point number ($1.0 \le v \le 1.6$).
- **Geometry:**
  - `papersize`: strictly enumerated (`"a4"`, `"letter"`).
  - `margins` (`top`, `bottom`, `left`, `right`): dimension syntax validated via regex `^\d+(\.\d+)?(cm|mm|in|pt)$` and bounded between $1.0\text{ cm}$ and $5.0\text{ cm}$.
- **Color Scheme:**
  - `colorlinks`: boolean.
  - `linkcolor`, `urlcolor`, `citecolor`: hex `#RRGGBB` or allowlisted CSS/xcolor names (`Maroon`, `Blue`, `ForestGreen`).
- **Section Hierarchy:**
  - `number_sections`: boolean.
  - `number_depth`: integer (1 to 4).
  - `toc`: boolean.
  - `toc_depth`: integer (1 to 3).
  - `toc_title`: string.
- **Document Class & Layout:**
  - `documentclass`: strictly enumerated allowlist (`"scrartcl"`, `"article"`, `"report"`, `"scrreprt"`, `"book"`, `"scrbook"`).
  - `classoptions`: enumerated allowlisted tokens (`"DIV=12"`, `"parskip=half"`, `"twocolumn"`, `"oneside"`, `"twoside"`).
  - `strikethrough_engine`: typed configuration token (`"default"`, `"soul"`, or proposed `"ulem"` awaiting explicit maintainer approval of D-PROP-08).

#### Capabilities Explicitly Deferred to Future Releases:
- Arbitrary raw LaTeX `header-includes` fragments (strictly forbidden).
- Arbitrary running headers/footers with embedded TeX code.
- Custom callout environment styling packages.
- Custom code block syntax highlighting themes beyond Pandoc defaults.
- Custom table package styles (`tabularx` / `booktabs` overrides).
- Custom title/cover page generators.

### 7.3 Schema Safety & Absolute Macro Prohibition

The schema is implemented as pure Python dataclasses in `core/entities/publication_style_profile.py`:

```python
@dataclass(frozen=True)
class TypographyConfig:
    mainfont: str              # Default: "FreeSerif" (fallback to Vazirmatn in Persian)
    sansfont: str              # Default: "FreeSerif"
    monofont: str              # Default: "FreeMono"
    mathfont: Optional[str]    # Optional unicode-math font
    fontsize: str              # "10pt", "11pt", "12pt"
    linestretch: float         # 1.0 to 1.6
    strikethrough_engine: str  # "ulem", "soul", "default" (governed by D-PROP-08; awaiting approval)

@dataclass(frozen=True)
class GeometryConfig:
    papersize: str             # "a4", "letter"
    margin_top: str            # e.g. "2.5cm"
    margin_bottom: str         # e.g. "2.5cm"
    margin_left: str           # e.g. "2.0cm"
    margin_right: str          # e.g. "2.0cm"

@dataclass(frozen=True)
class ColorSchemeConfig:
    colorlinks: bool           # True / False
    linkcolor: str             # Hex color (e.g. "#1a5fb4") or allowlisted name
    urlcolor: str              # e.g. "#1a5fb4"
    citecolor: str             # e.g. "#26a269"

@dataclass(frozen=True)
class SectionConfig:
    number_sections: bool      # True / False
    number_depth: int          # 1 to 4
    toc: bool                  # True / False
    toc_depth: int             # 1 to 3
    toc_title: str             # e.g. "Table of Contents" / "فهرست مطالب"

@dataclass(frozen=True)
class PublicationStyleProfile:
    """Declarative publication style specification."""
    profile_id: str            # "builtin:academic", "user:custom_report_v1"
    version: str               # "1.0.0"
    schema_version: int        # 1
    display_name: str          # "Academic Paper (ACM/IEEE)"
    description: str           # Human-readable explanation
    is_builtin: bool           # True for read-only built-in profiles
    typography: TypographyConfig
    geometry: GeometryConfig
    colors: ColorSchemeConfig
    sections: SectionConfig
    documentclass: str         # "scrartcl", "article", "report", "scrreprt", "book", "scrbook"
    classoptions: Tuple[str, ...] # e.g. ("DIV=12", "parskip=half")
```

#### Explicitly Forbidden Invariants:
- NO arbitrary `\usepackage` directives.
- NO arbitrary `\newcommand` or macro definitions.
- NO raw TeX preamble injection.
- NO shell escape or executable directives.
- NO unvalidated filesystem paths.

### 7.4 Profile Ownership, Packaging & Reproducibility (Finding F)

#### Proposed Decision D-PROP-06: Publication Profile Packaging & Storage Alternatives
Status: `PROPOSED — AWAITING EXPLICIT MAINTAINER APPROVAL`

We evaluate two architectural options for packaging and storing publication style profiles:

- **Option A: Version-Controlled Built-in JSON Assets + SQLite User Overrides**
  - Shipped built-in profiles are static, immutable JSON files in `resources/styles/` (`academic.json`, `technical_report.json`, `textbook.json`, `minimal.json`).
  - User-customized styles are stored in SQLite table `publication_style_profiles` under Migration 006.
  - Pros: Clean git version tracking of defaults; zero schema migration needed when built-in typography evolves; instant out-of-the-box availability without database seed steps.
  - Cons: Requires hybrid lookup logic in `PublicationStyleService` (file system check for `builtin:`, SQLite query for `user:`).
- **Option B: Database-Seeded Built-ins in SQLite Table**
  - All profiles (both built-in and user) reside in SQLite table `publication_style_profiles` populated by migration seeds.
  - Pros: Single unified repository interface (`IStyleProfileRepository.get_profile`).
  - Cons: Modifying shipped built-in profiles requires database schema migrations; risk of accidental mutation or overwrite in SQLite.

*Recommendation:* Option A for immutable versioning of standard templates and clean override precedence.
*Status:* `PROPOSED — AWAITING EXPLICIT MAINTAINER APPROVAL`

1. **Built-in Profiles:**
   - Under Option A, shipped as version-controlled JSON assets under `resources/styles/` (`academic.json`, `technical_report.json`, `textbook.json`, `minimal.json`).
   - Marked `is_builtin = True`, immutable, and read-only.
2. **User-Defined Profiles:**
   - Persisted in SQLite table `publication_style_profiles` under migration `006_agent_task_tables.sql`.
   - Modifiable only via `PublicationStyleService.save_custom_profile()`.
3. **Document Selection & Lineage:**
   - Document front matter records the assigned profile ID and version (e.g. `style_profile: "builtin:academic"`, `style_profile_version: "1.0.0"`).
4. **Effective Resolved Profile Snapshot:**
   - Associated with each export operation as an immutable snapshot.
5. **Deterministic Handling of Edge Cases:**
   - **Profile Updated:** Existing documents retain their assigned profile ID. When re-exporting, the latest profile configuration is resolved.
   - **Profile Deleted:** If a user profile is deleted, documents referencing it fall back to `builtin:academic` with an explicit UI warning.
   - **Document Moved Across Workstations:** Built-in profiles resolve identically everywhere. If a custom user profile is absent on the destination machine, the export defaults gracefully to `builtin:academic`.
   - **Built-in Profile Updated in Application Release:** Built-in profiles use semantic versioning; backward compatibility is preserved.
   - **Font Missing on Machine:** `PublicationStyleService` verifies font existence using system fontconfig. If a font is absent, it falls back gracefully to `FreeSerif` / `Vazirmatn` without failing XeLaTeX compilation.

### 7.5 Standard Built-in Profiles

1. **`builtin:academic` (Academic Paper):**
   - Documentclass: `scrartcl` (KOMA-Script article).
   - Font: `FreeSerif` / `Vazirmatn`, size: `10pt`, linestretch: `1.15`, strikethrough: `"default"` (proposal `"ulem"` subject to explicit maintainer approval of D-PROP-08).
   - Geometry: A4, tight academic margins ($2.0\text{ cm}$).
   - Numbered sections enabled (`number_sections: true`, depth 3).
   - High-contrast citations (`citecolor: #26a269`, `linkcolor: #1a5fb4`).
2. **`builtin:technical_report` (Technical & Engineering Report):**
   - Documentclass: `scrreprt`.
   - Font: `FreeSerif` / `Vazirmatn`, size: `11pt`, linestretch: `1.25`, strikethrough: `"default"`.
   - Geometry: A4, comfortable margins ($2.5\text{ cm}$).
   - Table of contents enabled (`toc: true`).
3. **`builtin:textbook` (Educational & Course Lesson):**
   - Documentclass: `scrbook`.
   - Font: `FreeSerif` / `Vazirmatn`, size: `12pt`, linestretch: `1.3`, strikethrough: `"default"`.
   - Geometry: A4, wide reading margins ($3.0\text{ cm}$ outer for marginal notes).
   - Large headings, colored links.
4. **`builtin:minimal` (Minimalist Clean Document):**
   - Documentclass: `article`.
   - Font: `FreeSerif`, size: `11pt`, linestretch: `1.2`, strikethrough: `"default"`.
   - Monochrome links (`colorlinks: false`).
   - Clean unnumbered sections, compact margins.

### 7.6 Staging Injection

When `QuartoPdfExportService` stages a document for compilation:
1. It queries `PublicationStyleService.get_effective_profile(job_id, document_metadata)`.
2. The effective profile is mapped into the normalized metadata dictionary (`raw_metadata`):
   ```python
   metadata["documentclass"] = profile.documentclass
   metadata["fontsize"] = profile.typography.fontsize
   metadata["linestretch"] = profile.typography.linestretch
   metadata["papersize"] = profile.geometry.papersize
   metadata["geometry"] = [
       f"top={profile.geometry.margin_top}",
       f"bottom={profile.geometry.margin_bottom}",
       f"left={profile.geometry.margin_left}",
       f"right={profile.geometry.margin_right}",
   ]
   metadata["number-sections"] = profile.sections.number_sections
   metadata["number-depth"] = profile.sections.number_depth
   metadata["toc"] = profile.sections.toc
   metadata["toc-depth"] = profile.sections.toc_depth
   metadata["colorlinks"] = profile.colors.colorlinks
   metadata["linkcolor"] = profile.colors.linkcolor
   metadata["urlcolor"] = profile.colors.urlcolor
   metadata["citecolor"] = profile.colors.citecolor
   ```
3. `StagingWorkspaceManager` passes this normalized dictionary through `normalize_quarto_pdf_metadata()` which validates every key against the existing allowlist (`ALLOWED_ROOT_KEYS_V1`).
4. **Zero Unrestricted TeX Injection:** The profile never generates arbitrary `\usepackage` lines or raw macros in the author's document; all styling flows through the existing, verified allowlist parameters.

---

## 8. Consequences

### Positive:
- **Absolute Visual Safety:** Complete decoupling between desktop application appearance and exported document typography eliminates user confusion.
- **Strict Macro Security:** Zero arbitrary LaTeX injection into preambles; styling managed via typed tokens.
- **Reproducible Styling:** Declarative profile schemas ensure identical rendering across restarts and across machines.
- **Safe Fallbacks:** Missing profiles or fonts fall back cleanly without compilation crashes.

### Negative / Trade-offs:
- **Bounded Customization:** Advanced custom LaTeX packages cannot be configured through Style Profiles until a future structured model is designed.
- **Profile Asset Maintenance:** Built-in JSON assets must be maintained and versioned alongside core templates.

---

## 9. Testing & Verification Strategy

- **Schema Validation Tests:** Unit tests verifying that `PublicationStyleProfile` correctly validates margin formats, linestretch values, and documentclass choices.
- **Quarto Injection Tests:** Verify that staged documents generated with each of the 4 built-in profiles compile cleanly under XeLaTeX.
- **Font Fallback Tests:** Verify that missing font names fall back gracefully to `FreeSerif` / `Vazirmatn` without failing compilation.
- **Immutability Tests:** Verify that built-in profiles reject deletion or mutation attempts with `ImmutableBuiltinProfileError`.
