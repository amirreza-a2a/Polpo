# TICK-ADE-10: Deterministic Error Classifier, Scope Separator & Foundation PDF Inspection Port

- **Ticket ID:** `TICK-ADE-10`
- **Title:** Deterministic Error Classifier, Scope Separator & Foundation PDF Inspection Port
- **Milestone:** Milestone C (Quarto Diagnostic Infrastructure)
- **Priority:** P1
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Implement the deterministic first-stage error classifier in `application/services/pdf_diagnostic_service.py` to enforce D-USER-03 scope separation, and define the foundational `IPdfInspectionEngine` port and DTO contracts in `application/ports/pdf_validator_port.py` and `application/dto/pdf_inspection_dto.py` before Milestone D.
- **Explicit Non-Goals:**
  - No AI model-based classification for the first stage (classification must be deterministic, reproducible, and fast).
  - No mutating global product templates during user document tasks.
  - No implementing the PyMuPDF inspection engine here (contract definition only; implementation resides in `TICK-ADE-12`, Finding B).
- **Prerequisites:** `TICK-ADE-08` and `TICK-ADE-09` completed; ADR-009 approved.
- **Dependencies (Blocked By):** `TICK-ADE-08`, `TICK-ADE-09`.
- **Unblocks:** `TICK-ADE-11`, `TICK-ADE-12`.

---

## 1. Existing Source-Code Integration Points

- `infrastructure/pdf/tex_log_sanitizer.py`: Regex pattern matching for missing packages and fonts.
- `resources/templates/polpo_preamble.tex`: Known preamble macro definitions.
- `application/ports/pdf_export_port.py`: Precedent for PDF export interfaces.

---

## 2. Proposed Application Interfaces & DTOs

### 2.1 Error Classification Contracts
```python
class RepairScope(Enum):
    DOCUMENT_MARKUP = "DOCUMENT_MARKUP"        # Markdown syntax, LaTeX math, TikZ diagrams
    YAML_METADATA = "YAML_METADATA"            # Front-matter options (geometry, lang)
    PUBLICATION_STYLE = "PUBLICATION_STYLE"    # Typed PublicationStyleProfile token adjustment
    PRODUCT_DEFECT_REPORT = "PRODUCT_DEFECT_REPORT" # Polpo template or engine issue
    ENVIRONMENT_DEPENDENCY = "ENVIRONMENT_DEPENDENCY" # Missing TeX package / system font / binary
    UNRESOLVED_DIAGNOSTIC = "UNRESOLVED_DIAGNOSTIC"   # Ambiguous or conflicting diagnostic evidence

@dataclass(frozen=True)
class ErrorClassificationResult:
    category: ErrorCategory
    recommended_scope: Optional[RepairScope]   # None when UNKNOWN or unresolved!
    classification_confidence: float          # 0.0 when UNKNOWN
    requires_human_inspection: bool            # True when UNKNOWN
    automatic_patch_allowed: bool              # False when UNKNOWN
    suggested_action: str
    target_identifier: Optional[str] = None    # Package name, font name, or profile key
    explanation: str = ""
```

### 2.2 Foundation PDF Inspection Port Contracts (Finding B, Finding E)
```python
# Defined in application/ports/pdf_validator_port.py and application/dto/pdf_inspection_dto.py

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
    page_dimensions: Tuple[float, float]
    text_character_count: int
    vector_drawing_count: int

@dataclass(frozen=True)
class PdfValidationPolicy:
    max_pages: int = 500
    allowed_page_variance: float = 0.20
    expected_dimensions: Optional[Tuple[float, float]] = None
    require_vector_drawings: bool = False      # Default False: text-only PDFs pass cleanly
    expected_diagram_pages: Tuple[int, ...] = ()

@dataclass(frozen=True)
class PdfInspectionRequest:
    pdf_path: Path
    expected_text_anchors: Tuple[str, ...] = () # Minimized text anchors (max 10, max 128 chars each, max 2 KB total)
    policy: PdfValidationPolicy = field(default_factory=PdfValidationPolicy)
    baseline_pdf_path: Optional[Path] = None
    expected_page_count_range: Optional[Tuple[int, int]] = None
    generate_raster_previews: bool = False

@dataclass(frozen=True)
class PdfInspectionResult:
    is_valid: bool
    page_summary: PdfPageSummary
    anomalies: Tuple[PdfVisualAnomaly, ...]
    raster_previews: Tuple[bytes, ...]         # 150 DPI PNG byte buffers if requested (max 5 pages, 50 MB cap)

class IPdfInspectionEngine(ABC):
    @abstractmethod
    def inspect_pdf(self, request: PdfInspectionRequest) -> PdfInspectionResult:
        """Inspects candidate PDF structure, text preservation, and visual integrity."""
        pass
```

