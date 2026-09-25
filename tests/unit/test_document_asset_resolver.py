# ============================================================
#  tests/unit/test_document_asset_resolver.py
# ============================================================

import os
from pathlib import Path
import pytest
from unittest.mock import patch
import urllib.request

from application.services.document_asset_resolver import (
    DocumentAssetResolver,
    resolve_document_assets,
    ResolvedAssetSet,
    AssetContainmentError,
    AssetNotFoundError,
    AssetAccessError,
)



def test_one_local_file_uri_resolves(tmp_path: Path):
    """A single local file:/// URI resolves to an exported asset and rewrites markdown."""
    job_dir = tmp_path / "artifacts" / "job_42"
    job_dir.mkdir(parents=True)
    crop_file = job_dir / "crop_1.jpg"
    crop_data = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"image data"
    crop_file.write_bytes(crop_data)

    uri = crop_file.as_uri()
    markdown = f'# Document\n\n![Crop]({uri} "polpo:region=1;occ=1")\n'

    result = resolve_document_assets(
        job_id=42,
        canonical_markdown=markdown,
        job_artifacts_dir=job_dir,
    )

    assert isinstance(result, ResolvedAssetSet)
    assert len(result.assets) == 1
    asset = result.assets[0]
    assert asset.original_reference == uri
    assert asset.source_path == crop_file.resolve()
    assert asset.relative_dest_path == "assets/crop_1.jpg"
    assert asset.size_bytes == len(crop_data)
    assert asset.mime_type == "image/jpeg"
    assert len(asset.sha256) == 64

    expected_md = '# Document\n\n![Crop](assets/crop_1.jpg "polpo:region=1;occ=1")\n'
    assert result.resolved_markdown == expected_md


def test_local_reference_without_file_scheme_resolves(tmp_path: Path):
    """Direct relative or absolute filesystem path within job boundary resolves."""
    job_dir = tmp_path / "job_10"
    job_dir.mkdir(parents=True)
    img_file = job_dir / "figure.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nfake png")

    # Direct relative path reference
    markdown = "![Diagram](figure.png)"
    result = resolve_document_assets(
        job_id=10,
        canonical_markdown=markdown,
        job_artifacts_dir=job_dir,
    )

    assert len(result.assets) == 1
    assert result.assets[0].relative_dest_path == "assets/figure.png"
    assert result.resolved_markdown == "![Diagram](assets/figure.png)"


