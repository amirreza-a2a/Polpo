"""Unit tests for pure Markdown asset reference rewriter (TICK-EXP-1)."""

from core.entities.artifact import ArtifactType


def test_document_package_zip_artifact_type_exists():
    """TICK-EXP-1: ArtifactType.DOCUMENT_PACKAGE_ZIP is registered without altering ATTACHMENTS_ZIP."""
    assert ArtifactType.DOCUMENT_PACKAGE_ZIP == "document_package_zip"
    assert ArtifactType.ATTACHMENTS_ZIP == "attachments_zip"


def test_rewrite_asset_references_empty_mapping_returns_unchanged():
    """Empty mapping returns the input text character-for-character unchanged."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = "# Title\n\n![Figure](file:///artifacts/crop.jpg \"polpo:region=1;occ=2\")\n"
    assert rewrite_asset_references(text, {}) == text


def test_rewrite_single_mapped_local_image():
    """Rewrites destination URI of a single mapped local image while preserving alt, title, and structure."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = "Intro\n\n![Figure 1](file:///artifacts/job_1/crop_a.jpg \"polpo:region=11111111-1111-4111-8111-111111111111;occ=22222222-2222-4222-8222-222222222222\")\n\nOutro"
    mapping = {"file:///artifacts/job_1/crop_a.jpg": "assets/crop_a.jpg"}
    expected = "Intro\n\n![Figure 1](assets/crop_a.jpg \"polpo:region=11111111-1111-4111-8111-111111111111;occ=22222222-2222-4222-8222-222222222222\")\n\nOutro"

    assert rewrite_asset_references(text, mapping) == expected


def test_rewrite_multiple_mapped_images():
    """Rewrites multiple distinct mapped images across lines."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = (
        "# Document\n\n"
        "![Image 1](file:///path/crop_1.jpg)\n\n"
        "Paragraph text.\n\n"
        "![Image 2](file:///path/crop_2.jpg \"title 2\")\n"
    )
    mapping = {
        "file:///path/crop_1.jpg": "assets/crop_1.jpg",
        "file:///path/crop_2.jpg": "assets/crop_2.jpg",
    }
    expected = (
        "# Document\n\n"
        "![Image 1](assets/crop_1.jpg)\n\n"
        "Paragraph text.\n\n"
        "![Image 2](assets/crop_2.jpg \"title 2\")\n"
    )
    assert rewrite_asset_references(text, mapping) == expected


def test_unmapped_images_and_external_http_preserved():
    """Preserves unmapped local images and external HTTP/HTTPS references character-for-character."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = (
        "![Mapped](file:///local/mapped.jpg)\n"
        "![Web](https://example.com/logo.png)\n"
        "![Unmapped Local](file:///local/unmapped.jpg)\n"
    )
    mapping = {"file:///local/mapped.jpg": "assets/mapped.jpg"}
    expected = (
        "![Mapped](assets/mapped.jpg)\n"
        "![Web](https://example.com/logo.png)\n"
        "![Unmapped Local](file:///local/unmapped.jpg)\n"
    )
    assert rewrite_asset_references(text, mapping) == expected


def test_multiple_occurrences_of_same_source_uri_all_map_to_one_destination():
    """Multiple occurrences of the same source URI are all rewritten to the same destination."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = (
        "Occurrence 1: ![Fig](file:///local/crop.jpg \"polpo:region=r1;occ=o1\")\n"
        "Occurrence 2: ![Fig Repeat](file:///local/crop.jpg \"polpo:region=r1;occ=o2\")\n"
    )
    mapping = {"file:///local/crop.jpg": "assets/crop.jpg"}
    expected = (
        "Occurrence 1: ![Fig](assets/crop.jpg \"polpo:region=r1;occ=o1\")\n"
        "Occurrence 2: ![Fig Repeat](assets/crop.jpg \"polpo:region=r1;occ=o2\")\n"
    )
    assert rewrite_asset_references(text, mapping) == expected


def test_already_relative_unmapped_path_is_preserved():
    """An already-relative path not in the mapping is preserved unchanged."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = "![Icon](images/icon.png)"
    assert rewrite_asset_references(text, {"other.png": "assets/other.png"}) == text


def test_inline_code_image_like_text_is_unchanged():
    """Image-like syntax inside inline backtick code spans must not be rewritten."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = "Use `![Alt](file:///local/pic.jpg)` to display image."
    mapping = {"file:///local/pic.jpg": "assets/pic.jpg"}
    assert rewrite_asset_references(text, mapping) == text


def test_fenced_code_image_like_text_is_unchanged():
    """Image-like syntax inside fenced code blocks must not be rewritten."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = (
        "Before\n\n"
        "```markdown\n"
        "![Alt](file:///local/pic.jpg)\n"
        "```\n\n"
        "After\n"
    )
    mapping = {"file:///local/pic.jpg": "assets/pic.jpg"}
    assert rewrite_asset_references(text, mapping) == text


