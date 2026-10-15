# 01: Metadata Normalizer, Allowlist Schema & Typesetting Header Compatibility

**What to build:** Implement the metadata normalizer service (`normalize_quarto_pdf_metadata`) in `application/services/metadata_normalizer.py` and domain exceptions in `core/exceptions/domain_exceptions.py`. Enforces versioned YAML allowlists (`ALLOWED_ROOT_KEYS_V1`, `ALLOWED_PDF_KEYS_V1`), documentclass precedence, multi-format shape validation, decoupled include collections, and preflight package compatibility checking.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

## Scope

- Define domain exceptions in `core/exceptions/domain_exceptions.py`:
  - `UntrustedDocumentExtensionError(DomainError)`
  - `UnsupportedMetadataKeyError(DomainError)`
  - `UnsupportedDocumentClassError(DomainError)`
  - `MalformedMetadataError(DomainError)`
  - `ConflictingLatexHeaderError(DomainError)`
- Implement versioned allowlists in `application/services/metadata_normalizer.py`:
  - `ALLOWED_ROOT_KEYS_V1`: 35 allowlisted root-level keys.
  - `ALLOWED_PDF_KEYS_V1`: 26 allowlisted format.pdf keys.
  - `_SUPPORTED_DOCUMENT_CLASSES`: `{"scrartcl", "article", "report", "scrreprt", "book", "scrbook"}`.
  - `_PERMITTED_NON_PDF_FORMATS`: `{"html", "epub", "docx", "gfm", "revealjs", "typst"}`.
  - `_KNOWN_EXECUTION_KEYS`: 20 prohibited execution/template/filter keys.
  - `_MANAGED_PACKAGES`: 10 managed packages (`babel`, `bidi`, `polyglossia`, `xepersian`, `fontspec`, `amsmath`, `amssymb`, `tikz`, `pgfplots`, `etoolbox`).
- Implement `_validate_typed_schema(data: Dict[str, Any], is_root: bool = True)`:
  - **F-01 Enforced:** String validation branch MUST include `"papersize"`, `"title"`, `"subtitle"`, `"date"`, `"abstract"`, `"description"`, `"lang"`, `"toc-title"`, `"fontsize"`, `"mainfont"`, `"sansfont"`, `"monofont"`, `"mathfont"`, `"linkcolor"`, `"urlcolor"`, `"citecolor"`. Non-strings raise `MalformedMetadataError`.
  - Boolean validation: `"toc"`, `"number-sections"`, `"colorlinks"`, `"indent"`, `"parskip"`, `"keep-tex"`.
  - Positive integer validation: `"toc-depth"`, `"number-depth"` (`v >= 1`, rejecting booleans).
  - Positive float/int validation: `"linestretch"` (`v > 0`, rejecting booleans).
  - Direction validation: `"dir"` must be `"ltr"` or `"rtl"`.
  - List of strings validation: `"author"`, `"classoption"`, `"geometry"` (`str | list[str]`); `"authors"`, `"keywords"` (`list[str]`).
  - Null handling: `if v is None: continue` (treated as omitted/default).
- Implement documentclass precedence: `format.pdf.documentclass` overrides root `documentclass`. Whitelist enforced at both scopes.
- Implement format selection boundary across all YAML shapes:
  - String shorthand: `format: pdf` permitted; built-ins pruned; custom (e.g. `acm-pdf`) raises `UnsupportedMetadataKeyError`.
  - List shape: `format: [pdf]` permitted; built-ins pruned; custom raises `UnsupportedMetadataKeyError`.
  - Mapping shape: `format: {pdf: {...}}` permitted; built-ins pruned; custom raises `UnsupportedMetadataKeyError`.
  - Non-string keys in format mapping raise `MalformedMetadataError`.
- **F-02 Enforced (Include Normalization):**
  - Author root and format `header-includes` appended to `staged_header_includes`.
  - Author inline `include-in-header: [{text: "..."}]` appended to `staged_header_includes` (strictly after `header-includes`).
  - Author external files (`include-in-header: "evil.tex"` or `{"file": "evil.tex"}`) raise `UntrustedDocumentExtensionError`.
  - `include-before-body` and `include-after-body` inline dicts (`{"text": "..."}`) appended strictly to `staged_include_before_body` and `staged_include_after_body`. External files raise `UntrustedDocumentExtensionError`.
  - Preflight package compatibility verification (`_validate_package_compatibility`) runs on the combined validation set, raising `ConflictingLatexHeaderError` if managed packages are matched. Validation set is never assigned to output.
