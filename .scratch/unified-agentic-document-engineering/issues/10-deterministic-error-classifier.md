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
    YAML_METADATA = "YAML_METADATA"            # Front-matter options
    PUBLICATION_STYLE = "PUBLICATION_STYLE"    # Typed PublicationStyleProfile token adjustment
    PRODUCT_DEFECT_REPORT = "PRODUCT_DEFECT_REPORT" # Polpo template or engine issue

@dataclass(frozen=True)
class ErrorClassificationResult:
    category: ErrorCategory
    recommended_scope: RepairScope
    suggested_action: str
    target_identifier: Optional[str] = None     # Package name, font name, or profile key
```

### 2.2 Foundation PDF Inspection Port Contracts (Finding B)
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
    require_vector_drawings: bool = True

@dataclass(frozen=True)
class PdfInspectionRequest:
    pdf_path: Path
    source_markdown: str
    policy: PdfValidationPolicy = field(default_factory=PdfValidationPolicy)
    baseline_pdf_path: Optional[Path] = None
    generate_raster_previews: bool = False

@dataclass(frozen=True)
class PdfInspectionResult:
    is_valid: bool
    page_summary: PdfPageSummary
    anomalies: List[PdfVisualAnomaly]
    raster_previews: List[bytes]

class IPdfInspectionEngine(ABC):
    @abstractmethod
    def inspect_pdf(self, request: PdfInspectionRequest) -> PdfInspectionResult:
        """Inspects candidate PDF structure, text preservation, and visual integrity."""
        pass
```

---

## 3. Detailed Implementation Requirements

1. **Deterministic Classifier Rules (`PdfDiagnosticService.classify_error`):**
   - `PACKAGE_MISSING`: Matches `! LaTeX Error: File '...sty' not found`. Returns `recommended_scope = YAML_METADATA` (package installation).
   - `FONT_MISSING`: Matches `fontspec error: "cannot-find-font"`. Returns `recommended_scope = PUBLICATION_STYLE`.
   - `TEMPLATE_CONFLICT` (Finding A): Matches `Package soul Error: Reconstruction failed.`. Returns `recommended_scope = PUBLICATION_STYLE` with `suggested_action = "set_strikethrough_engine_ulem"` and flags `PRODUCT_DEFECT_REPORT`.
   - `SOURCE_SYNTAX`: Matches `! Undefined control sequence`, `! Missing $ inserted`, `! Extra }`. Returns `recommended_scope = DOCUMENT_MARKUP`.
   - `PANDOC_TRANSFORM`: Matches `pandoc: ...` errors. Returns `recommended_scope = DOCUMENT_MARKUP`.
   - `PRODUCT_DEFECT`: Matches errors inside `polpo_preamble.tex` primitives. Returns `recommended_scope = PRODUCT_DEFECT_REPORT`.
2. **Foundation Inspection Contract Ownership (Finding B):**
   - Places `IPdfInspectionEngine`, `PdfInspectionRequest`, and `PdfInspectionResult` in `application/ports/pdf_validator_port.py` and `application/dto/pdf_inspection_dto.py`.
   - This decouples repair orchestration (`TICK-ADE-11`) from PyMuPDF implementation (`TICK-ADE-12`).

---

## 4. Acceptance Criteria

- [ ] Missing `.sty` errors classified as `PACKAGE_MISSING` and routed to package recovery.
- [ ] Missing font errors classified as `FONT_MISSING`.
- [ ] `Package soul Error: Reconstruction failed.` classified as `TEMPLATE_CONFLICT` recommending typed style token `strikethrough_engine = "ulem"`.
- [ ] Preamble macro definition errors classified as `PRODUCT_DEFECT_REPORT`.
- [ ] `IPdfInspectionEngine` port and DTO contracts are fully defined and importable in `application/ports/`.
- [ ] Output provides unambiguous `recommended_scope` preventing destructive edits to author Markdown.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_error_classifier.py`:
  - `test_categorize_soul_reconstruction_failure()`: Mandatory verification of the RTL strikethrough error.
  - `test_categorize_missing_packages_and_fonts()`: Verifies regex matching.
  - `test_categorize_source_syntax_errors()`: Verifies broken formula classification.
  - `test_scope_separation_enforcement()`: Confirms product defects do not recommend document edits.

### 5.2 Negative & Security Tests
- `test_prevent_classifier_redos()`: Asserts regex execution completes in $< 5$ms on large compiler logs.

### 5.3 Concurrency & Persistence Considerations
- Stateless, pure functional classification service.

---

## 6. Error and Recovery Behavior

- Unmatched errors default safely to `ErrorCategory.UNKNOWN` with `recommended_scope = DOCUMENT_MARKUP` for human inspection.

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
