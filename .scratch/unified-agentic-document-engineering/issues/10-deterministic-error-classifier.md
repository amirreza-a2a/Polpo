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
class ErrorCategory(Enum):
    PACKAGE_MISSING = "PACKAGE_MISSING"
    FONT_MISSING = "FONT_MISSING"
    TEMPLATE_CONFLICT = "TEMPLATE_CONFLICT"
    SOURCE_SYNTAX = "SOURCE_SYNTAX"
    PANDOC_TRANSFORM = "PANDOC_TRANSFORM"
    PRODUCT_DEFECT = "PRODUCT_DEFECT"
    UNKNOWN = "UNKNOWN"

class SourceMappingEvidence(Enum):
    EXACT_TOKEN = "EXACT_TOKEN"         # Ordinal score: 1.0
    CONTEXTUAL_MATCH = "CONTEXTUAL_MATCH" # Ordinal score: 0.7
    SECTION_ANCHOR = "SECTION_ANCHOR"   # Ordinal score: 0.5
    UNKNOWN = "UNKNOWN"                 # Ordinal score: 0.0

class RemediationScope(Enum):
    DOCUMENT_MARKUP = "DOCUMENT_MARKUP"        # Markdown syntax, LaTeX math, TikZ diagrams
    YAML_METADATA = "YAML_METADATA"            # Front-matter options (geometry, lang)
    PUBLICATION_STYLE = "PUBLICATION_STYLE"    # Typed PublicationStyleProfile token adjustment
    PRODUCT_TEMPLATE = "PRODUCT_TEMPLATE"      # Polpo template, preamble, or pandoc filter issue
    ENVIRONMENT_DEPENDENCY = "ENVIRONMENT_DEPENDENCY" # Missing TeX package / system font / binary

class RemediationRiskTier(Enum):
    READ_ONLY = "READ_ONLY"
    ISOLATED_MUTATION = "ISOLATED_MUTATION"
    EXTERNAL_TRANSMISSION = "EXTERNAL_TRANSMISSION"
    CONSEQUENTIAL_SYSTEM = "CONSEQUENTIAL_SYSTEM"
    CANONICAL_ADOPTION = "CANONICAL_ADOPTION"

class SuggestedAction(Enum):
    INSPECT_SOURCE = "INSPECT_SOURCE"
    REVIEW_PUBLICATION_PROFILE = "REVIEW_PUBLICATION_PROFILE"
    REPORT_PRODUCT_DEFECT = "REPORT_PRODUCT_DEFECT"
    REVIEW_ENVIRONMENT_DEPENDENCY = "REVIEW_ENVIRONMENT_DEPENDENCY"
    HUMAN_INSPECTION_REQUIRED = "HUMAN_INSPECTION_REQUIRED"

@dataclass(frozen=True)
class ErrorClassificationResult:
    error_category: ErrorCategory
    source_mapping_evidence: SourceMappingEvidence
    remediation_scope: Optional[RemediationScope]   # None when UNKNOWN or unresolved!
    risk_tier: RemediationRiskTier
    requires_human_inspection: bool            # True when UNKNOWN
    proposal_generation_allowed: bool          # False when UNKNOWN
    automatic_patch_application_allowed: bool  # Strictly False without explicit interactive user approval
    suggested_action: SuggestedAction
    target_identifier: Optional[str] = None    # Package name, font name, or profile key
    explanation: str = ""
```

### 2.2 Foundation PDF Inspection Port Contracts (Finding B, Finding E, Finding F)
```python
# Defined in application/ports/pdf_validator_port.py and application/dto/pdf_inspection_dto.py

class AnomalySeverity(Enum):
    HARD_FAILURE = "HARD_FAILURE"        # Corrupt file, 0 pages, rendering crash, missing required diagram
    WARNING = "WARNING"                  # Orphan blank page, dimensions variance, low text density, missing optional diagram
    INFO = "INFO"                        # Vector drawing metrics, page count delta

class ExpectedDiagramKind(Enum):
    VECTOR = "VECTOR"
    RASTER = "RASTER"
    UNSPECIFIED = "UNSPECIFIED"

