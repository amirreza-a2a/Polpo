# ADR-010: Publication Style Profile System, Declarative Rendering Governance, and Visual Identity Boundaries

- **Status:** Proposed (Awaiting Maintainer Approval)
- **Date:** 2026-11-09
- **Scope:** Publication Style Profiles, Declarative Typesetting Schema, Theme Scope Separation, Profile Persistence, Built-in Profiles, Font Resolution, Quarto Metadata Injection
- **Authoritative Baseline:** Commit `25bb95d198c34c035b5d66513166e06494d82cdb`
- **Related ADRs:** ADR-003 (Quarto PDF Export), ADR-005 (Unified Diagram Preview & Authoring), ADR-007 (Agent Orchestration & Tool Governance), ADR-008 (Working Copy & Patch Architecture), ADR-009 (Quarto Diagnostics & PDF Repair)
- **Confirmed Requirements:** D-USER-01 (Unified Assistant Scope), D-USER-02 (Supervised Agent Autonomy), D-USER-03 (Document vs Rendering-System Repair)

---

## 1. Context

Polpo produces publication-grade vector PDF documents via Quarto CLI and XeLaTeX ([ADR-003](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/docs/adr/2026-10-12-adr-003-quarto-pdf-export-integration.md)). Currently, all compiled PDF documents inherit typographic styling from a single, static preamble template ([`resources/templates/polpo_preamble.tex`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/resources/templates/polpo_preamble.tex)) and YAML front matter validated against versioned allowlists ([`ALLOWED_ROOT_KEYS_V1`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/metadata_normalizer.py#L34)).

Meanwhile, the desktop user interface provides application-level visual themes (Dark, Light, High Contrast) managed by `ThemePalette` ([`interfaces/desktop/theme.py`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/theme.py)).

When authors request the AI Assistant to "make this document look professional" or "apply an academic style," there is no structured mechanism for customizing or switching document publication styles. Furthermore, without clear architectural boundaries, AI models often confuse the **Application Theme** (desktop GUI appearance) with the **Publication Theme** (PDF output typography), or attempt to inject raw, arbitrary LaTeX macros into document front matter, creating security risks and compilation breakage.

---

## 2. Problem Statement

1. **Confusion of Visual Scopes:** Desktop themes, publication styles, authoring conventions, and internal engine templates are conflated if not separated by explicit domain boundaries.
2. **Unstructured Style Customization:** Authors currently have no way to choose between distinct publication formats (e.g. Academic Paper, Technical Report, Textbook, Minimal Article) other than manually editing YAML front matter or hacking raw LaTeX headers.
3. **Security Risks from Unvetted LaTeX Injection:** If an AI assistant generates raw, arbitrary LaTeX commands in `header-includes` to fulfill style requests, it bypasses allowlists, introduces package conflicts, or risks shell-escape and path traversal exploits.
4. **Reproducibility & Versioning:** Document formatting must be deterministic and reproducible across machines and application upgrades. Style definitions must be versioned and stored cleanly without corrupting document Markdown source.

---

## 3. Architectural Forces & Constraints

1. **Four-Scope Visual Separation:**
   - *Scope 1: Application Theme:* Desktop QML appearance (surface colors, text contrast, window chrome).
   - *Scope 2: Publication Theme (Style Profile):* Output PDF appearance (typography, margins, font families, headers, footers).
   - *Scope 3: Document Content Style:* Semantic structure, tone, and authoring guidelines in Markdown.
   - *Scope 4: Product Rendering Template:* Polpo-managed engine code (`polpo_preamble.tex`, barrier `_quarto.yml`).
2. **Declarative, Controlled Token Schema:** Publication styles must be expressed via validated, typed settings, NOT arbitrary AI-generated TeX scripts.
3. **Cross-Platform Portability ([`AGENTS.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/AGENTS.md) Rule 28):** Font families specified in profiles must resolve cleanly across Windows, macOS, and Linux without crashing XeLaTeX when a font is missing.
4. **Bilingual / RTL First-Class Support:** Persian and Latin typography, directional spacing, and HarfBuzz font configurations must remain robust.
5. **Clean Architecture ([`AGENTS.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/AGENTS.md) Rule 6):** Profile models reside in `core/`; rendering and validation reside in `application/`; disk injection resides in `infrastructure/`.

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
1. A **Strict Scope Separation Model** distinguishing Application Theme, Publication Theme, Content Style, and Product Template.
2. A typed, declarative **`PublicationStyleProfile` Schema** in `core/entities/publication_style_profile.py`.
3. Four standard **Built-in Profiles**: `Academic`, `Technical Report`, `Textbook`, and `Minimal Article`.
4. A persistence and resolution service in `application/services/publication_style_service.py` supporting SQLite storage for user-customized profiles.
5. A deterministic **Staging Workspace Injector** translating profile tokens into normalized Quarto front-matter settings.

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

### 7.2 The `PublicationStyleProfile` Schema

```python
@dataclass(frozen=True)
class TypographyConfig:
    mainfont: str              # Default: "FreeSerif" (fallback to Vazirmatn in Persian)
    sansfont: str              # Default: "FreeSerif"
    monofont: str              # Default: "FreeMono"
    mathfont: Optional[str]    # Optional unicode-math font
    fontsize: str              # "10pt", "11pt", "12pt"
    linestretch: float         # 1.0 to 1.5

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
    linkcolor: str             # Hex color or standard name (e.g. "#1a5fb4")
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

### 7.3 Standard Built-in Profiles

1. **`builtin:academic` (Academic Paper):**
   - Documentclass: `scrartcl` (KOMA-Script article).
   - Font: `FreeSerif` / `Vazirmatn`, size: `10pt`, linestretch: `1.15`.
   - Geometry: A4, tight academic margins ($2.0\text{ cm}$).
   - Numbered sections enabled (`number_sections: true`, depth 3).
   - High-contrast citations (`citecolor: #26a269`, `linkcolor: #1a5fb4`).
2. **`builtin:technical_report` (Technical & Engineering Report):**
   - Documentclass: `scrreprt`.
   - Font: `FreeSerif` / `Vazirmatn`, size: `11pt`, linestretch: `1.25`.
   - Geometry: A4, comfortable margins ($2.5\text{ cm}$).
   - Table of contents enabled (`toc: true`).
   - Callout blocks and shaded code listings.
3. **`builtin:textbook` (Educational & Course Lesson):**
   - Documentclass: `scrbook`.
   - Font: `FreeSerif` / `Vazirmatn`, size: `12pt`, linestretch: `1.3`.
   - Geometry: A4, wide reading margins ($3.0\text{ cm}$ outer for marginal notes).
   - Large headings, colored accent borders on callouts and TikZ diagrams.
4. **`builtin:minimal` (Minimalist Clean Document):**
   - Documentclass: `article`.
   - Font: `FreeSerif`, size: `11pt`, linestretch: `1.2`.
   - Monochrome links (`colorlinks: false`).
   - Clean unnumbered sections, compact margins.

### 7.4 Profile Resolution & Staging Injection

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

### 7.5 AI-Assisted Style Customization Workflow

When a user instructs the Assistant to adjust document styling (e.g. "Make the margins wider and number all sections"):
1. The Assistant reads the active profile using tool `get_publication_profile`.
2. It generates a structured patch to specific profile tokens using tool `propose_publication_style_override`.
3. The proposed tokens are applied to the `AgentWorkingCopy`.
4. Quarto compiles a candidate PDF in ephemeral staging.
5. The UI presents the user with:
   - A visual side-by-side preview comparing the original PDF candidate with the newly styled candidate.
   - A clean diff of the changed settings (e.g. `Margins: 2.0cm → 3.0cm`, `Number Sections: False → True`).
6. Upon user confirmation ("Apply Style"), the document's assigned style profile is updated in document front matter or SQLite.

---

## 8. Dependency & Data Flow

```text
[User Request: "Apply Academic Style"]
                   │
                   ▼
[AgentController] ──► [AgentOrchestrationService]
                             │
                             ▼
[Tool: get_publication_profile("builtin:academic")]
                             │
                             ▼
[Tool: apply_style_to_working_copy]
                             │
                             ▼
[QuartoPdfExportService: Stage Document with Profile Tokens]
                             │
                             ├── normalize_quarto_pdf_metadata() (Validates allowlist)
                             ├── Injects geometry, fonts, and classoptions into _quarto.yml / qmd
                             └── Compiles candidate PDF in .pdf_staging/
                             │
                             ▼
[PyMuPdfValidator: Verifies Candidate Integrity]
                             │
                             ▼
[ReviewWorkspaceView.qml: Split Candidate Preview & Settings Diff]
                             │
                             ▼
[User Confirms: Profile Saved to Document Metadata under OCC]
```

---

## 9. Security & Privacy Considerations

1. **Deterministic Schema Defense:** Because style profiles are declarative dataclasses rather than executable scripts, prompt injection attacks cannot use style requests to execute malicious TeX code or filesystem reads.
2. **Font Fallback Isolation:** If a custom font name is requested that does not exist on the user's host machine, `QuartoPdfAdapter` detects the missing font via `TexLogSanitizer` and falls back gracefully to `FreeSerif` / `Vazirmatn` without failing compilation.
3. **Template Immutability:** User-customized profiles are persisted in the user's SQLite database (`publication_style_profiles`). The application's core template (`polpo_preamble.tex`) is read-only and never modified.

---

## 10. Failure & Recovery Behavior

1. **Invalid Profile Values:** If an invalid token value is provided (e.g. `margin_top = "invalid"` or `linestretch = -1`), schema validation in `PublicationStyleService` rejects the change immediately with `InvalidStyleProfileError`.
2. **Missing Font Recovery:** If a requested font family cannot be loaded by XeLaTeX, `TexLogSanitizer` reports `missing_fonts`, enabling the assistant to substitute standard system fonts (`FreeSerif`, `FreeSans`, `Vazirmatn`).

---

## 11. Consequences

### Positive:
- **Professional Publications:** Authors can switch between distinct academic, technical, and book publication layouts with one click.
- **Absolute Visual Safety:** Complete decoupling between desktop application appearance and exported document typography eliminates user confusion.
- **Reproducible Styling:** Declarative profile schemas ensure identical rendering across restarts and across machines.

### Negative / Trade-offs:
- **Profile Maintenance:** Supporting 4 built-in publication profiles requires testing each profile across supported operating systems (Linux, Windows, macOS).

---

## 12. Migration & Rollout Strategy

1. **Phase 1:** Implement `core/entities/publication_style_profile.py` with the 4 standard built-in profile definitions.
2. **Phase 2:** Implement SQLite table `publication_style_profiles` and `application/services/publication_style_service.py`.
3. **Phase 3:** Integrate profile token mapping into `QuartoPdfExportService`.
4. **Phase 4:** Add style selection and visual preview controls to `ExportController` and `ReviewWorkspaceView.qml`.

---

## 13. Testing & Verification Strategy

- **Schema Validation Tests:** Unit tests verifying that `PublicationStyleProfile` correctly validates margin formats, linestretch values, and documentclass choices.
- **Quarto Injection Tests:** Verify that staged documents generated with each of the 4 built-in profiles compile cleanly under XeLaTeX.
- **Bilingual Font Rendering Tests:** Verify that Persian/RTL text and Latin text render crisp vector glyphs under each profile.
- **AST Architecture Invariant Tests:** Verify that `PublicationStyleService` has zero imports of PySide6, Qt, or subprocesses.