- **F-03 Enforced (Trust Boundary Disclosure):**
  - Module docstring documents that XeLaTeX executes within desktop user OS privileges, and that `-no-shell-escape` prevents shell commands but does NOT sandbox local filesystem reads via TeX primitives (`\openin`, `\input`).

## Non-Goals

- Executing Quarto or XeLaTeX subprocesses (deferred to Tickets 03 and 05).
- Filesystem workspace creation or staging disk writes (deferred to Ticket 04).
- Parsing Markdown body content or images (handled by existing asset rewriters and Ticket 04).

## Files Likely Impacted

- Modify/Create: `core/exceptions/domain_exceptions.py`
- Create: `application/services/metadata_normalizer.py`
- Create: `tests/unit/test_metadata_normalizer.py`

## Public Interfaces / Contracts

```python
def normalize_quarto_pdf_metadata(
    raw_meta: Dict[str, Any],
    is_tinytex: bool,
    default_title: str,
) -> Dict[str, Any]:
    """
    Normalizes author front matter into staged document.qmd front matter.
    Enforces versioned allowlists, documentclass precedence, decoupled include
    collections, and typesetting compatibility.

    Security Trust Boundary:
    XeLaTeX runs under ordinary desktop user privileges with -no-shell-escape.
    This prevents subprocess invocation (\write18) but does not sandbox filesystem
    reads via TeX primitives (\input, \openin).
    """
```

## Architectural Invariants

- **Clean Architecture:** `metadata_normalizer.py` and `domain_exceptions.py` contain zero imports of `subprocess`, PySide6/Qt, or SQLite.
- **Deterministic Pure Function:** Normalization is deterministic, idempotent, and produces no side effects on disk.
- **Source Preservation:** The original author front matter is never mutated in place.

## Acceptance Criteria

- [ ] All 5 domain exceptions defined inheriting from `DomainError`.
- [ ] **F-01 Enforced:** Non-string `papersize` (e.g. `papersize: 123`, `papersize: ["a4"]`) raises `MalformedMetadataError`. Valid string `papersize` passes. Covered at root and `format.pdf` scopes.
- [ ] **F-02 Enforced:** Author inline `include-in-header` text is merged into `staged_header_includes`. Staged PDF output contains `include-in-header: [{"file": "polpo_preamble.tex"}]`.
- [ ] Body includes (`include-before-body`, `include-after-body`) remain strictly in their body collections and never leak into `header-includes`.
- [ ] Format-scoped `documentclass` takes precedence over root-level `documentclass`. Whitelist enforced; path traversals rejected with `UnsupportedDocumentClassError`.
- [ ] Custom format extensions (e.g. `acm-pdf`) fail closed with `UnsupportedMetadataKeyError` across string, list, and mapping shapes.
- [ ] Built-in non-PDF formats (`html`, `epub`, `docx`) are pruned from staged output.
- [ ] Prohibited execution keys (`filters`, `template`, `jupyter`, `defaults`) raise `UntrustedDocumentExtensionError`.
- [ ] Conflicting managed packages (`babel`, `xepersian`, `bidi`, `polyglossia`, `amsmath`) raise `ConflictingLatexHeaderError`.
- [ ] **F-03 Enforced:** Module docstring explicitly discloses the OS privilege model and TeX filesystem read limitation.
- [ ] Comprehensive unit test suite in `tests/unit/test_metadata_normalizer.py` passes 100%.

## Verification / Tests Required

- `test_metadata_normalizer_papersize_type_validation()`: verifies F-01 string enforcement and error rejection.
- `test_metadata_normalizer_include_consolidation_and_isolation()`: verifies F-02 header consolidation and body include separation.
- `test_metadata_normalizer_documentclass_precedence()`: format override and whitelist enforcement.
- `test_metadata_normalizer_format_shapes_and_custom_rejection()`: string, list, dict shapes and custom format rejection.
- `test_metadata_normalizer_non_pdf_format_pruning()`: verifies pruning of HTML/docx.
- `test_metadata_normalizer_execution_keys_rejection()`: asserts `UntrustedDocumentExtensionError` on filters/templates.
- `test_metadata_normalizer_package_collision_detection()`: asserts `ConflictingLatexHeaderError` on conflicting packages.

## Commit Boundary

Single commit: `feat(export): implement quarto pdf metadata normalizer and allowlist schema`