@dataclass(frozen=True)
class DiagramExpectation:
    page_number: int
    kind: ExpectedDiagramKind
    required: bool = False

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

@dataclass(frozen=True)
class PdfInspectionRequest:
    pdf_path: Path
    expected_text_anchors: Tuple[str, ...] = () # Minimized text anchors (max 10, max 128 chars each, max 2 KB total)
    expected_diagrams: Tuple[DiagramExpectation, ...] = () # Typed diagram expectations per page (default empty: text-only PDFs pass cleanly)
    policy: PdfValidationPolicy = field(default_factory=PdfValidationPolicy)
    baseline_pdf_path: Optional[Path] = None
    expected_page_count_range: Optional[Tuple[int, int]] = None
    generate_raster_previews: bool = False
    preview_page_indices: Tuple[int, ...] = ()

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

1. **Five-Output Decoupled Classifier Architecture (Finding C):**
   The classification service decouples five independent dimensions:
   - `error_category`: `PACKAGE_MISSING`, `FONT_MISSING`, `TEMPLATE_CONFLICT`, `SOURCE_SYNTAX`, `PANDOC_TRANSFORM`, `PRODUCT_DEFECT`, `UNKNOWN`.
   - `source_mapping_evidence`: `EXACT_TOKEN` (1.0), `CONTEXTUAL_MATCH` (0.7), `SECTION_ANCHOR` (0.5), `UNKNOWN` (0.0). *Explicit note: these values are ordinal evidence scores, NOT calibrated statistical probabilities.*
   - `remediation_scope`: `DOCUMENT_MARKUP`, `YAML_METADATA`, `PUBLICATION_STYLE`, `PRODUCT_TEMPLATE`, `ENVIRONMENT_DEPENDENCY`, or `None` (unresolved). Note: `PRODUCT_TEMPLATE` is a target scope, whereas `REPORT_PRODUCT_DEFECT` is a user action.
   - `risk_tier`: `READ_ONLY`, `ISOLATED_MUTATION`, `EXTERNAL_TRANSMISSION`, `CONSEQUENTIAL_SYSTEM`, `CANONICAL_ADOPTION`.
   - `suggested_action`: `INSPECT_SOURCE`, `REVIEW_PUBLICATION_PROFILE`, `REPORT_PRODUCT_DEFECT`, `REVIEW_ENVIRONMENT_DEPENDENCY`, `HUMAN_INSPECTION_REQUIRED`.