def test_tilde_fenced_code_image_like_text_is_unchanged():
    """Image-like syntax inside tilde fenced code blocks must not be rewritten."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = (
        "~~~\n"
        "![Alt](file:///local/pic.jpg)\n"
        "~~~\n"
    )
    mapping = {"file:///local/pic.jpg": "assets/pic.jpg"}
    assert rewrite_asset_references(text, mapping) == text


def test_indented_code_block_image_like_text_is_unchanged():
    """Image-like syntax inside 4-space indented code blocks must not be rewritten."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = (
        "Paragraph:\n\n"
        "    ![Alt](file:///local/pic.jpg)\n\n"
        "Next paragraph."
    )
    mapping = {"file:///local/pic.jpg": "assets/pic.jpg"}
    assert rewrite_asset_references(text, mapping) == text


def test_html_comment_image_like_text_is_unchanged():
    """Image-like syntax inside HTML comments must not be rewritten."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = "<!-- ![Commented Image](file:///local/pic.jpg) -->\nVisible text"
    mapping = {"file:///local/pic.jpg": "assets/pic.jpg"}
    assert rewrite_asset_references(text, mapping) == text


def test_unicode_persian_and_math_character_preservation():
    """Preserves Persian/Farsi text, mathematical LaTeX spans, and Unicode alt text."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = (
        "# گزارش تحلیلی فرمول‌های ریاضی\n\n"
        "فرمول انرژی: $E = mc^2$ و ماتریس:\n\n"
        "$$\n"
        "\\begin{matrix} a & b \\\\ c & d \\end{matrix}\n"
        "$$\n\n"
        "تصویر ۱: ![نمودار همگرایی و رشد تابع](file:///artifacts/plot_1.png \"عنوان نمودار\")\n"
    )
    mapping = {"file:///artifacts/plot_1.png": "assets/plot_1.png"}
    expected = (
        "# گزارش تحلیلی فرمول‌های ریاضی\n\n"
        "فرمول انرژی: $E = mc^2$ و ماتریس:\n\n"
        "$$\n"
        "\\begin{matrix} a & b \\\\ c & d \\end{matrix}\n"
        "$$\n\n"
        "تصویر ۱: ![نمودار همگرایی و رشد تابع](assets/plot_1.png \"عنوان نمودار\")\n"
    )
    assert rewrite_asset_references(text, mapping) == expected