def test_repeated_source_reference_resolves_once(tmp_path: Path):
    """Identical source reference appearing multiple times resolves once and rewrites all occurrences."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)
    img_file = job_dir / "shared.png"
    img_file.write_bytes(b"content")
    uri = img_file.as_uri()

    markdown = f"![First]({uri})\n\nSome text\n\n![Second]({uri})\n"
    result = resolve_document_assets(
        job_id=1,
        canonical_markdown=markdown,
        job_artifacts_dir=job_dir,
    )

    assert len(result.assets) == 1
    assert result.assets[0].relative_dest_path == "assets/shared.png"
    assert result.resolved_markdown == "![First](assets/shared.png)\n\nSome text\n\n![Second](assets/shared.png)\n"


def test_deduplication_same_basename_same_content(tmp_path: Path):
    """Two distinct files in different subdirectories with identical basename and content share one asset."""
    job_dir = tmp_path / "job_1"
    sub_dir = job_dir / "sub"
    sub_dir.mkdir(parents=True)

    file_a = job_dir / "photo.jpg"
    file_b = sub_dir / "photo.jpg"
    content = b"same photo content"
    file_a.write_bytes(content)
    file_b.write_bytes(content)

    uri_a = file_a.as_uri()
    uri_b = file_b.as_uri()

    markdown = f"![A]({uri_a})\n\n![B]({uri_b})"
    result = resolve_document_assets(
        job_id=1,
        canonical_markdown=markdown,
        job_artifacts_dir=job_dir,
    )

    assert len(result.assets) == 1
    assert result.assets[0].relative_dest_path == "assets/photo.jpg"
    assert result.resolved_markdown == "![A](assets/photo.jpg)\n\n![B](assets/photo.jpg)"


def test_collision_same_basename_different_content(tmp_path: Path):
    """Two files with same basename but different content receive deterministic collision names."""
    job_dir = tmp_path / "job_1"
    sub_dir = job_dir / "sub"
    sub_dir.mkdir(parents=True)

    file_a = job_dir / "figure.png"
    file_b = sub_dir / "figure.png"
    file_a.write_bytes(b"content A")
    file_b.write_bytes(b"content B")

    uri_a = file_a.as_uri()
    uri_b = file_b.as_uri()

    markdown = f"![A]({uri_a})\n\n![B]({uri_b})"
    result = resolve_document_assets(
        job_id=1,
        canonical_markdown=markdown,
        job_artifacts_dir=job_dir,
    )

    assert len(result.assets) == 2
    asset_a = result.assets[0]
    asset_b = result.assets[1]

    assert asset_a.relative_dest_path == "assets/figure.png"
    expected_b_name = f"assets/figure_{asset_b.sha256[:8]}.png"
    assert asset_b.relative_dest_path == expected_b_name
    assert result.resolved_markdown == f"![A](assets/figure.png)\n\n![B]({expected_b_name})"


def test_security_asset_outside_boundary_rejected(tmp_path: Path):
    """An asset reference pointing outside the job directory is rejected."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True)
    outside_file = outside_dir / "secret.png"
    outside_file.write_bytes(b"secret")

    markdown = f"![Exploit]({outside_file.as_uri()})"

    with pytest.raises(AssetContainmentError):
        resolve_document_assets(
            job_id=1,
            canonical_markdown=markdown,
            job_artifacts_dir=job_dir,
        )


def test_security_symlink_escaping_boundary_rejected(tmp_path: Path):
    """A symlink located inside the job directory pointing outside is rejected."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)

    secret_file = tmp_path / "secret.txt"
    secret_file.write_bytes(b"confidential")

    symlink_file = job_dir / "symlink_crop.jpg"
    try:
        os.symlink(secret_file, symlink_file)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported in this environment")

    markdown = f"![Symlink]({symlink_file.as_uri()})"

    with pytest.raises(AssetContainmentError):
        resolve_document_assets(
            job_id=1,
            canonical_markdown=markdown,
            job_artifacts_dir=job_dir,
        )


def test_security_parent_traversal_rejected(tmp_path: Path):
    """A relative path containing '..' escaping the job directory is rejected."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)

    secret_file = tmp_path / "secret.png"
    secret_file.write_bytes(b"secret")

    markdown = "![Traversal](../secret.png)"

    with pytest.raises(AssetContainmentError):
        resolve_document_assets(
            job_id=1,
            canonical_markdown=markdown,
            job_artifacts_dir=job_dir,
        )


def test_security_directory_reference_rejected(tmp_path: Path):
    """An asset reference resolving to a directory instead of a regular file is rejected."""
    job_dir = tmp_path / "job_1"
    sub_dir = job_dir / "subfolder"
    sub_dir.mkdir(parents=True)

    markdown = f"![Dir]({sub_dir.as_uri()})"

    with pytest.raises(AssetAccessError):
        resolve_document_assets(
            job_id=1,
            canonical_markdown=markdown,
            job_artifacts_dir=job_dir,
        )


def test_missing_asset_rejected(tmp_path: Path):
    """A referenced local asset that does not exist raises AssetNotFoundError."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)

    missing_uri = (job_dir / "missing.png").as_uri()
    markdown = f"![Missing]({missing_uri})"

    with pytest.raises(AssetNotFoundError):
        resolve_document_assets(
            job_id=1,
            canonical_markdown=markdown,
            job_artifacts_dir=job_dir,
        )


def test_external_http_preserved_no_network(tmp_path: Path):
    """HTTP references do not trigger network calls and remain unchanged."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)

    markdown = "![Logo](http://example.com/logo.png)"

    with patch.object(urllib.request, "urlopen") as mock_http:
        result = resolve_document_assets(
            job_id=1,
            canonical_markdown=markdown,
            job_artifacts_dir=job_dir,
        )
        assert mock_http.call_count == 0

    assert len(result.assets) == 0
    assert result.resolved_markdown == markdown