---

## 3. Detailed Implementation Requirements

1. **Four-Output Decoupled Classifier Architecture (Finding C):**
   The classification service decouples four independent dimensions:
   - `error_category`: `PACKAGE_MISSING`, `FONT_MISSING`, `TEMPLATE_CONFLICT`, `SOURCE_SYNTAX`, `PANDOC_TRANSFORM`, `PRODUCT_DEFECT`, `UNKNOWN`.
   - `source_mapping_confidence`: `EXACT_TOKEN` (1.0), `CONTEXTUAL_MATCH` (0.7), `SECTION_ANCHOR` (0.5), `UNKNOWN` (0.0).
   - `recommended_scope`: `DOCUMENT_MARKUP`, `PUBLICATION_STYLE`, `PRODUCT_TEMPLATE`, `ENVIRONMENT_DEPENDENCY`, or `None` (unresolved).
   - `risk_tier`: `READ_ONLY`, `ISOLATED_MUTATION`, `EXTERNAL_TRANSMISSION`, `CONSEQUENTIAL_SYSTEM`, `CANONICAL_ADOPTION`.

2. **Deterministic Classifier Rules & Source-Evidence Governance (`PdfDiagnosticService.classify_error`):**
   - `PACKAGE_MISSING`: Matches `! LaTeX Error: File '...sty' not found`. Returns `recommended_scope = ENVIRONMENT_DEPENDENCY` with failure location in TeX compilation environment/preamble. Action: Prompt user to install package via TinyTeX or system package manager. Prohibits faking or altering front-matter YAML.
   - `FONT_MISSING`: Matches `fontspec error: "cannot-find-font"`. Returns `recommended_scope = PUBLICATION_STYLE` with `suggested_action = "fallback_standard_font"`. Never silently mutates the profile.
   - `TEMPLATE_CONFLICT` (Finding A, Finding D): Matches `Package soul Error: Reconstruction failed.`. Returns `recommended_scope = PUBLICATION_STYLE` proposing typed token `strikethrough_engine = "ulem"` (subject to explicit maintainer approval of D-PROP-08) or flags `PRODUCT_DEFECT_REPORT`. Under no circumstances does it recommend modifying user Markdown source.
   - `SOURCE_SYNTAX`: Matches `! Undefined control sequence`, `! Missing $ inserted`, `! Extra }`. Returns `recommended_scope = DOCUMENT_MARKUP` **only if** source mapping establishes `EXACT_TOKEN` or `CONTEXTUAL_MATCH` confidence. If source mapping is ambiguous or unmapped (`SECTION_ANCHOR` or `UNKNOWN`), returns `recommended_scope = None` and `automatic_patch_allowed = False`.
   - `PANDOC_TRANSFORM` (Finding C): Matches `pandoc: ...` errors. Evaluates source mapping evidence: returns `recommended_scope = DOCUMENT_MARKUP` only when confident Markdown syntax coordinates are proven (`EXACT_TOKEN` or `CONTEXTUAL_MATCH`). If the failure originates from writer defects, YAML syntax, or template interactions, routes to `PRODUCT_TEMPLATE`, `PUBLICATION_STYLE`, or `ENVIRONMENT_DEPENDENCY`. If unmapped, returns `recommended_scope = None`.
   - `PRODUCT_DEFECT`: Matches errors inside `polpo_preamble.tex` primitives. Returns `recommended_scope = PRODUCT_DEFECT_REPORT`.
   - `UNKNOWN` (Finding C): Matches unclassified or ambiguous compiler output. Returns:
     ```python
     recommended_scope = None
     classification_confidence = 0.0
     requires_human_inspection = True
     automatic_patch_allowed = False
     suggested_action = "human_inspection_required"
     ```
     **Invariant:** An unknown error NEVER produces an automatic Markdown patch.

3. **Foundation Inspection Contract Ownership (Finding B, Finding E):**
   - Places `IPdfInspectionEngine`, `PdfInspectionRequest`, and `PdfInspectionResult` in `application/ports/pdf_validator_port.py` and `application/dto/pdf_inspection_dto.py`.
   - `require_vector_drawings = False` by default: text-only documents are structurally valid without drawings. Drawings are verified only when `expected_diagram_pages` is non-empty.
   - `expected_diagram_pages` missing vector drawings produce `WARNING` (severity: `WARNING`), never automatic hard failure.
   - `expected_text_anchors` is bounded (max 10 anchors, max 128 chars each, max 2 KB total); short-lived local memory only; never persisted in SQLite; never sent to AI automatically.
   - Raster previews: max 5 pages, 150 DPI, 50 MB buffer cap, 5.0s timeout.
   - Decouples repair orchestration (`TICK-ADE-11`) from PyMuPDF implementation (`TICK-ADE-12`).

