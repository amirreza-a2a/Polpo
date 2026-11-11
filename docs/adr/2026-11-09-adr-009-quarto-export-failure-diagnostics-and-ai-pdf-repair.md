# ADR-009: Quarto PDF Export Failure Diagnostics, Source Mapping, and AI-Assisted Candidate PDF Repair

- **Status:** Proposed (Awaiting Maintainer Approval)
- **Date:** 2026-11-09 (Revised: 2026-11-11)
- **Scope:** Quarto PDF Compilation Pipeline, Staging Failure Capture, Diagnostic Bundle Lifecycle, Multi-Tier Source Mapping, Error Classification, Candidate PDF Recompilation, Structural vs Visual PDF Inspection, Repair Scope Separation, RTL Typesetting Workaround Policy
- **Authoritative Baseline:** Commit `25bb95d198c34c035b5d66513166e06494d82cdb`
- **Related ADRs:** ADR-003 (Quarto PDF Export Integration), ADR-005 (Unified Quarto Diagram Pipeline), ADR-006 (AI LaTeX Repair), ADR-007 (Agent Orchestration & Tool Governance), ADR-008 (Working Copy & Patch Architecture), ADR-010 (Publication Style System)
- **Confirmed Requirements:** D-USER-01 (Unified Assistant Scope), D-USER-02 (Supervised Agent Autonomy), D-USER-03 (Document vs Rendering-System Repair)

---

## 1. Context

Polpo features an integrated publication-grade PDF export pipeline based on the external Quarto CLI and XeLaTeX ([ADR-003](docs/adr/2026-10-12-adr-003-quarto-pdf-export-integration.md)). Export execution takes place inside an ephemeral staging workspace managed by `StagingWorkspaceManager` ([`infrastructure/pdf/staging_workspace.py`](infrastructure/pdf/staging_workspace.py)) and orchestrated by `QuartoPdfExportService` ([`application/services/quarto_pdf_export_service.py`](application/services/quarto_pdf_export_service.py)).

During compilation, complex documents can fail due to Markdown formatting defects, broken LaTeX equations, incompatible front-matter metadata, TikZ diagram syntax errors, missing TeX packages, font mismatches, or layout engine collisions (such as the observed `Package soul Error: Reconstruction failed.` failure under Persian/RTL typesetting).

While ADR-006 ([`docs/adr/2026-11-07-adr-006-ai-assisted-latex-repair.md`](docs/adr/2026-11-07-adr-006-ai-assisted-latex-repair.md)) introduced isolated fragment repair for individual MathJax formulas and TikZ diagrams, full-document Quarto PDF export failures currently lack automated diagnostic preservation, reliable source location mapping, structural validation, and safe repair governance.

---

## 2. Problem Statement

