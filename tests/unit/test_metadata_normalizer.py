# ============================================================
#  tests/unit/test_metadata_normalizer.py
#  Unit tests for Quarto PDF Metadata Normalizer Service
#  Phase 13 — Ticket #97
# ============================================================

import pytest

from application.services.metadata_normalizer import (
    ALLOWED_PDF_KEYS_V1,
    ALLOWED_ROOT_KEYS_V1,
    normalize_quarto_pdf_metadata,
)
from core.exceptions.domain_exceptions import (
    ConflictingLatexHeaderError,
    DomainError,
    MalformedMetadataError,
    UnsupportedDocumentClassError,
    UnsupportedMetadataKeyError,
    UntrustedDocumentExtensionError,
)


def test_domain_exceptions_hierarchy():
    """Verify all 5 required domain exceptions inherit from DomainError."""
    assert issubclass(UntrustedDocumentExtensionError, DomainError)
    assert issubclass(UnsupportedMetadataKeyError, DomainError)
    assert issubclass(UnsupportedDocumentClassError, DomainError)
    assert issubclass(MalformedMetadataError, DomainError)
    assert issubclass(ConflictingLatexHeaderError, DomainError)


def test_metadata_normalizer_papersize_type_validation():
    """
    Mandatory F-01:
    'papersize' must be a string. Non-string values (int, list, dict)
    must fail with MalformedMetadataError at both root and format.pdf scopes.
    """
    # Valid string at root
    res1 = normalize_quarto_pdf_metadata({"papersize": "a4"}, is_tinytex=False, default_title="Test")
    assert res1["papersize"] == "a4"

    # Valid string in format.pdf
    res2 = normalize_quarto_pdf_metadata(
        {"format": {"pdf": {"papersize": "letter"}}},
        is_tinytex=False,
        default_title="Test",
    )
    assert res2["format"]["pdf"]["papersize"] == "letter"

    # Non-string at root (int)
    with pytest.raises(MalformedMetadataError, match="papersize"):
        normalize_quarto_pdf_metadata({"papersize": 123}, is_tinytex=False, default_title="Test")

    # Non-string at root (list)
    with pytest.raises(MalformedMetadataError, match="papersize"):
        normalize_quarto_pdf_metadata({"papersize": ["a4"]}, is_tinytex=False, default_title="Test")

    # Non-string in format.pdf (int)
    with pytest.raises(MalformedMetadataError, match="papersize"):
        normalize_quarto_pdf_metadata(
            {"format": {"pdf": {"papersize": 456}}},
            is_tinytex=False,
            default_title="Test",
        )

    # Non-string in format.pdf (list)
    with pytest.raises(MalformedMetadataError, match="papersize"):
        normalize_quarto_pdf_metadata(
            {"format": {"pdf": {"papersize": ["letter"]}}},
            is_tinytex=False,
            default_title="Test",
        )


def test_metadata_normalizer_include_consolidation_and_isolation():
    """
    Mandatory F-02:
    - Author inline include-in-header text is merged into staged_header_includes
      strictly after header-includes.
    - Staged PDF output contains include-in-header: [{'file': 'polpo_preamble.tex'}].
    - External file references in include-in-header raise UntrustedDocumentExtensionError.
    - Body includes (include-before-body, include-after-body) remain strictly in their
      body collections and never leak into header-includes.
    """
    raw_meta = {
        "header-includes": ["\\newcommand{\\foo}{bar}"],
        "format": {
            "pdf": {
                "include-in-header": [{"text": "\\newcommand{\\baz}{qux}"}],
                "include-before-body": [{"text": "\\section{Intro}"}],
                "include-after-body": [{"text": "\\section{Appendix}"}],
            }
        },
    }

    staged = normalize_quarto_pdf_metadata(raw_meta, is_tinytex=False, default_title="Doc")

    pdf_format = staged["format"]["pdf"]
    # Staged include-in-header references the preamble file
    assert pdf_format["include-in-header"] == [{"file": "polpo_preamble.tex"}]

    # Consolidated header includes contains both in order
    assert "header-includes" in pdf_format
    assert pdf_format["header-includes"] == [
        "\\newcommand{\\foo}{bar}",
        "\\newcommand{\\baz}{qux}",
    ]

    # Body includes remain isolated
    assert pdf_format["include-before-body"] == [{"text": "\\section{Intro}"}]
    assert pdf_format["include-after-body"] == [{"text": "\\section{Appendix}"}]

    # External file in include-in-header raises UntrustedDocumentExtensionError
    with pytest.raises(UntrustedDocumentExtensionError):
        normalize_quarto_pdf_metadata(
            {"format": {"pdf": {"include-in-header": "evil.tex"}}},
            is_tinytex=False,
            default_title="Doc",
        )

    with pytest.raises(UntrustedDocumentExtensionError):
        normalize_quarto_pdf_metadata(
            {"format": {"pdf": {"include-in-header": [{"file": "evil.tex"}]}}},
            is_tinytex=False,
            default_title="Doc",
        )

    with pytest.raises(UntrustedDocumentExtensionError):
        normalize_quarto_pdf_metadata(
            {"format": {"pdf": {"include-before-body": "bad.tex"}}},
            is_tinytex=False,
            default_title="Doc",
        )


