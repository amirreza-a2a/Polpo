# ============================================================
#  application/services/metadata_normalizer.py
#  Quarto PDF Metadata Normalizer Service
#  Phase 13 — Ticket #97
# ============================================================

"""
Quarto PDF Metadata Normalizer Service.

Security Trust Boundary Disclosure (Mandatory F-03):
---------------------------------------------------
XeLaTeX executes within desktop user OS privileges.
The compilation flag -no-shell-escape strictly prevents shell command execution
and subprocess invocation via \\write18. However, -no-shell-escape does NOT
sandbox host filesystem reads via native TeX primitives (such as \\input and \\openin).
Therefore, metadata normalization enforces strict allowlists, rejects external file
inclusions from untrusted document front matter, and prevents arbitrary package collisions.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from core.exceptions.domain_exceptions import (
    ConflictingLatexHeaderError,
    MalformedMetadataError,
    UnsupportedDocumentClassError,
    UnsupportedMetadataKeyError,
    UntrustedDocumentExtensionError,
)

# Versioned Allowlist: 35 allowlisted root-level keys
ALLOWED_ROOT_KEYS_V1: frozenset[str] = frozenset({
    "title",
    "subtitle",
    "author",
    "authors",
    "date",
    "abstract",
    "description",
    "keywords",
    "lang",
    "dir",
    "format",
    "documentclass",
    "classoption",
    "papersize",
    "fontsize",
    "mainfont",
    "sansfont",
    "monofont",
    "mathfont",
    "linestretch",
    "geometry",
    "toc",
    "toc-depth",
    "toc-title",
    "number-sections",
    "number-depth",
    "colorlinks",
    "linkcolor",
    "urlcolor",
    "citecolor",
    "indent",
    "parskip",
    "keep-tex",
    "header-includes",
    "include-in-header",
})

# Versioned Allowlist: 26 allowlisted format.pdf keys
ALLOWED_PDF_KEYS_V1: frozenset[str] = frozenset({
    "documentclass",
    "classoption",
    "papersize",
    "fontsize",
    "mainfont",
    "sansfont",
    "monofont",
    "mathfont",
    "linestretch",
    "geometry",
    "toc",
    "toc-depth",
    "toc-title",
    "number-sections",
    "number-depth",
    "colorlinks",
    "linkcolor",
    "urlcolor",
    "citecolor",
    "indent",
    "parskip",
    "keep-tex",
    "header-includes",
    "include-in-header",
    "include-before-body",
    "include-after-body",
})

# Supported LaTeX document classes
_SUPPORTED_DOCUMENT_CLASSES: frozenset[str] = frozenset({
    "scrartcl",
    "article",
    "report",
    "scrreprt",
    "book",
    "scrbook",
})

# Permitted non-PDF formats pruned during PDF export staging
_PERMITTED_NON_PDF_FORMATS: frozenset[str] = frozenset({
    "html",
    "epub",
    "docx",
    "gfm",
    "revealjs",
    "typst",
})

# Prohibited execution, template, and filter keys (20 known keys)
_KNOWN_EXECUTION_KEYS: frozenset[str] = frozenset({
    "filters",
    "template",
    "template-partials",
    "jupyter",
    "knitr",
    "defaults",
    "engine",
    "execute",
    "eval",
    "echo",
    "warning",
    "error",
    "message",
    "output",
    "include",
    "code-fold",
    "code-summary",
    "code-tools",
    "code-line-numbers",
    "server",
})

# System-managed LaTeX packages conflicting with custom user headers
_MANAGED_PACKAGES: frozenset[str] = frozenset({
    "babel",
    "bidi",
    "polyglossia",
    "xepersian",
    "fontspec",
    "amsmath",
    "amssymb",
    "tikz",
    "pgfplots",
    "etoolbox",
})

_LATEX_PACKAGE_PATTERN = re.compile(
    r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}"
)


def _validate_package_compatibility(snippets: List[str]) -> None:
    """Detect collisions with system-managed LaTeX packages in header text."""
    for snippet in snippets:
        # Strip unescaped TeX comments (% to end of line) before inspection
        clean_snippet = re.sub(r"(?<!\\)%.*$", "", snippet, flags=re.MULTILINE)
        for match in _LATEX_PACKAGE_PATTERN.finditer(clean_snippet):
            raw_pkg_list = match.group(1)
            packages = [pkg.strip() for pkg in raw_pkg_list.split(",") if pkg.strip()]
            for pkg in packages:
                if pkg in _MANAGED_PACKAGES:
                    raise ConflictingLatexHeaderError(
                        f"LaTeX package '{pkg}' is managed by Polpo and cannot be loaded in custom headers."
                    )


def _validate_typed_schema(data: Dict[str, Any], is_root: bool = True) -> None:
    """
    Validates field types against the allowlist schema.
    Enforces F-01 ('papersize' string validation).
    """
    valid_field_names = ALLOWED_ROOT_KEYS_V1 if is_root else ALLOWED_PDF_KEYS_V1

    for key, value in data.items():
        if key in _KNOWN_EXECUTION_KEYS:
            raise UntrustedDocumentExtensionError(
                f"Prohibited execution key '{key}' is not allowed in document metadata."
            )

        if key not in valid_field_names:
            scope = "root" if is_root else "format.pdf"
            raise UnsupportedMetadataKeyError(
                f"Unsupported metadata key '{key}' in {scope} scope."
            )

        if value is None:
            continue

        # F-01: String validation branch MUST include papersize
        if key in {
            "papersize",
            "title",
            "subtitle",
            "date",
            "abstract",
            "description",
            "lang",
            "toc-title",
            "fontsize",
            "mainfont",
            "sansfont",
            "monofont",
            "mathfont",
            "linkcolor",
            "urlcolor",
            "citecolor",
        }:
            if not isinstance(value, str):
                raise MalformedMetadataError(
                    f"Metadata field '{key}' must be a string, got {type(value).__name__}."
                )

        # Booleans (check exact bool type to prevent bool/int conflation)
        elif key in {"toc", "number-sections", "colorlinks", "indent", "parskip", "keep-tex"}:
            if not isinstance(value, bool):
                raise MalformedMetadataError(
                    f"Metadata field '{key}' must be a boolean, got {type(value).__name__}."
                )

        # Positive integers >= 1 (reject bools)
        elif key in {"toc-depth", "number-depth"}:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise MalformedMetadataError(
                    f"Metadata field '{key}' must be a positive integer >= 1."
                )

        # Positive float or int > 0 (reject bools)
        elif key == "linestretch":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise MalformedMetadataError(
                    f"Metadata field 'linestretch' must be a positive number > 0."
                )

        # Direction
        elif key == "dir":
            if value not in {"ltr", "rtl"}:
                raise MalformedMetadataError(
                    f"Metadata field 'dir' must be 'ltr' or 'rtl', got '{value}'."
                )

        # String or list of strings
        elif key in {"author", "classoption", "geometry"}:
            if isinstance(value, str):
                continue
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                continue
            raise MalformedMetadataError(
                f"Metadata field '{key}' must be a string or list of strings."
            )

        # List of strings strictly
        elif key in {"authors", "keywords"}:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise MalformedMetadataError(
                    f"Metadata field '{key}' must be a list of strings."
                )

        # Document class
        elif key == "documentclass":
            if not isinstance(value, str) or value not in _SUPPORTED_DOCUMENT_CLASSES:
                raise UnsupportedDocumentClassError(
                    f"Document class '{value}' is not supported. Must be one of: "
                    f"{', '.join(sorted(_SUPPORTED_DOCUMENT_CLASSES))}."
                )


def _extract_header_includes(raw_val: Any) -> List[str]:
    """Extract raw LaTeX snippets from header-includes field."""
    snippets: List[str] = []
    if raw_val is None:
        return snippets
    if isinstance(raw_val, str):
        snippets.append(raw_val)
    elif isinstance(raw_val, list):
        for item in raw_val:
            if isinstance(item, str):
                snippets.append(item)
            elif isinstance(item, dict):
                if "file" in item:
                    raise UntrustedDocumentExtensionError(
                        "External file references in header-includes are not permitted."
                    )
                if "text" in item and isinstance(item["text"], str):
                    snippets.append(item["text"])
                else:
                    raise MalformedMetadataError("Malformed dict in header-includes.")
            else:
                raise MalformedMetadataError(
                    f"Unsupported item in header-includes: {type(item).__name__}."
                )
    else:
        raise MalformedMetadataError("header-includes must be a string or list.")
    return snippets


def _extract_include_items(
    raw_val: Any,
    field_name: str,
    allow_files: bool = False,
) -> List[Dict[str, str]]:
    """Extract and validate include items ([{text: ...}]), rejecting external files."""
    results: List[Dict[str, str]] = []
    if raw_val is None:
        return results

    if isinstance(raw_val, str):
        # A raw string in include-in-header/body is an external file path in Quarto
        raise UntrustedDocumentExtensionError(
            f"External file inclusion via '{field_name}' ('{raw_val}') is not permitted."
        )

    if isinstance(raw_val, dict):
        raw_val = [raw_val]

    if isinstance(raw_val, list):
        for item in raw_val:
            if isinstance(item, str):
                raise UntrustedDocumentExtensionError(
                    f"External file inclusion via '{field_name}' ('{item}') is not permitted."
                )
            if isinstance(item, dict):
                if item.get("file") == "polpo_preamble.tex":
                    # Internal Polpo preamble template is trusted and managed by staging workspace
                    continue
                if "file" in item and not allow_files:
                    raise UntrustedDocumentExtensionError(
                        f"External file inclusion via '{field_name}' ('{item['file']}') is not permitted."
                    )
                if "text" in item:
                    if not isinstance(item["text"], str):
                        raise MalformedMetadataError(
                            f"Field 'text' in '{field_name}' must be a string."
                        )
                    results.append({"text": item["text"]})
                elif "file" in item and allow_files:
                    results.append({"file": item["file"]})
                else:
                    raise MalformedMetadataError(f"Missing 'text' in '{field_name}' item.")
            else:
                raise MalformedMetadataError(f"Unsupported item in '{field_name}'.")
    else:
        raise MalformedMetadataError(f"Field '{field_name}' must be a dict or list.")

    return results


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
    This prevents subprocess invocation (\\write18) but does not sandbox filesystem
    reads via TeX primitives (\\input, \\openin).
    """
    if not isinstance(raw_meta, dict):
        raise MalformedMetadataError("Document metadata must be a dictionary.")

    # 1. Validate root schema
    _validate_typed_schema(raw_meta, is_root=True)

    # 2. Parse and normalize format
    raw_format = raw_meta.get("format")
    pdf_custom_meta: Dict[str, Any] = {}

    if raw_format is None:
        pass
    elif isinstance(raw_format, str):
        if raw_format == "pdf":
            pass
        elif raw_format in _PERMITTED_NON_PDF_FORMATS:
            pass  # Pruned
        else:
            raise UnsupportedMetadataKeyError(f"Unsupported format '{raw_format}'.")
    elif isinstance(raw_format, list):
        for fmt in raw_format:
            if not isinstance(fmt, str):
                raise MalformedMetadataError("Format list items must be strings.")
            if fmt == "pdf":
                pass
            elif fmt in _PERMITTED_NON_PDF_FORMATS:
                pass  # Pruned
            else:
                raise UnsupportedMetadataKeyError(f"Unsupported format '{fmt}'.")
    elif isinstance(raw_format, dict):
        for fmt_key, fmt_val in raw_format.items():
            if not isinstance(fmt_key, str):
                raise MalformedMetadataError("Format mapping keys must be strings.")
            if fmt_key == "pdf":
                if fmt_val is not None:
                    if not isinstance(fmt_val, dict):
                        raise MalformedMetadataError("format.pdf must be a dictionary.")
                    _validate_typed_schema(fmt_val, is_root=False)
                    pdf_custom_meta = dict(fmt_val)
            elif fmt_key in _PERMITTED_NON_PDF_FORMATS:
                pass  # Pruned
            else:
                raise UnsupportedMetadataKeyError(f"Unsupported format '{fmt_key}'.")
    else:
        raise MalformedMetadataError("Format field has an invalid shape.")

    # 3. Documentclass precedence: format.pdf overrides root, default to 'scrartcl'
    chosen_documentclass = pdf_custom_meta.get("documentclass") or raw_meta.get("documentclass") or "scrartcl"
    if chosen_documentclass not in _SUPPORTED_DOCUMENT_CLASSES:
        raise UnsupportedDocumentClassError(f"Document class '{chosen_documentclass}' is not supported.")

    # 4. Mandatory F-02 Include Consolidation & Isolation
    staged_header_includes: List[str] = []

    # 4a. Collect root header-includes
    if "header-includes" in raw_meta:
        staged_header_includes.extend(_extract_header_includes(raw_meta["header-includes"]))

    # 4b. Collect format.pdf header-includes
    if "header-includes" in pdf_custom_meta:
        staged_header_includes.extend(_extract_header_includes(pdf_custom_meta["header-includes"]))

    # 4c. Collect inline include-in-header (strictly after header-includes)
    inline_headers_root = _extract_include_items(raw_meta.get("include-in-header"), "include-in-header")
    for item in inline_headers_root:
        staged_header_includes.append(item["text"])

    inline_headers_pdf = _extract_include_items(pdf_custom_meta.get("include-in-header"), "include-in-header")
    for item in inline_headers_pdf:
        staged_header_includes.append(item["text"])

    # 4d. Body includes remain strictly isolated
    staged_before_body: List[Dict[str, str]] = []
    if "include-before-body" in pdf_custom_meta:
        staged_before_body.extend(
            _extract_include_items(pdf_custom_meta["include-before-body"], "include-before-body")
        )

    staged_after_body: List[Dict[str, str]] = []
    if "include-after-body" in pdf_custom_meta:
        staged_after_body.extend(
            _extract_include_items(pdf_custom_meta["include-after-body"], "include-after-body")
        )

    # 5. Preflight package compatibility verification on combined validation set
    validation_snippets: List[str] = list(staged_header_includes)
    validation_snippets.extend(item["text"] for item in staged_before_body)
    validation_snippets.extend(item["text"] for item in staged_after_body)
    _validate_package_compatibility(validation_snippets)

    # 6. Assemble normalized staged metadata
    staged_meta: Dict[str, Any] = {}

    # Copy allowed root fields (omitting format and include fields handled specially)
    for k, v in raw_meta.items():
        if k in {"format", "header-includes", "include-in-header", "documentclass"} or v is None:
            continue
        staged_meta[k] = v

    # Ensure title
    if not staged_meta.get("title"):
        staged_meta["title"] = default_title

    # Assemble format.pdf
    staged_pdf: Dict[str, Any] = {
        "documentclass": chosen_documentclass,
        "include-in-header": [{"file": "polpo_preamble.tex"}],
    }

    # Copy allowed format.pdf fields
    for k, v in pdf_custom_meta.items():
        if k in {
            "documentclass",
            "header-includes",
            "include-in-header",
            "include-before-body",
            "include-after-body",
        } or v is None:
            continue
        staged_pdf[k] = v

    if staged_header_includes:
        staged_pdf["header-includes"] = staged_header_includes

    if staged_before_body:
        staged_pdf["include-before-body"] = staged_before_body

    if staged_after_body:
        staged_pdf["include-after-body"] = staged_after_body

    staged_meta["format"] = {"pdf": staged_pdf}

    return staged_meta