def test_external_https_preserved_no_network(tmp_path: Path):
    """HTTPS references do not trigger network calls and remain unchanged."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)

    markdown = "![Logo](https://example.com/logo.png)"

    with patch.object(urllib.request, "urlopen") as mock_http:
        result = resolve_document_assets(
            job_id=1,
            canonical_markdown=markdown,
            job_artifacts_dir=job_dir,
        )
        assert mock_http.call_count == 0

    assert len(result.assets) == 0
    assert result.resolved_markdown == markdown


def test_unsupported_custom_scheme_preserved_no_network(tmp_path: Path):
    """Unsupported custom schemes (e.g. ftp://, data:, custom://) remain unmapped and trigger no I/O."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)

    markdown = (
        "![FTP](ftp://example.com/file.png)\n\n"
        "![Data](data:image/png;base64,iVBORw0KGgo=)\n\n"
        "![Custom](custom://asset/42)\n"
    )

    result = resolve_document_assets(
        job_id=1,
        canonical_markdown=markdown,
        job_artifacts_dir=job_dir,
    )

    assert len(result.assets) == 0
    assert result.resolved_markdown == markdown


def test_markdown_preservation_comprehensive(tmp_path: Path):
    """
    Comprehensive test verifying that in a full document with mixed references,
    titles, Polpo metadata, math, code blocks, inline code, comments, and Persian text,
    only valid local image destination spans are rewritten.
    """
    job_dir = tmp_path / "job_42"
    job_dir.mkdir(parents=True)

    crop_1 = job_dir / "crop_1.jpg"
    crop_1.write_bytes(b"crop 1 data")
    crop_2 = job_dir / "crop_2.png"
    crop_2.write_bytes(b"crop 2 data")

    uri_1 = crop_1.as_uri()
    uri_2 = crop_2.as_uri()

    canonical_markdown = (
        "<!-- Page 1 -->\n"
        "# گزارش فنی\n\n"
        f'![نمودار معماری]({uri_1} "polpo:region=r1;occ=o1")\n\n'
        "$$E = mc^2$$\n\n"
        "```python\n"
        f'code_img = "![fake]({uri_1})"\n'
        "```\n\n"
        f"کد درون‌خطی: `![inline]({uri_2})`\n\n"
        "![لوگو](https://polpot.local/logo.svg)\n\n"
        f'![تصویر دوم](<{uri_2}> "polpo:region=r2;occ=o2")\n'
    )

    result = resolve_document_assets(
        job_id=42,
        canonical_markdown=canonical_markdown,
        job_artifacts_dir=job_dir,
    )

    assert len(result.assets) == 2

    expected_markdown = (
        "<!-- Page 1 -->\n"
        "# گزارش فنی\n\n"
        '![نمودار معماری](assets/crop_1.jpg "polpo:region=r1;occ=o1")\n\n'
        "$$E = mc^2$$\n\n"
        "```python\n"
        f'code_img = "![fake]({uri_1})"\n'
        "```\n\n"
        f"کد درون‌خطی: `![inline]({uri_2})`\n\n"
        "![لوگو](https://polpot.local/logo.svg)\n\n"
        '![تصویر دوم](<assets/crop_2.png> "polpo:region=r2;occ=o2")\n'
    )

    assert result.resolved_markdown == expected_markdown


def test_determinism_identical_input_identical_output(tmp_path: Path):
    """Running resolution multiple times with identical inputs yields identical results and asset order."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)

    img_a = job_dir / "a.png"
    img_b = job_dir / "b.png"
    img_a.write_bytes(b"content a")
    img_b.write_bytes(b"content b")

    markdown = f"![A]({img_a.as_uri()})\n\n![B]({img_b.as_uri()})"

    res1 = resolve_document_assets(job_id=1, canonical_markdown=markdown, job_artifacts_dir=job_dir)
    res2 = resolve_document_assets(job_id=1, canonical_markdown=markdown, job_artifacts_dir=job_dir)

    assert res1.resolved_markdown == res2.resolved_markdown
    assert len(res1.assets) == len(res2.assets)
    for a1, a2 in zip(res1.assets, res2.assets):
        assert a1.relative_dest_path == a2.relative_dest_path
        assert a1.sha256 == a2.sha256
        assert a1.size_bytes == a2.size_bytes


def test_no_absolute_host_path_in_portable_projection(tmp_path: Path):
    """The resolved markdown and asset relative destinations contain no absolute host paths."""
    job_dir = tmp_path / "secret_host_directory" / "job_1"
    job_dir.mkdir(parents=True)
    img = job_dir / "fig.png"
    img.write_bytes(b"png")

    markdown = f"![Fig]({img.as_uri()})"
    result = resolve_document_assets(job_id=1, canonical_markdown=markdown, job_artifacts_dir=job_dir)

    assert str(tmp_path) not in result.resolved_markdown
    assert str(job_dir) not in result.resolved_markdown
    for asset in result.assets:
        assert not asset.relative_dest_path.startswith("/")
        assert not asset.relative_dest_path.startswith("\\")
        assert "secret_host_directory" not in asset.relative_dest_path


def test_windows_reserved_names_sanitized(tmp_path: Path):
    """Windows reserved device names (e.g. CON.png, NUL.jpg) are sanitized portably."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)
    con_file = job_dir / "con.png"
    con_file.write_bytes(b"data")

    markdown = f"![Reserved]({con_file.as_uri()})"
    result = resolve_document_assets(job_id=1, canonical_markdown=markdown, job_artifacts_dir=job_dir)

    assert len(result.assets) == 1
    dest = result.assets[0].relative_dest_path
    assert dest != "assets/con.png"
    assert "con" in dest.lower()
    assert dest.startswith("assets/")


def test_filename_with_unsafe_characters_sanitized(tmp_path: Path):
    """Filenames with characters invalid on Windows/Linux/macOS are sanitized."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)
    # create a file with standard name, but reference with weird characters or spaces
    img = job_dir / "figure 1.png"
    img.write_bytes(b"data")

    markdown = f"![Safe]({img.as_uri()})"
    result = resolve_document_assets(job_id=1, canonical_markdown=markdown, job_artifacts_dir=job_dir)

    assert len(result.assets) == 1
    assert result.assets[0].relative_dest_path == "assets/figure 1.png"


def test_resolver_class_interface_with_artifacts_dir(tmp_path: Path):
    """DocumentAssetResolver can be instantiated with artifacts_dir and resolves by job_id."""
    artifacts_dir = tmp_path / "artifacts"
    job_dir = artifacts_dir / "job_99"
    job_dir.mkdir(parents=True)
    img = job_dir / "diagram.png"
    img.write_bytes(b"diagram")

    resolver = DocumentAssetResolver(artifacts_dir=artifacts_dir)
    result = resolver.resolve_document_assets(
        job_id=99,
        canonical_markdown=f"![Diagram]({img.as_uri()})",
    )

    assert len(result.assets) == 1
    assert result.assets[0].relative_dest_path == "assets/diagram.png"
    assert result.resolved_markdown == "![Diagram](assets/diagram.png)"


def test_unreadable_file_raises_access_error(tmp_path: Path):
    """An unreadable local asset file raises AssetAccessError."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)
    unreadable_file = job_dir / "unreadable.png"
    unreadable_file.write_bytes(b"data")

    # Simulate permission error
    with patch("application.services.document_asset_resolver.compute_file_sha256", side_effect=PermissionError("Permission denied")):
        with pytest.raises(AssetAccessError):
            resolve_document_assets(
                job_id=1,
                canonical_markdown=f"![Unreadable]({unreadable_file.as_uri()})",
                job_artifacts_dir=job_dir,
            )


def test_case_collision_different_content(tmp_path: Path):
    """Case-colliding basenames with different content receive distinct deterministic names."""
    job_dir = tmp_path / "job_1"
    sub_dir = job_dir / "sub"
    sub_dir.mkdir(parents=True)

    file_a = job_dir / "figure.png"
    file_b = sub_dir / "FIGURE.png"
    file_a.write_bytes(b"content 1")
    file_b.write_bytes(b"content 2")

    markdown = f"![1]({file_a.as_uri()})\n\n![2]({file_b.as_uri()})"
    result = resolve_document_assets(job_id=1, canonical_markdown=markdown, job_artifacts_dir=job_dir)

    assert len(result.assets) == 2
    names = [a.relative_dest_path.lower() for a in result.assets]
    assert names[0] != names[1]


def test_empty_markdown_or_no_images(tmp_path: Path):
    """Empty markdown or markdown without image tokens returns immediately with empty asset list."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)

    res_empty = resolve_document_assets(job_id=1, canonical_markdown="", job_artifacts_dir=job_dir)
    assert res_empty.resolved_markdown == ""
    assert res_empty.assets == ()
    assert isinstance(res_empty.assets, tuple)
    assert len(res_empty.uri_mapping) == 0

    text_no_img = "# Just a header\n\nParagraph with no images.\n"
    res_no_img = resolve_document_assets(job_id=1, canonical_markdown=text_no_img, job_artifacts_dir=job_dir)
    assert res_no_img.resolved_markdown == text_no_img
    assert res_no_img.assets == ()
    assert isinstance(res_no_img.assets, tuple)
    assert len(res_no_img.uri_mapping) == 0



def test_distinct_reference_spellings_to_same_file(tmp_path: Path):
    """Different reference spellings pointing to the same file resolve to a single asset and both rewrite."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)
    img = job_dir / "plot.png"
    img.write_bytes(b"plot data")

    uri = img.as_uri()
    rel = "plot.png"

    markdown = f"![Absolute]({uri})\n\n![Relative]({rel})\n\n![Repeated Absolute]({uri})\n"
    result = resolve_document_assets(job_id=1, canonical_markdown=markdown, job_artifacts_dir=job_dir)

    # Assets list has exactly 1 unique asset and is an immutable tuple
    assert len(result.assets) == 1
    assert isinstance(result.assets, tuple)
    assert result.assets[0].relative_dest_path == "assets/plot.png"
    assert result.resolved_markdown == "![Absolute](assets/plot.png)\n\n![Relative](assets/plot.png)\n\n![Repeated Absolute](assets/plot.png)\n"

    # uri_mapping must be complete: contain both 'uri' and 'rel' spellings
    assert result.uri_mapping[uri] == "assets/plot.png"
    assert result.uri_mapping[rel] == "assets/plot.png"
    assert len(result.uri_mapping) == 2

    # uri_mapping must be immutable
    with pytest.raises((TypeError, AttributeError)):
        result.uri_mapping["new_key"] = "assets/new.png"  # type: ignore[index]



def test_percent_encoded_local_uri_resolves(tmp_path: Path):
    """Percent-encoded file URI resolves to disk path with spaces and rewrites correctly."""
    job_dir = tmp_path / "job_1"
    job_dir.mkdir(parents=True)
    img = job_dir / "my chart.png"
    img.write_bytes(b"chart data")

    # Construct percent-encoded URI (e.g. .../my%20chart.png)
    uri = img.as_uri()
    assert "%20" in uri

    markdown = f"![Chart]({uri})"
    result = resolve_document_assets(job_id=1, canonical_markdown=markdown, job_artifacts_dir=job_dir)

    assert len(result.assets) == 1
    assert result.assets[0].relative_dest_path == "assets/my chart.png"
    assert result.resolved_markdown == "![Chart](assets/my chart.png)"