1. **Ephemeral Workspace Purge on Failure:** When Quarto or XeLaTeX fails, `StagingWorkspaceManager` deterministically deletes the staging directory (`shutil.rmtree(staging_dir)`). The intermediate generated LaTeX (`document.tex`) and the detailed compiler logs are deleted immediately, preventing subsequent inspection or diagnosis.
2. **Generated TeX vs. Markdown Line Offset Disconnect:** XeLaTeX error logs report line coordinates corresponding strictly to `document.tex` (e.g. `l.384 ... \st{Strikethrough}`). Because `document.tex` includes Pandoc templates, preambles, and macro expansions, line 384 does not match line 384 of the user's Markdown. Naive line navigation jumps to completely wrong document locations.
3. **Truncated Diagnostics:** The existing `TexLogSanitizer` truncates compiler error logs to 6–8 lines, which often discards essential context (such as undefined command sequences or package stack traces).
4. **Scope Conflation & Unsafe LaTeX Injection (D-USER-03, Finding A):** Without clear scope classification, an AI repair system might attempt to fix a product-level template defect or package conflict (e.g. `soul` in RTL) by deleting legitimate content (e.g. removing strikethrough from the author's Markdown text) or by attempting to inject arbitrary raw LaTeX macros (`\newcommand{\st}[1]{\sout{#1}}`) into the document preamble, violating the security model.
5. **Inspection Coupling & Shaky Validation (Finding B, Finding H):** Mixing structural PDF validation with repair orchestration creates circular dependencies. Moreover, treating basic process exit codes (`returncode == 0`) or unrealistic 500ms guarantees as proof of visual correctness risks accepting blank or overflowing documents.

---

## 3. Architectural Forces & Constraints

1. **Repair Scope Separation (D-USER-03):** Document markup repairs, YAML metadata options, publication style configurations, and Polpo product defects must remain strictly isolated. A document task must never mutate the globally installed `polpo_preamble.tex`.
2. **Zero Arbitrary Macro Injection (Finding A, Finding F):** The AI assistant must NEVER generate unrestricted raw LaTeX macros or preamble code for injection into document headers. Global fixes belong in version-controlled templates; user-selectable style options must use strictly typed allowlisted tokens.
3. **Decoupled Inspection Contracts (Finding B):** The PDF inspection port (`IPdfInspectionEngine`) must be defined as an abstract application port before repair orchestration, ensuring an acyclic dependency graph.
4. **Data Minimization & Staging Lifecycle (Finding G):** The local diagnostic mapper requires intermediate `document.tex`, but the system must never transmit the full document to an external AI provider. Staging directories must still be cleaned up deterministically.
5. **Separation of Structural and Visual Inspection (Finding H):** PyMuPDF structural checks (fast object analysis) must be decoupled from optional raster preview generation.

---

## 4. Confirmed Requirements (Maintainer Settled)

- **D-USER-03 (Both Document and Rendering-System Repair):** PDF repair must cover both document-level failures (Markdown source, LaTeX fragments, math, diagrams, metadata) and rendering-system failures (Pandoc transforms, generated LaTeX, preamble conflicts, publication templates, fonts, RTL configuration). These scopes must remain separate. Document tasks must never modify installed product templates; product defects must be reported for the normal development workflow.

---

## 5. Persian RTL Strikethrough Investigation: `soul` vs. `ulem` (Finding A)

### 5.1 Real Toolchain Empirical Investigation
We evaluated the strikethrough failure mechanism using Polpo's real runtime toolchain:
- **Environment:** Ubuntu 24.04 LTS (x86_64, Linux 6.8)
- **TeX Engine:** XeTeX 3.141592653-2.6-0.999995 (TeX Live 2023/Debian, kpathsea 6.3.5, ICU 74.2, HarfBuzz 8.3.0)
- **Converter:** Pandoc 3.1.3 (compiled with Lua 5.4)
- **Quarto Baseline:** Quarto 1.9.38
- **Typesetting Setup:** `[bidi=default,provide=*]{babel}`, `\babelprovide[main,import]{persian}`, `\babelfont{rm}[Renderer=HarfBuzz,Script=Arabic]{Vazirmatn}`, `resources/templates/polpo_preamble.tex`
- **Installed Packages:** `soul.sty` (2023-06-14 v3.1, HO), `ulem.sty` (2019-11-18)

### 5.2 Pandoc 3.1.3 Template Behavior & Macro Ownership Collision
1. **Pandoc Default LaTeX Template (`pandoc -D latex`):**
   - Lines 293–303 contain the conditional package inclusion:
     ```latex
     $if(strikeout)$
     $-- also used for underline
     \ifLuaTeX
       \usepackage{luacolor}
       \usepackage[soul]{lua-ul}
     \else
       \usepackage{soul}
     \fi
     $endif$
     ```
   - Pandoc emits `\st{...}` for Markdown `~~...~~` and defines template variable `strikeout=true`.
   - Under XeLaTeX (`\else` branch), Pandoc unconditionally emits `\usepackage{soul}`.
   - Pandoc's template injects `$for(header-includes)$` later in the preamble (line 380).
2. **The Macro Collision Hazard (Finding D):**
   - When Pandoc loads `\usepackage{soul}`, `soul` defines `\st`.
   - If a preamble or `header-includes` contains `\newcommand{\st}[1]{\sout{#1}}`, XeLaTeX immediately halts with:
     ```text
     ! LaTeX Error: Command \st already defined.
                     Or name \end... illegal, see p.192 of the manual.
     ```
   - Therefore, a naive `\newcommand` is syntactically invalid. Any macro redefinition must use `\renewcommand{\st}[1]{\sout{#1}}` (or `\def\st{\sout}` / `\let\st\sout`), or must prevent Pandoc from emitting `\st` altogether via an AST filter.
3. **Failure Mechanism of `soul` under Multilingual Babel/HarfBuzz:**
   - The `soul` package analyzes its arguments by tokenizing input character-by-character and reconstructing hyphenation/syllables through internal box placement (`\SOUL@...`).
   - In pure English text without Babel/bidi, `soul` compiles cleanly.
   - Pure Persian text (`\st{متن فارسی}`) without language or font boundaries also compiles cleanly under `soul` v3.1.
   - However, `soul` crashes with `! Package soul Error: Reconstruction failed.` whenever:
     - An inline language boundary or direction switch occurs (e.g. `\st{متن English}` or `\st{\foreignlanguage{english}{text}}`).
     - Font changes occur inside strikethrough (e.g. `\st{\textsf{دیگر}}`).
     - Grouping or formatting macros exist inside (e.g. `\st{\textbf{پررنگ}}`).
     - Inline math occurs inside (`\st{$x^2$}`).
     - Moving arguments are used (headings `\section{\st{...}}`).
4. **Behavior of `ulem`:**
   - The `ulem` package provides `\sout{...}`, operating at the word/box level rather than reconstructing syllables. It tolerates language switches, font switches, and nested formatting cleanly.
   - **Mandatory Caveats of `ulem`:**
     - Must be loaded with `\usepackage[normalem]{ulem}` to prevent redefining `\emph` and `\em` to underline text.
     - Inline math inside `\sout{...}` must be placed inside `\mbox{$...$}`.
     - Headings require `\protect\sout` to survive moving arguments.

### 5.3 Twelve-Case Empirical Regression Matrix

| # | Test Scenario | Input LaTeX / QMD | `soul` v3.1 Result | `ulem` (2019) Result | Controlled Alternative (Lua Filter / Preamble) |
|---|---|---|---|---|---|
| 1 | English-only strikethrough (standard English document) | `\st{English text}` (ambient English, no Babel bidi) | ✅ Passes (rc=0, clean PDF) | ✅ Passes (rc=0) | `\st{English text}` / `\sout{English text}` |
| 2 | Persian-only strikethrough (ambient Persian) | `\st{متن فارسی}` (single language, no switch) | ✅ Passes (rc=0, clean PDF) | ✅ Passes (rc=0) | `\sout{متن فارسی}` |
| 3 | Mixed Persian & English text | `\st{متن فارسی English}` | ❌ Fails (`Reconstruction failed`) | ✅ Passes (rc=0) | `\sout{متن فارسی English}` |
| 4 | Nested braces | `\st{{متن داخل آکولاد}}` | ❌ Fails (`Reconstruction failed`) | ✅ Passes (rc=0) | `\sout{{متن داخل آکولاد}}` |
| 5 | Nested formatting macros | `\st{متن \textbf{پررنگ}}`, `\st{\emph{تأکید}}` | ❌ Fails (`Reconstruction failed`) | ✅ Passes (rc=0) | `\sout{متن \textbf{پررنگ}}` |
| 6 | Font switch inside | `\st{\textsf{فونت دیگر}}`, `\st{\texttt{کد}}` | ❌ Fails (`Reconstruction failed`) | ✅ Passes (rc=0) | `\sout{\textsf{فونت دیگر}}` |
| 7 | Babel language switches | `\st{\foreignlanguage{english}{English text}}` | ❌ Fails (`Reconstruction failed`) | ❌ Fails unboxed (`Extra }`); ✅ Passes with `\mbox` | `\sout{\mbox{\foreignlanguage{english}{English text}}}` |
| 8 | Direction changes | `\st{\foreignlanguage{english}{LTR} متن}` | ❌ Fails (`Reconstruction failed`) | ❌ Fails unboxed (`Extra }`); ✅ Passes with `\mbox` | `\sout{\mbox{...}}` handles LTR/RTL box boundary |
| 9 | Headings & moving arguments | `\section{\st{عنوان سرفصل}}` | ❌ Fails in moving argument | ✅ Passes with `\protect\sout` | Protected macro `\protect\sout` or AST translation |
| 10 | List items | `\begin{itemize}\item \st{مورد فهرست}\end{itemize}` | ✅ Passes pure Persian; ❌ Fails if mixed/nested | ✅ Passes (rc=0) | `\sout{مورد فهرست}` |
| 11 | Inline math inside strikeout | `\st{$x^2$}` vs `\st{قبل} $x^2$ \st{بعد}` | ❌ Fails (`\SOUL@addmath has extra }`) | ❌ Fails unboxed (`Missing $`); ✅ Passes boxed | Boxed math: `\sout{\mbox{$x^2$}}` or uncrossed math |
| 12 | Quarto-generated PDF output | `~~متن فارسی نمونه~~` / `~~mixed English~~` | ✅ Passes pure Persian; ❌ Fails mixed English (`Reconstruction failed`) | ✅ Passes with `ulem` override | Clean Quarto XeLaTeX PDF |

*Note on toolchain versions:* Ubuntu 24.04 LTS, TeX Live 2023, XeTeX 3.141592653-2.6-0.999995, Pandoc 3.1.3, Quarto 1.9.38, Babel Persian 3.90, `soul` v3.1 (2023-06-14), `ulem` (2019-11-18).
*Empirical note on English-only and Persian-only behavior:* `soul` v3.1 compiles cleanly for pure English and pure Persian without language or font transitions. The failure (`Package soul Error: Reconstruction failed`) reproduces specifically when language boundaries (mixed Persian-English), direction changes, font switches, nested braces, or inline math cross the strikethrough boundary under Babel bidi.
*Empirical note on `ulem` behavior:* While `ulem` handles mixed script and nested formatting cleanly, unboxed `\foreignlanguage` grouping triggers `! Extra }, or forgotten \endgroup` and unboxed math triggers `! Missing $ inserted`, requiring explicit `\mbox{...}` protection (automatically provided in Option B via AST traversal).

### 5.4 Proposed Decision D-PROP-08: Persian Strikethrough Engine Alternatives
Status: `PROPOSED — AWAITING EXPLICIT MAINTAINER APPROVAL`

We evaluate two controlled architectural alternatives without arbitrary AI macro injection:

- **Option A: Controlled Static ULEM Engine (`resources/templates/polpo_preamble.tex`)**
  - Polpo's static preamble loads `\usepackage[normalem]{ulem}`.
  - To safely handle Pandoc's template emission of `\usepackage{soul}` and `\st`, the preamble defines:
    ```latex
    \AtBeginDocument{%
      \ifdef{\st}{%
        \renewcommand{\st}[1]{\sout{#1}}%
      }{%
        \newcommand{\st}[1]{\sout{#1}}%
      }%
    }
    ```
  - Pros: Clean, purely template-owned, zero external filter files, works across all Quarto invocations.
  - Cons: Requires `\AtBeginDocument` or `\renewcommand` to prevent macro collision with `soul`.
- **Option B: Controlled Pandoc Lua AST Filter (`filters/strikethrough_ulem.lua`)**
  - A small, deterministic Pandoc Lua filter intercepts `Strikeout` AST elements before Pandoc's LaTeX writer.
  - Replaces `pandoc.Strikeout(inlines)` with raw LaTeX `\sout{...}`, preventing Pandoc from setting `strikeout=true` and preventing Pandoc from loading `soul` or emitting `\st`.
  - Pros: Completely eliminates `soul` package inclusion and macro collisions; can automatically box inline math into `\mbox{$...$}` during AST traversal.
  - Cons: Requires passing `--lua-filter` in Quarto render options and managing the filter file lifecycle.

**Invariant:** Under both options, the AI Agent CANNOT generate or inject raw TeX macros. The Agent may only recommend setting the typed token `strikethrough_engine: "ulem"` in a `PublicationStyleProfile` or flag a product defect report. Neither option is implemented until the maintainer explicitly approves D-PROP-08.

---

## 6. Proposed Decision

We propose establishing the **Quarto PDF Diagnostic and AI-Assisted Repair System** comprising:
1. **Diagnostic Bundle Extraction:** Enhancing `QuartoPdfAdapter` and `StagingWorkspaceManager` to capture a structured `DiagnosticBundle` before workspace purge.
2. **Decoupled PDF Inspection Port (Finding B):** Defining `IPdfInspectionEngine` and DTOs in `application/ports/pdf_validator_port.py` prior to repair orchestration.
3. **Five-Tier Staging & Diagnostic Data Lifecycle (Finding G):** Keeping intermediate `document.tex` locally for mapping while enforcing data minimization for external AI payloads.
4. **Multi-Tier Source Location Mapper:** Mapping generated TeX errors to canonical Markdown coordinates with explicit confidence scoring (`EXACT_TOKEN`, `SECTION_ANCHOR`, `CONTEXTUAL_MATCH`, `UNKNOWN`).
5. **Deterministic First-Stage Error Classifier:** Categorizing failures into discrete scopes without model guesswork.
6. **Separated Structural & Visual PDF Inspection (Finding H):** Fast PyMuPDF object-level checks separated from optional raster preview generation.

---

## 7. Detailed Design

### 7.1 Architecture & Component Boundaries

```text
Application Layer (application/)
 ├── dto/pdf_diagnostic_dto.py            ──> DiagnosticBundle, SourceMappingResult, ErrorCategory
 ├── dto/pdf_inspection_dto.py            ──> PdfInspectionRequest, PdfInspectionResult, PdfVisualAnomaly
 ├── ports/pdf_validator_port.py          ──> IPdfInspectionEngine (Foundation Contract)
 ├── services/pdf_diagnostic_service.py   ──> Error classification, multi-tier source mapping
 └── services/pdf_repair_service.py       ──> AI repair orchestration (depends on IPdfInspectionEngine port)
Infrastructure Layer (infrastructure/)
 ├── pdf/quarto_adapter.py                ──> Captures DiagnosticBundle before purging staging dir
 ├── pdf/staging_workspace.py             ──> Exposes intermediate document.tex extraction hook
 ├── pdf/tex_log_sanitizer.py             ──> Extended sanitizer (100 lines / 10KB bounded excerpt)
 └── pdf/mupdf_validator.py               ──> PyMuPdfValidator (implements IPdfInspectionEngine)
```

### 7.2 Foundation PDF Inspection Contract (Finding B, Finding E)

Defined in `application/ports/pdf_validator_port.py` and `application/dto/pdf_inspection_dto.py`:

```python
class AnomalySeverity(Enum):
    HARD_FAILURE = "HARD_FAILURE"        # Corrupt file, 0 pages, rendering crash
    WARNING = "WARNING"                  # Orphan blank page, dimensions variance, low text density, missing expected diagram
    INFO = "INFO"                        # Vector drawing metrics, page count delta

@dataclass(frozen=True)
class PdfVisualAnomaly:
    anomaly_type: str                    # "CORRUPT_STRUCTURE", "BLANK_PAGE", "OVERFLOW_BOUNDS", "MISSING_DIAGRAM"
    severity: AnomalySeverity
    page_number: int
    message: str

@dataclass(frozen=True)
class PdfPageSummary:
    total_pages: int
    page_dimensions: Tuple[float, float] # (width_pt, height_pt)
    text_character_count: int
    vector_drawing_count: int

@dataclass(frozen=True)
class PdfValidationPolicy:
    max_pages: int = 500
    allowed_page_variance: float = 0.20
    expected_dimensions: Optional[Tuple[float, float]] = None
    require_vector_drawings: bool = False
    expected_diagram_pages: Tuple[int, ...] = ()

@dataclass(frozen=True)
class PdfInspectionRequest:
    pdf_path: Path
    expected_text_anchors: Tuple[str, ...] = ()
    policy: PdfValidationPolicy = field(default_factory=PdfValidationPolicy)
    baseline_pdf_path: Optional[Path] = None
    expected_page_count_range: Optional[Tuple[int, int]] = None
    generate_raster_previews: bool = False

@dataclass(frozen=True)
class PdfInspectionResult:
    is_valid: bool
    page_summary: PdfPageSummary
    anomalies: Tuple[PdfVisualAnomaly, ...]
    raster_previews: Tuple[bytes, ...]   # 150 DPI PNG byte buffers if requested

class IPdfInspectionEngine(ABC):
    @abstractmethod
    def inspect_pdf(self, request: PdfInspectionRequest) -> PdfInspectionResult:
        """Inspects candidate PDF structure, text preservation, and visual integrity."""
        pass
```

`PdfRepairService` depends strictly on `IPdfInspectionEngine`. In unit tests, it is tested with mock inspection engines. `PyMuPdfValidator` implements this contract in `infrastructure/pdf/mupdf_validator.py`.

### 7.3 Diagnostic Lifecycle & Data Minimization (Finding G)

To balance local diagnostic needs with privacy, five distinct data representations are separated:

1. **Temporary Staging Workspace:** Ephemeral directory (`.pdf_staging/<job_id>_<token>/`). Exists during compilation; deterministically removed via `finally: shutil.rmtree()` immediately after diagnostic capture.
2. **Sanitized Failure Bundle (`DiagnosticBundle`):** Captured locally in memory/ephemeral store (max 5 bundles per job, max 10MB total). Bounded to 100 lines / 10KB. Absolute host paths, usernames, and staging hashes are redacted.
3. **TeX Source Mapping Input:** The intermediate `document.tex` file, inspected strictly locally within the staging hook by `PdfDiagnosticService` to compute line mappings. **Never transmitted externally.**
4. **AI Transmission Payload:** Minimal excerpt disclosed to the author in `LaTeXRepairConsentDialog`: only the failing TeX snippet (10 lines), mapped Markdown line, and sanitized compiler message.
5. **Persistent Task Journal Metadata:** Only non-sensitive audit metadata (opaque step correlation IDs, error category, timestamps) recorded in SQLite under migration 006 (plain content hashes strictly prohibited).

### 7.4 Multi-Tier Source Location Mapping & Fail-Closed Classification (Finding C)

The classification contract explicitly decouples four independent outputs to eliminate conflation between compiler errors and document edits:
1. **`ErrorCategory`:** What kind of failure occurred (`PACKAGE_MISSING`, `FONT_MISSING`, `TEMPLATE_CONFLICT`, `SOURCE_SYNTAX`, `PANDOC_TRANSFORM`, `PRODUCT_DEFECT`, `UNKNOWN`).
2. **`SourceMappingEvidence`:** Where the error originated and how confident the location match is (`EXACT_TOKEN` = 1.0, `CONTEXTUAL_MATCH` = 0.7, `SECTION_ANCHOR` = 0.5, `UNKNOWN` = 0.0).
3. **`RemediationScope`:** Which architectural subsystem is the responsible target for repair (`DOCUMENT_MARKUP`, `PUBLICATION_STYLE`, `PRODUCT_TEMPLATE`, `ENVIRONMENT_DEPENDENCY`, or `None`).
4. **`RemediationRiskTier`:** The governance level and user approval required (`READ_ONLY`, `ISOLATED_MUTATION`, `EXTERNAL_TRANSMISSION`, `CONSEQUENTIAL_SYSTEM`, `CANONICAL_ADOPTION`).

#### Source Mapping Algorithm:
Pandoc transforms Markdown into LaTeX non-linearly. The system uses a 4-tier mapping algorithm to locate the source coordinate:

```mermaid
flowchart TD
    Start[TeX Log Line l.384] --> ExtractTeX[Extract 5 lines around l.384 in document.tex]
    ExtractTeX --> T1{Tier 1: Exact Unique Token Search}

    T1 -->|Unique distinctive token found| M1[Set mapped_line = token_line<br/>Confidence = EXACT_TOKEN (1.0)]
    T1 -->|Ambiguous or transformed| T2{Tier 2: Contextual Match}

    T2 -->|Phrase match found in Markdown| M2[Set mapped_line = phrase_line<br/>Confidence = CONTEXTUAL_MATCH (0.7)]
    T2 -->|Not found| T3{Tier 3: Section Anchor}

    T3 -->|Nearest \\section matches heading| M3[Set mapped_line = header_line<br/>Confidence = SECTION_ANCHOR (0.5)]
    T3 -->|Boilerplate / Preamble error| T4[Tier 4: Explicit Unmapped Fallback]

    T4 --> M4[Set mapped_line = None<br/>Confidence = UNKNOWN (0.0)<br/>UI shows TeX snippet directly]
```

- **`EXACT_TOKEN` (1.0):** Matches distinctive macro tokens (e.g. `\includegraphics{crop_abc123.png}`, `\begin{tikzpicture}`, `\label{eq:5}`). Authorizes targeted document patch generation.
- **`CONTEXTUAL_MATCH` (0.7):** Matches a distinctive phrase surrounding the error, handling common Pandoc transforms (`~~text~~` $\to$ `\st{text}`). Authorizes document patch generation after surrounding context verification.
- **`SECTION_ANCHOR` (0.5):** Scans backward in `document.tex` to find the nearest `\section{...}` heading and maps to the Markdown `# ...` header. **Insufficient for automatic exact patch generation;** surfaces heading coordinate for human inspection.
- **`UNKNOWN` (0.0): Fail-Closed Safety Invariant:** When an error cannot be mapped to a source coordinate with confidence, or when the error pattern is unclassified:
  - `recommended_scope = None`
  - `classification_confidence = 0.0`
  - `requires_human_inspection = True`
  - `automatic_patch_allowed = False`
  - **The Agent NEVER automatically produces a Markdown patch or modifies documents when confidence is `UNKNOWN`.** The UI displays diagnostic logs directly and asks for human direction.

#### Disentangling Error Categories from Source Markup Mutation (Finding C):
1. **`PANDOC_TRANSFORM` is NOT automatically `DOCUMENT_MARKUP`:**
   - A Pandoc transformation error can originate from user Markdown syntax, an application Lua filter error, Quarto front-matter metadata, an unsupported YAML option, a Pandoc writer bug, or an external toolchain issue.
   - `PANDOC_TRANSFORM` maps to `DOCUMENT_MARKUP` **only if** source mapping establishes `EXACT_TOKEN` or `CONTEXTUAL_MATCH` pointing to an identifiable Markdown construct.
   - If the error is caused by template interactions, filters, or YAML metadata, it routes to `PRODUCT_TEMPLATE`, `PUBLICATION_STYLE`, or `ENVIRONMENT_DEPENDENCY`.
   - If ambiguous or unmapped, it yields `recommended_scope = None` and `automatic_patch_allowed = False`.
2. **LaTeX Syntax Errors Require Confident Source Evidence:**
   - Errors such as `! Undefined control sequence`, `! Missing $ inserted`, or `! Extra }` do not automatically prove malformed source Markdown. They frequently stem from macro package collisions, ambient RTL bidi boundary issues, or preamble macro definitions.
   - Unless `PdfDiagnosticService` maps the error via `EXACT_TOKEN` or `CONTEXTUAL_MATCH` directly to a math formula or diagram in the author's Markdown, `recommended_scope` remains `None`, preventing automatic document modification.
3. **`soul` Reconstruction Errors (`TEMPLATE_CONFLICT`):**
   - Matches `! Package soul Error: Reconstruction failed.`.
   - Originates from `soul`'s syllable-reconstruction engine colliding with Babel Persian bidi font/direction boundaries.
   - Under no circumstances does this error trigger a source-text mutation of user Markdown.
   - It routes to `PUBLICATION_STYLE` (proposing typed token `strikethrough_engine = "ulem"` subject to explicit maintainer approval of D-PROP-08) or flags a `PRODUCT_TEMPLATE` defect report.
4. **`PACKAGE_MISSING` vs YAML Metadata:**
   - Matches `! LaTeX Error: File '...sty' not found`.
   - Failure location: TeX compilation environment / preamble.
   - Remediation target: `ENVIRONMENT_DEPENDENCY` (prompts user to install the missing package via system package manager or TinyTeX).
   - Policy: The application NEVER alters document YAML or Markdown to fake package presence or delete package requirements.
5. **`FONT_MISSING` vs Profile Silent Mutation:**
   - Matches `fontspec error: "cannot-find-font"`.
   - Remediation target: `PUBLICATION_STYLE` with `suggested_action = "fallback_standard_font"`.
   - Policy: The application NEVER silently mutates the user's publication style profile without explicit human confirmation.

### 7.5 Four-Category PDF Inspection vs. Visual and Semantic Quality (Finding E, Finding F, Finding H)

To eliminate false senses of correctness, PDF validation explicitly defines four distinct validation categories and separates structural verification from visual quality and semantic correctness:

1. **Category 1: Structural Validation (Deterministic PyMuPDF Object Checks):**
   - Header integrity (`%PDF-`), valid xref table via `fitz.open()`.
   - Page count within variance of baseline ($\pm 20\%$) and within `max_pages` bound (default 500).
   - Page dimensions match target format (A4: $595 \times 842 \pm 2$ pt).
   - Text stream presence: extracted text character count > 0.
   - **Vector Drawing Interaction & Severity:**
     - `require_vector_drawings = False` by default. Text-only documents pass structural validation cleanly with zero vector drawings.
     - When `expected_diagram_pages` is non-empty (e.g. `(3, 5)`), `PyMuPdfValidator` checks `len(page.get_drawings()) > 0` strictly on those specified pages.
     - Missing vector drawings on an expected diagram page produce a `WARNING`, NOT an automatic `HARD_FAILURE`.
     - *Distinction:* `page.get_drawings()` verifies the presence of vector path primitives. It does **NOT** prove semantic diagram correctness, label legibility, or TikZ rendering fidelity.
   - Blank page detection: pages with $< 10$ characters of text and zero vector drawings are flagged as `WARNING`.
2. **Category 2: Source-Preservation Checks (Text Anchors):**
   - Verifies that bounded `expected_text_anchors` appear in the extracted text stream.
   - **Text Anchor Privacy & Bounds:**
     - Maximum 10 anchors per inspection request.
     - Maximum 128 characters per anchor; maximum 2 KB aggregate byte size.
     - Anchors reside strictly in short-lived local in-memory inspection requests.
     - Anchors are **never persisted** in SQLite database tables or task journals.
     - Anchors are **never transmitted** to external cloud AI providers automatically; any external transmission requires explicit user payload disclosure and consent.
3. **Category 3: Visual Anomaly Detection (Bounded Raster Previews):**
   - Optional 150 DPI PNG raster preview generation for key sampled pages:
     - Sampled pages: first 3 pages + last page + any flagged anomaly pages; **maximum 5 pages total**.
     - Resolution: 150 DPI; maximum pixel dimensions $1240 \times 1754$ px (A4 @ 150 DPI).
     - Memory bounds: maximum 10 MB per page image; maximum **50 MB aggregate buffer cap**.
     - Execution timeout: **5.0 seconds** with cooperative thread cancellation check.
     - Buffer lifetime: ephemeral byte buffers allocated in RAM; dropped immediately following UI consumption.
4. **Category 4: Semantic Correctness (Explicitly Out-of-Scope / Unsupported):**
   - Structural and raster inspection CANNOT prove mathematical correctness, semantic fidelity, absence of subtle layout overflow, Persian typography aesthetics, or translation accuracy.
   - **`is_valid = True` proves ONLY structural well-formedness and readability.** It is NEVER represented as proof of visual beauty or semantic correctness.
5. **Validation Severity Semantics:**
   - `HARD_FAILURE`: Corrupt PDF structure, unopenable file, zero pages rendered. Results in `is_valid = False`.
   - `WARNING`: Low text density, unexpected blank page, missing vector drawings on expected diagram page, dimension variance. Does NOT cause automatic validation rejection unless configured by explicit policy; surfaces in anomaly list for user review.
   - `INFO`: Page dimensions, text character counts, drawing primitive metrics.
6. **Performance Budget:**
   - Structural checks: $< 1.5$ seconds for documents under 50 pages on reference hardware (Ubuntu 24.04, Core i7/Ryzen 7). This is an empirical benchmark target, not an absolute real-time SLA.

---

## 8. Dependency & Data Flow

```text
[PDF Export Fails in QuartoPdfAdapter]
                 │
                 ▼
[Extract DiagnosticBundle before staging purge]
 ├── Capture sanitized stdout/stderr + *.log
 ├── Read intermediate document.tex locally for mapping
 └── Run multi-tier source mapper
                 │
                 ▼
[Store in Ephemeral Diagnostic Store] (Job-scoped LRU, max 5)
                 │
                 ▼
[Emit Qt Signal: pdfCompilationFailedWithDiagnostic(bundle)]
                 │
                 ▼
[AgentController: User requests AI PDF Repair]
                 │
                 ▼
[Pre-Transmission Consent Dialog] (Discloses minimal excerpt)
                 │
                 ▼
[AI Provider Generates Candidate Patch] (Document or Typed Style scope)
                 │
                 ▼
[AgentWorkingCopyService: Apply to Scratchpad]
                 │
                 ▼
[QuartoPdfAdapter: Recompile Candidate in Staging]
                 │
                 ▼
[PyMuPdfValidator: Inspect Candidate PDF (IPdfInspectionEngine)]
                 │
                 ▼
[Present Side-by-Side Candidate Preview & Diff in QML]
```

---

## 9. Consequences

### Positive:
- **Actionable Diagnostics:** Preserves intermediate TeX evidence before staging purge without disk bloat.
- **Evidence-Based RTL Fix:** Factual understanding of `soul` vs `ulem` under Babel/HarfBuzz replaces unsupported claims.
- **Strict Macro Security:** Zero arbitrary LaTeX injection into preambles; styling managed via typed tokens.
- **Decoupled Architecture:** `IPdfInspectionEngine` port allows independent testing and eliminates circular ticket dependencies.
- **Realistic Validation:** Structural and visual inspection prevents false positives and false senses of correctness.

### Negative / Trade-offs:
- **Recompilation Overhead:** Validating candidate PDFs through full Quarto and XeLaTeX requires 2–5 seconds per candidate.
- **Sampling Limits:** Visual raster previews are sampled across key pages rather than full 500-page rasterization to respect memory budgets.

---

## 10. Testing & Verification Strategy

- **RTL Strikethrough Regression Suite:** Automated tests verifying the 10 scenarios in the regression matrix under XeLaTeX and Babel.
- **Diagnostic Capture Tests:** Verify that `DiagnosticBundle` extracts failing snippets and that staging directories are safely purged in `finally:`.
- **Source Mapper Tests:** Test multi-tier mapping against sample `document.tex` and Markdown pairs, verifying confidence scoring (`EXACT_TOKEN`, `SECTION_ANCHOR`, `UNKNOWN`).
- **Inspection Engine Unit Tests:** Verify `PyMuPdfValidator` detects blank pages, corrupt headers, dimension mismatches, and vector drawings on test fixtures.
