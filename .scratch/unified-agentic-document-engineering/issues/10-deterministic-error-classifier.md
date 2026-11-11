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

### 2.2 Foundation PDF Inspection Port Contracts (Finding B, Finding F)
```python
# Defined in application/ports/pdf_validator_port.py and application/dto/pdf_inspection_dto.py

class AnomalySeverity(Enum):
    HARD_FAILURE = "HARD_FAILURE"
    WARNING = "WARNING"
    INFO = "INFO"

@dataclass(frozen=True)
class PdfVisualAnomaly:
    anomaly_type: str
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
    expected_diagram_pages: Set[int] = field(default_factory=set)

@dataclass(frozen=True)
class PdfInspectionRequest:
    pdf_path: Path
    expected_text_anchors: List[str] = field(default_factory=list) # Minimized text anchors
    policy: PdfValidationPolicy = field(default_factory=PdfValidationPolicy)
    baseline_pdf_path: Optional[Path] = None
    expected_page_count_range: Optional[Tuple[int, int]] = None
    generate_raster_previews: bool = False

@dataclass(frozen=True)
class PdfInspectionResult:
    is_valid: bool
    page_summary: PdfPageSummary
    anomalies: List[PdfVisualAnomaly]
    raster_previews: List[bytes]               # 150 DPI PNG byte buffers if requested

class IPdfInspectionEngine(ABC):
    @abstractmethod
    def inspect_pdf(self, request: PdfInspectionRequest) -> PdfInspectionResult:
        """Inspects candidate PDF structure, text preservation, and visual integrity."""
        pass
```

---

## 3. Detailed Implementation Requirements

1. **Deterministic Classifier Rules (`PdfDiagnosticService.classify_error`):**
   - `PACKAGE_MISSING`: Matches `! LaTeX Error: File '...sty' not found`. Returns `recommended_scope = ENVIRONMENT_DEPENDENCY` with failure location in TeX compilation environment/preamble. Action: Prompt user to install package via TinyTeX or system package manager. Prohibits faking or altering front-matter YAML.
   - `FONT_MISSING`: Matches `fontspec error: "cannot-find-font"`. Returns `recommended_scope = PUBLICATION_STYLE` with `suggested_action = "fallback_standard_font"`.
   - `TEMPLATE_CONFLICT` (Finding A, Finding D): Matches `Package soul Error: Reconstruction failed.`. Returns `recommended_scope = PUBLICATION_STYLE` with `suggested_action = "set_strikethrough_engine_ulem"` and flags `PRODUCT_DEFECT_REPORT`.
   - `SOURCE_SYNTAX`: Matches `! Undefined control sequence`, `! Missing $ inserted`, `! Extra }`. Returns `recommended_scope = DOCUMENT_MARKUP`.
   - `PANDOC_TRANSFORM`: Matches `pandoc: ...` errors. Returns `recommended_scope = DOCUMENT_MARKUP`.
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
2. **Foundation Inspection Contract Ownership (Finding B, Finding F):**
   - Places `IPdfInspectionEngine`, `PdfInspectionRequest`, and `PdfInspectionResult` in `application/ports/pdf_validator_port.py` and `application/dto/pdf_inspection_dto.py`.
   - `require_vector_drawings = False` by default: text-only documents are structurally valid without drawings. Drawings are required only when `expected_diagram_pages` is non-empty.
   - `PdfInspectionRequest` accepts bounded text anchors (`expected_text_anchors`), completely eliminating full `source_markdown` transmission into the inspection port.
   - This decouples repair orchestration (`TICK-ADE-11`) from PyMuPDF implementation (`TICK-ADE-12`).

---

## 4. Acceptance Criteria

- [ ] Missing `.sty` errors classified as `PACKAGE_MISSING` and routed to `ENVIRONMENT_DEPENDENCY` (not `YAML_METADATA`).
- [ ] Missing font errors classified as `FONT_MISSING` and routed to `PUBLICATION_STYLE`.
- [ ] `Package soul Error: Reconstruction failed.` classified as `TEMPLATE_CONFLICT` recommending typed style token `strikethrough_engine = "ulem"`.
- [ ] Unknown or unmatched errors produce `recommended_scope = None`, `confidence = 0.0`, `requires_human_inspection = True`, and `automatic_patch_allowed = False`.
- [ ] `IPdfInspectionEngine` default `require_vector_drawings = False` ensures text-only PDFs pass structural validation.
- [ ] `PdfInspectionRequest` does not require or accept full `source_markdown`.
- [ ] Preamble macro definition errors classified as `PRODUCT_DEFECT_REPORT`.
- [ ] `IPdfInspectionEngine` port and DTO contracts are fully defined and importable in `application/ports/`.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_error_classifier.py`:
  - `test_categorize_unknown_error_produces_none_scope_and_zero_confidence()`: Verifies unclassified compiler output produces `recommended_scope = None` and `automatic_patch_allowed = False`.
  - `test_categorize_missing_package_routes_to_environment_dependency()`: Verifies `.sty` missing routes to `ENVIRONMENT_DEPENDENCY`.
  - `test_categorize_soul_reconstruction_failure()`: Mandatory verification of the RTL strikethrough error.
  - `test_categorize_missing_fonts()`: Verifies fontspec error matching.
  - `test_categorize_source_syntax_errors()`: Verifies broken formula classification.
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