2. **Deterministic Classifier Rules & Source-Evidence Governance (`PdfDiagnosticService.classify_error`):**
   - `PACKAGE_MISSING`: Matches `! LaTeX Error: File '...sty' not found`. Returns `remediation_scope = RemediationScope.ENVIRONMENT_DEPENDENCY` with `suggested_action = SuggestedAction.REVIEW_ENVIRONMENT_DEPENDENCY`. Prompts user to install package via TinyTeX or system package manager. Prohibits faking or altering front-matter YAML.
   - `FONT_MISSING`: Matches `fontspec error: "cannot-find-font"`. Returns `remediation_scope = RemediationScope.PUBLICATION_STYLE` with `suggested_action = SuggestedAction.REVIEW_PUBLICATION_PROFILE`. Never silently mutates the profile.
   - `TEMPLATE_CONFLICT` (Finding A, Finding D): Matches `Package soul Error: Reconstruction failed.`. Returns `remediation_scope = RemediationScope.PUBLICATION_STYLE` (proposing typed token `strikethrough_engine = "ulem"` subject to explicit maintainer approval of D-PROP-08) or flags `suggested_action = SuggestedAction.REPORT_PRODUCT_DEFECT` with `remediation_scope = RemediationScope.PRODUCT_TEMPLATE`. Under no circumstances does it recommend modifying user Markdown source.
   - `SOURCE_SYNTAX`: Matches `! Undefined control sequence`, `! Missing $ inserted`, `! Extra }`. Returns `remediation_scope = RemediationScope.DOCUMENT_MARKUP` **only if** source mapping establishes `EXACT_TOKEN` or `CONTEXTUAL_MATCH` confidence. If source mapping is ambiguous or unmapped (`SECTION_ANCHOR` or `UNKNOWN`), returns `remediation_scope = None`, `proposal_generation_allowed = False`, and `suggested_action = SuggestedAction.HUMAN_INSPECTION_REQUIRED`.
   - `PANDOC_TRANSFORM` (Finding C): Matches `pandoc: ...` errors. Evaluates source mapping evidence: returns `remediation_scope = RemediationScope.DOCUMENT_MARKUP` only when confident Markdown syntax coordinates are proven (`EXACT_TOKEN` or `CONTEXTUAL_MATCH`). If the failure originates from writer defects, YAML syntax, or template interactions, routes to `PRODUCT_TEMPLATE`, `PUBLICATION_STYLE`, or `ENVIRONMENT_DEPENDENCY`. If unmapped, returns `remediation_scope = None`.
   - `PRODUCT_DEFECT`: Matches errors inside `polpo_preamble.tex` primitives. Returns `remediation_scope = RemediationScope.PRODUCT_TEMPLATE` with `suggested_action = SuggestedAction.REPORT_PRODUCT_DEFECT`.
   - `UNKNOWN` (Finding C): Matches unclassified or ambiguous compiler output. Returns:
     ```python
     remediation_scope = None
     source_mapping_evidence = SourceMappingEvidence.UNKNOWN
     requires_human_inspection = True
     proposal_generation_allowed = False
     automatic_patch_application_allowed = False
     suggested_action = SuggestedAction.HUMAN_INSPECTION_REQUIRED
     ```
     **Invariant:** An unknown error NEVER produces an automatic Markdown patch.

3. **Separation of Patch Proposal from Patch Adoption:**
   - A confident source match (`EXACT_TOKEN` or unique `CONTEXTUAL_MATCH`) may authorize generating a candidate patch proposal inside an isolated scratchpad (`AgentWorkingCopy`).
   - It NEVER authorizes silently applying the patch to the active editor draft or committing to canonical document storage. Applying changes to the editor requires interactive review and explicit user confirmation; canonical publishing requires explicit save under OCC.

4. **Foundation Inspection Contract Ownership & Typed Diagram Expectations (Finding B, Finding E, Finding F):**
   - Places `IPdfInspectionEngine`, `PdfInspectionRequest`, `DiagramExpectation`, and `PdfInspectionResult` in `application/ports/pdf_validator_port.py` and `application/dto/pdf_inspection_dto.py`.
   - Text-only documents default to `expected_diagrams = ()` and pass structural validation cleanly with zero vector drawings or raster images.
   - When `expected_diagrams` is populated:
     - `ExpectedDiagramKind.VECTOR`: verifies `len(page.get_drawings()) > 0`. Path existence evidence only; no semantic correctness claim.
     - `ExpectedDiagramKind.RASTER`: verifies `len(page.get_images()) > 0`. Image existence evidence only; no semantic correctness claim.
     - `ExpectedDiagramKind.UNSPECIFIED`: no presence check is inferred.
   - Severity: Missing required diagram (`required=True`) produces `AnomalySeverity.HARD_FAILURE` (failing validation); missing optional diagram (`required=False`) produces `AnomalySeverity.WARNING`.
   - `expected_text_anchors` is bounded (max 10 anchors, max 128 chars each, max 2 KB total); short-lived local memory only; never persisted in SQLite; never sent to AI automatically.
   - Raster previews: max 5 pages, 150 DPI, 50 MB buffer cap, 5.0s timeout.
   - Decouples repair orchestration (`TICK-ADE-11`) from PyMuPDF implementation (`TICK-ADE-12`).

---

## 4. Acceptance Criteria