def test_metadata_normalizer_documentclass_precedence():
    """
    Format-scoped documentclass takes precedence over root-level documentclass.
    Whitelist enforced; unsupported or path-traversal classes raise UnsupportedDocumentClassError.
    """
    # Override root with format.pdf
    raw_meta = {
        "documentclass": "article",
        "format": {"pdf": {"documentclass": "scrreprt"}},
    }
    staged = normalize_quarto_pdf_metadata(raw_meta, is_tinytex=False, default_title="Doc")
    assert staged["format"]["pdf"]["documentclass"] == "scrreprt"

    # Unsupported documentclass at root
    with pytest.raises(UnsupportedDocumentClassError):
        normalize_quarto_pdf_metadata({"documentclass": "novel"}, is_tinytex=False, default_title="Doc")

    # Path traversal attack
    with pytest.raises(UnsupportedDocumentClassError):
        normalize_quarto_pdf_metadata(
            {"documentclass": "../../../evil"},
            is_tinytex=False,
            default_title="Doc",
        )

    with pytest.raises(UnsupportedDocumentClassError):
        normalize_quarto_pdf_metadata(
            {"format": {"pdf": {"documentclass": "../../../evil"}}},
            is_tinytex=False,
            default_title="Doc",
        )


def test_metadata_normalizer_format_shapes_and_custom_rejection():
    """
    Test format selection across string, list, and mapping shapes.
    Custom format extensions fail closed with UnsupportedMetadataKeyError.
    Non-string keys in format mapping raise MalformedMetadataError.
    """
    # String shorthand: format: pdf
    s1 = normalize_quarto_pdf_metadata({"format": "pdf"}, is_tinytex=False, default_title="Doc")
    assert "pdf" in s1["format"]

    # String custom: format: acm-pdf
    with pytest.raises(UnsupportedMetadataKeyError):
        normalize_quarto_pdf_metadata({"format": "acm-pdf"}, is_tinytex=False, default_title="Doc")

    # List shape: format: [pdf]
    s2 = normalize_quarto_pdf_metadata({"format": ["pdf"]}, is_tinytex=False, default_title="Doc")
    assert "pdf" in s2["format"]

    # List custom: format: [acm-pdf]
    with pytest.raises(UnsupportedMetadataKeyError):
        normalize_quarto_pdf_metadata({"format": ["acm-pdf"]}, is_tinytex=False, default_title="Doc")

    # Mapping custom: format: {acm-pdf: {...}}
    with pytest.raises(UnsupportedMetadataKeyError):
        normalize_quarto_pdf_metadata(
            {"format": {"acm-pdf": {"documentclass": "article"}}},
            is_tinytex=False,
            default_title="Doc",
        )

    # Non-string format key in mapping
    with pytest.raises(MalformedMetadataError):
        normalize_quarto_pdf_metadata({"format": {123: {}}}, is_tinytex=False, default_title="Doc")


def test_metadata_normalizer_non_pdf_format_pruning():
    """
    Built-in non-PDF formats (html, epub, docx, typst, revealjs, gfm)
    are pruned from staged output.
    """
    raw_meta = {
        "format": {
            "pdf": {"toc": True},
            "html": {"toc": False, "theme": "cosmo"},
            "docx": {"number-sections": True},
        }
    }
    staged = normalize_quarto_pdf_metadata(raw_meta, is_tinytex=False, default_title="Doc")
    assert "pdf" in staged["format"]
    assert "html" not in staged["format"]
    assert "docx" not in staged["format"]


def test_metadata_normalizer_execution_keys_rejection():
    """Prohibited execution keys raise UntrustedDocumentExtensionError."""
    prohibited_keys = ["filters", "template", "jupyter", "defaults", "knitr", "engine", "execute"]
    for key in prohibited_keys:
        with pytest.raises(UntrustedDocumentExtensionError):
            normalize_quarto_pdf_metadata({key: "value"}, is_tinytex=False, default_title="Doc")

        with pytest.raises(UntrustedDocumentExtensionError):
            normalize_quarto_pdf_metadata(
                {"format": {"pdf": {key: "value"}}},
                is_tinytex=False,
                default_title="Doc",
            )