def test_escaped_brackets_in_alt_text():
    """Handles escaped opening and closing brackets inside alt text."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = "![Figure \\[A\\] and \\[B\\]](file:///local/img.png)"
    mapping = {"file:///local/img.png": "assets/img.png"}
    expected = "![Figure \\[A\\] and \\[B\\]](assets/img.png)"
    assert rewrite_asset_references(text, mapping) == expected


def test_escaped_exclamation_mark_is_not_an_image():
    """An escaped exclamation mark '\\![' is not an image token and must remain unchanged."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = r"\![escaped](file:///local/img.png)"
    mapping = {"file:///local/img.png": "assets/img.png"}
    assert rewrite_asset_references(text, mapping) == text


def test_angle_bracket_destination_preserved():
    """Angle-bracketed destinations '<uri>' have their destination replaced within angle brackets."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = '![Figure](<file:///local/img.png> "title")'
    mapping = {"file:///local/img.png": "assets/img.png"}
    expected = '![Figure](<assets/img.png> "title")'
    assert rewrite_asset_references(text, mapping) == expected


def test_angle_bracket_destination_escaped_characters_remain_raw():
    """
    Angle-bracket destinations with escaped characters remain raw in AssetReference.destination
    and are rewritten correctly without delimiter loss or unescaping mismatch.
    """
    from core.markdown.asset_rewriter import scan_asset_references, rewrite_asset_references

    raw_dest = r"file:///path/a\>b\(1\).png"
    text = f"Intro\n\n![Figure](<{raw_dest}>)\n\nOutro"

    refs = scan_asset_references(text)
    assert len(refs) == 1
    assert refs[0].is_angle_bracketed is True
    assert refs[0].destination == raw_dest

    mapping = {raw_dest: "assets/a_b_1.png"}
    rewritten = rewrite_asset_references(text, mapping)
    expected = "Intro\n\n![Figure](<assets/a_b_1.png>)\n\nOutro"
    assert rewritten == expected


def test_standard_destination_escaped_parentheses_remain_raw():
    """
    Standard destination with escaped parentheses remains raw in AssetReference.destination
    and is rewritten correctly matching source spelling.
    """
    from core.markdown.asset_rewriter import scan_asset_references, rewrite_asset_references

    raw_dest = r"file:///path/a\(1\).png"
    text = f'![Figure]({raw_dest} "title")'

    refs = scan_asset_references(text)
    assert len(refs) == 1
    assert refs[0].is_angle_bracketed is False
    assert refs[0].destination == raw_dest

    mapping = {raw_dest: "assets/a1.png"}
    rewritten = rewrite_asset_references(text, mapping)
    assert rewritten == '![Figure](assets/a1.png "title")'


def test_whitespace_formatting_around_destination_preserved():
    """Preserves intra-parenthesis whitespace around the destination URI exactly."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = '![Figure](   file:///local/img.png   "title"   )'
    mapping = {"file:///local/img.png": "assets/img.png"}
    expected = '![Figure](   assets/img.png   "title"   )'
    assert rewrite_asset_references(text, mapping) == expected


def test_single_quoted_title_preserved():
    """Preserves single-quoted image title attributes."""
    from core.markdown.asset_rewriter import rewrite_asset_references

    text = "![Figure](file:///local/img.png 'single title')"
    mapping = {"file:///local/img.png": "assets/img.png"}
    expected = "![Figure](assets/img.png 'single title')"
    assert rewrite_asset_references(text, mapping) == expected


def test_regression_canonical_markdown_only_destinations_change():
    """
    Comprehensive regression test proving that in a full canonical document,
    every structural element is character-for-character identical except the mapped destinations.
    """
    from core.markdown.asset_rewriter import rewrite_asset_references

    canonical_doc = (
        "<!-- Page 1 -->\n"
        "# Executive Summary\n\n"
        "Here is the primary workflow diagram:\n\n"
        '![Architecture Diagram](file:///artifacts/job_42/crop_11111111-1111-4111-8111-111111111111_v1.jpg "polpo:region=11111111-1111-4111-8111-111111111111;occ=22222222-2222-4222-8222-222222222222")\n\n'
        "> Important note on formula:\n"
        "> $$\\int_{0}^{\\infty} e^{-x^2} dx = \\frac{\\sqrt{\\pi}}{2}$$\n\n"
        "Code reference:\n"
        "```python\n"
        "# Do not rewrite this:\n"
        'img = "![fake](file:///artifacts/job_42/crop_11111111-1111-4111-8111-111111111111_v1.jpg)"\n'
        "```\n\n"
        "Inline mention: `![inline](file:///artifacts/job_42/crop_22222222-2222-4222-8222-222222222222_v2.jpg)` is code.\n\n"
        "Second occurrence of first region:\n"
        '![Diagram Revisit](file:///artifacts/job_42/crop_11111111-1111-4111-8111-111111111111_v1.jpg "polpo:region=11111111-1111-4111-8111-111111111111;occ=33333333-3333-4333-8333-333333333333")\n\n'
        "External reference:\n"
        "![Logo](https://polpot.local/images/logo.png)\n\n"
        "<!-- Non-polpo comment with ![nested](file:///artifacts/job_42/ignored.jpg) -->\n"
        "Conclusion.\n"
    )

    mapping = {
        "file:///artifacts/job_42/crop_11111111-1111-4111-8111-111111111111_v1.jpg": "assets/crop_11111111-1111-4111-8111-111111111111_v1.jpg",
        "file:///artifacts/job_42/crop_22222222-2222-4222-8222-222222222222_v2.jpg": "assets/crop_22222222-2222-4222-8222-222222222222_v2.jpg",
    }

    expected_doc = (
        "<!-- Page 1 -->\n"
        "# Executive Summary\n\n"
        "Here is the primary workflow diagram:\n\n"
        '![Architecture Diagram](assets/crop_11111111-1111-4111-8111-111111111111_v1.jpg "polpo:region=11111111-1111-4111-8111-111111111111;occ=22222222-2222-4222-8222-222222222222")\n\n'
        "> Important note on formula:\n"
        "> $$\\int_{0}^{\\infty} e^{-x^2} dx = \\frac{\\sqrt{\\pi}}{2}$$\n\n"
        "Code reference:\n"
        "```python\n"
        "# Do not rewrite this:\n"
        'img = "![fake](file:///artifacts/job_42/crop_11111111-1111-4111-8111-111111111111_v1.jpg)"\n'
        "```\n\n"
        "Inline mention: `![inline](file:///artifacts/job_42/crop_22222222-2222-4222-8222-222222222222_v2.jpg)` is code.\n\n"
        "Second occurrence of first region:\n"
        '![Diagram Revisit](assets/crop_11111111-1111-4111-8111-111111111111_v1.jpg "polpo:region=11111111-1111-4111-8111-111111111111;occ=33333333-3333-4333-8333-333333333333")\n\n'
        "External reference:\n"
        "![Logo](https://polpot.local/images/logo.png)\n\n"
        "<!-- Non-polpo comment with ![nested](file:///artifacts/job_42/ignored.jpg) -->\n"
        "Conclusion.\n"
    )

    rewritten = rewrite_asset_references(canonical_doc, mapping)
    assert rewritten == expected_doc


def test_core_markdown_package_exports():
    """Verifies that AssetReference, scan_asset_references, and rewrite_asset_references are exported by core.markdown."""
    import core.markdown as md

    assert hasattr(md, "AssetReference")
    assert hasattr(md, "scan_asset_references")
    assert hasattr(md, "rewrite_asset_references")


def test_visual_token_mutator_opaque_helpers_exported():
    """Verifies that find_opaque_spans and is_opaque_span are public in visual_token_mutator."""
    import core.markdown.visual_token_mutator as vtm

    assert callable(vtm.find_opaque_spans)
    assert callable(vtm.is_opaque_span)