- [ ] Missing `.sty` errors classified as `PACKAGE_MISSING` and routed to `ENVIRONMENT_DEPENDENCY` (not `YAML_METADATA`).
- [ ] Missing font errors classified as `FONT_MISSING` and routed to `PUBLICATION_STYLE`.
- [ ] `Package soul Error: Reconstruction failed.` classified as `TEMPLATE_CONFLICT` routing to `PUBLICATION_STYLE` (proposing proposal token `strikethrough_engine = "ulem"`) or `PRODUCT_TEMPLATE` with `suggested_action = SuggestedAction.REPORT_PRODUCT_DEFECT`; never recommends Markdown source modification.
- [ ] `PANDOC_TRANSFORM` errors do NOT default to `DOCUMENT_MARKUP`; require strong source mapping evidence (`EXACT_TOKEN` or `CONTEXTUAL_MATCH`), failing closed to `remediation_scope = None` on ambiguous mapping.
- [ ] LaTeX syntax errors (`Undefined control sequence`, `Missing $ inserted`, `Extra }`) without confident source mapping yield `remediation_scope = None` and `proposal_generation_allowed = False`.
- [ ] Unknown or unmatched errors produce `remediation_scope = None`, `requires_human_inspection = True`, `proposal_generation_allowed = False`, and `suggested_action = SuggestedAction.HUMAN_INSPECTION_REQUIRED`.
- [ ] `IPdfInspectionEngine` default `expected_diagrams = ()` ensures text-only PDFs pass structural validation.
- [ ] `PdfInspectionRequest` accepts bounded `expected_text_anchors: Tuple[str, ...]` and does not require or accept full `source_markdown`.
- [ ] Preamble macro definition errors classified as `PRODUCT_DEFECT` routing to `PRODUCT_TEMPLATE`.
- [ ] `IPdfInspectionEngine` port and DTO contracts are fully defined and importable in `application/ports/`.

---

## 5. Test Specifications

### 5.1 Unit Tests
- `tests/unit/test_error_classifier.py`:
  - `test_repair_scope_enum_contains_only_canonical_values()`: Asserts `RemediationScope` contains only `DOCUMENT_MARKUP`, `YAML_METADATA`, `PUBLICATION_STYLE`, `PRODUCT_TEMPLATE`, `ENVIRONMENT_DEPENDENCY`.
  - `test_product_template_scope_is_valid()`: Verifies `PRODUCT_TEMPLATE` is a valid remediation scope.
  - `test_product_defect_report_is_a_suggested_action()`: Verifies `REPORT_PRODUCT_DEFECT` belongs to `SuggestedAction`, not `RemediationScope`.
  - `test_unknown_error_has_no_remediation_scope()`: Verifies unclassified compiler output produces `remediation_scope = None`.
  - `test_unknown_error_cannot_generate_patch()`: Asserts `UNKNOWN` error sets `proposal_generation_allowed = False` and `automatic_patch_application_allowed = False`.
  - `test_pandoc_transform_does_not_default_to_document_markup()`: Verifies Pandoc transform errors do not default to `DOCUMENT_MARKUP` without verified source mapping evidence.
  - `test_source_syntax_requires_unique_source_evidence()`: Verifies broken formula classification routes to `DOCUMENT_MARKUP` only with `EXACT_TOKEN` / `CONTEXTUAL_MATCH`, yielding `remediation_scope = None` on unmapped syntax errors.
  - `test_ambiguous_source_match_fails_closed()`: Verifies ambiguous diagnostics return `remediation_scope = None` and `requires_human_inspection = True`.
  - `test_template_conflict_never_modifies_user_markdown()`: Mandatory verification that `soul` reconstruction error routes to `PUBLICATION_STYLE` / `PRODUCT_TEMPLATE` and never mutates document markup.
  - `test_package_missing_never_modifies_yaml()`: Verifies missing package routes to `ENVIRONMENT_DEPENDENCY` and never suggests modifying YAML front matter.
  - `test_font_fallback_requires_explicit_user_review()`: Verifies fontspec error matching routes to `PUBLICATION_STYLE` with `suggested_action = REVIEW_PUBLICATION_PROFILE` and never silently alters saved profile.
  - `test_patch_proposal_does_not_authorize_adoption()`: Asserts candidate proposal generation in scratchpad does not authorize editor buffer mutation or canonical OCC publication.

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