def test_metadata_normalizer_package_collision_detection():
    """Managed packages in header text raise ConflictingLatexHeaderError."""
    managed_examples = [
        "\\usepackage{babel}",
        "\\usepackage[utf8]{xepersian}",
        "\\usepackage{bidi}",
        "\\usepackage{polyglossia}",
        "\\usepackage{amsmath}",
        "\\RequirePackage{tikz}",
        "\\usepackage{pgfplots}",
        "\\usepackage{fontspec}",
    ]
    for code in managed_examples:
        with pytest.raises(ConflictingLatexHeaderError):
            normalize_quarto_pdf_metadata(
                {"header-includes": [code]},
                is_tinytex=False,
                default_title="Doc",
            )

        with pytest.raises(ConflictingLatexHeaderError):
            normalize_quarto_pdf_metadata(
                {"format": {"pdf": {"include-in-header": [{"text": code}]}}},
                is_tinytex=False,
                default_title="Doc",
            )

    # Whitespace variants between macro, bracket options, and brace arguments
    whitespace_examples = [
        "\\usepackage {babel}",
        "\\usepackage [utf8]{xepersian}",
        "\\usepackage[utf8] {xepersian}",
        "\\usepackage \n [utf8] \n {xepersian}",
        "\\RequirePackage   {tikz}",
    ]
    for code in whitespace_examples:
        with pytest.raises(ConflictingLatexHeaderError):
            normalize_quarto_pdf_metadata(
                {"header-includes": [code]},
                is_tinytex=False,
                default_title="Doc",
            )

    # Package collision inside include-before-body and include-after-body
    with pytest.raises(ConflictingLatexHeaderError):
        normalize_quarto_pdf_metadata(
            {"format": {"pdf": {"include-before-body": [{"text": "\\usepackage{amsmath}"}]}}},
            is_tinytex=False,
            default_title="Doc",
        )

    with pytest.raises(ConflictingLatexHeaderError):
        normalize_quarto_pdf_metadata(
            {"format": {"pdf": {"include-after-body": [{"text": "\\usepackage{amssymb}"}]}}},
            is_tinytex=False,
            default_title="Doc",
        )

    # TeX comments containing managed package names must not trigger false positives
    commented_meta = {
        "header-includes": ["% \\usepackage{babel}\n% We manage babel separately\n\\geometry{margin=1in}"]
    }
    staged = normalize_quarto_pdf_metadata(commented_meta, is_tinytex=False, default_title="Doc")
    assert staged is not None


def test_metadata_normalizer_type_validations():
    """Verify various field type validations and null handling."""
    # Booleans
    with pytest.raises(MalformedMetadataError):
        normalize_quarto_pdf_metadata({"toc": "true"}, is_tinytex=False, default_title="Doc")

    with pytest.raises(MalformedMetadataError):
        normalize_quarto_pdf_metadata({"number-sections": 1}, is_tinytex=False, default_title="Doc")

    # Positive integer
    with pytest.raises(MalformedMetadataError):
        normalize_quarto_pdf_metadata({"toc-depth": 0}, is_tinytex=False, default_title="Doc")

    with pytest.raises(MalformedMetadataError):
        normalize_quarto_pdf_metadata({"toc-depth": True}, is_tinytex=False, default_title="Doc")

    # Positive float
    with pytest.raises(MalformedMetadataError):
        normalize_quarto_pdf_metadata({"linestretch": -1.0}, is_tinytex=False, default_title="Doc")

    with pytest.raises(MalformedMetadataError):
        normalize_quarto_pdf_metadata({"linestretch": True}, is_tinytex=False, default_title="Doc")

    # Direction
    with pytest.raises(MalformedMetadataError):
        normalize_quarto_pdf_metadata({"dir": "up"}, is_tinytex=False, default_title="Doc")

    # String list / str
    with pytest.raises(MalformedMetadataError):
        normalize_quarto_pdf_metadata({"author": 123}, is_tinytex=False, default_title="Doc")

    with pytest.raises(MalformedMetadataError):
        normalize_quarto_pdf_metadata({"keywords": "single-string"}, is_tinytex=False, default_title="Doc")

    # Null values omitted/default
    clean = normalize_quarto_pdf_metadata(
        {"title": None, "subtitle": None, "toc": None},
        is_tinytex=False,
        default_title="Default Title",
    )
    assert clean["title"] == "Default Title"
    assert "toc" not in clean

    # Unsupported keys at root and format.pdf scope
    with pytest.raises(UnsupportedMetadataKeyError, match="unsupported_key"):
        normalize_quarto_pdf_metadata({"unsupported_key": "val"}, is_tinytex=False, default_title="Doc")

    with pytest.raises(UnsupportedMetadataKeyError, match="unsupported_pdf_field"):
        normalize_quarto_pdf_metadata(
            {"format": {"pdf": {"unsupported_pdf_field": "val"}}},
            is_tinytex=False,
            default_title="Doc",
        )


def test_metadata_normalizer_allowlist_completeness():
    """Verify allowlist sizes match specification."""
    assert len(ALLOWED_ROOT_KEYS_V1) == 35
    assert len(ALLOWED_PDF_KEYS_V1) == 26