---

## 4. Acceptance Criteria

- [ ] Missing `.sty` errors classified as `PACKAGE_MISSING` and routed to `ENVIRONMENT_DEPENDENCY` (not `YAML_METADATA`).
- [ ] Missing font errors classified as `FONT_MISSING` and routed to `PUBLICATION_STYLE`.
- [ ] `Package soul Error: Reconstruction failed.` classified as `TEMPLATE_CONFLICT` routing to `PUBLICATION_STYLE` (proposing proposal token `strikethrough_engine = "ulem"`) or `PRODUCT_DEFECT_REPORT`; never recommends Markdown source modification.
- [ ] `PANDOC_TRANSFORM` errors do NOT default to `DOCUMENT_MARKUP`; require strong source mapping evidence (`EXACT_TOKEN` or `CONTEXTUAL_MATCH`), failing closed to `recommended_scope = None` on ambiguous mapping.
- [ ] LaTeX syntax errors (`Undefined control sequence`, `Missing $ inserted`, `Extra }`) without confident source mapping yield `recommended_scope = None` and `automatic_patch_allowed = False`.
- [ ] Unknown or unmatched errors produce `recommended_scope = None`, `confidence = 0.0`, `requires_human_inspection = True`, and `automatic_patch_allowed = False`.
- [ ] `IPdfInspectionEngine` default `require_vector_drawings = False` ensures text-only PDFs pass structural validation.
- [ ] `PdfInspectionRequest` accepts bounded `expected_text_anchors: Tuple[str, ...]` and does not require or accept full `source_markdown`.
- [ ] Preamble macro definition errors classified as `PRODUCT_DEFECT_REPORT`.
- [ ] `IPdfInspectionEngine` port and DTO contracts are fully defined and importable in `application/ports/`.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_error_classifier.py`:
  - `test_categorize_unknown_error_produces_none_scope_and_zero_confidence()`: Verifies unclassified compiler output produces `recommended_scope = None` and `automatic_patch_allowed = False`.
  - `test_categorize_missing_package_routes_to_environment_dependency()`: Verifies `.sty` missing routes to `ENVIRONMENT_DEPENDENCY` and never suggests modifying YAML.
  - `test_categorize_soul_reconstruction_failure()`: Mandatory verification that `soul` reconstruction error routes to `PUBLICATION_STYLE` / `PRODUCT_DEFECT` and never mutates document markup.
  - `test_categorize_missing_fonts()`: Verifies fontspec error matching routes to `PUBLICATION_STYLE` and never silently alters profile.
  - `test_categorize_source_syntax_errors_requires_evidence()`: Verifies broken formula classification routes to `DOCUMENT_MARKUP` only with `EXACT_TOKEN` / `CONTEXTUAL_MATCH`, and yields `recommended_scope = None` on unmapped syntax errors.
  - `test_pandoc_transform_error_disentanglement()`: Verifies Pandoc transform errors do not default to `DOCUMENT_MARKUP` without verified source mapping evidence.
  - `test_scope_separation_enforcement()`: Confirms product defects do not recommend document edits.
  - `test_conflicting_classification_evidence()`: Verifies ambiguous diagnostics return `recommended_scope = None`.

### 5.2 Negative & Security Tests
- `test_prevent_classifier_redos()`: Asserts regex execution completes in $< 5$ms on large compiler logs.

### 5.3 Concurrency & Persistence Considerations
- Stateless, pure functional classification service.

---

## 6. Error and Recovery Behavior

- Unmatched or ambiguous errors default strictly to `ErrorCategory.UNKNOWN` with `recommended_scope = None`, `classification_confidence = 0.0`, `requires_human_inspection = True`, and `automatic_patch_allowed = False`. Under no circumstances may an unknown error trigger automatic document modifications.

---

## 7. Observability & Audit Requirements

- Returns classification category and target identifier in `ErrorClassificationResult`.

---

## 8. Documentation Updates

- Document error categories and foundation inspection contract in ADR-009.

---

## 9. Required Project Skills

- `codebase-design`: Clean interface definition for `IPdfInspectionEngine`.
- `tdd`: Test-first implementation of regex classifiers.

---

## 10. Verification Commands

```bash
pytest tests/unit/test_error_classifier.py -v
python3 -c "from application.ports.pdf_validator_port import IPdfInspectionEngine; print('Inspection port ready')"
```

---

## 11. Definition of Done

- All unit tests pass with 100% coverage.
- Deterministic error classifier and `IPdfInspectionEngine` port fully verified.
