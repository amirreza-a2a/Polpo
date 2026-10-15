# ============================================================
#  tests/unit/test_staging_workspace.py
#  Unit tests for Quarto PDF Isolated Staging Workspace Manager
#  Phase 13 — Ticket #100
# ============================================================

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from application.services.document_asset_resolver import (
    AssetAccessError,
    DocumentAssetResolver,
)
from infrastructure.pdf.staging_workspace import (
    StagingCleanupError,
    StagingSession,
    StagingWorkspaceManager,
)


def test_staging_creates_and_cleans_workspace(tmp_path: Path):
    """
    Staging session creates an isolated directory under <job_artifacts_dir>/.pdf_staging/
    and deterministically removes it on context exit.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_1"
    job_artifacts_dir.mkdir(parents=True)

    markdown = "# Section 1\n\nHello from canonical markdown."
    raw_meta = {"title": "Isolated Document"}
    resolver = DocumentAssetResolver()
    manager = StagingWorkspaceManager()

    staging_dir_captured = None

    with manager.create_session(
        job_artifacts_dir=job_artifacts_dir,
        job_id=1,
        raw_metadata=raw_meta,
        canonical_markdown=markdown,
        asset_resolver=resolver,
    ) as session:
        assert isinstance(session, StagingSession)
        staging_dir_captured = session.staging_dir

        # Workspace allocation verification
        assert session.staging_dir.exists()
        assert session.staging_dir.is_dir()
        assert session.staging_dir.parent == job_artifacts_dir / ".pdf_staging"
        assert session.staging_dir.name.startswith("1_")

        # Session contract paths
        assert session.qmd_path.is_file()
        assert session.qmd_path == session.staging_dir / "document.qmd"
        assert session.preamble_path.is_file()
        assert session.preamble_path == session.staging_dir / "polpo_preamble.tex"
        assert session.quarto_yml_path.is_file()
        assert session.quarto_yml_path == session.staging_dir / "_quarto.yml"
        assert session.staged_asset_count == 0

    # Deterministic cleanup verification
    assert not staging_dir_captured.exists()
    assert not (job_artifacts_dir / ".pdf_staging").exists()


def test_staging_writes_project_barrier(tmp_path: Path):
    """
    Staging directory must contain an application-owned _quarto.yml barrier
    with project: {type: default} and XeLaTeX restricted engine options.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_2"
    job_artifacts_dir.mkdir(parents=True)

    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    with manager.create_session(
        job_artifacts_dir=job_artifacts_dir,
        job_id=2,
        raw_metadata={},
        canonical_markdown="Sample text",
        asset_resolver=resolver,
    ) as session:
        quarto_yml_content = session.quarto_yml_path.read_text(encoding="utf-8")
        assert "project:" in quarto_yml_content
        assert "type: default" in quarto_yml_content
        assert "format:" in quarto_yml_content
        assert "pdf:" in quarto_yml_content
        assert "pdf-engine: xelatex" in quarto_yml_content
        assert "-no-shell-escape" in quarto_yml_content
        assert "-interaction=nonstopmode" in quarto_yml_content
        assert "-halt-on-error" in quarto_yml_content


def test_staging_copies_preamble_template(tmp_path: Path):
    """
    Preamble polpo_preamble.tex is placed in staging with math, TikZ, Babel bidi,
    FreeSerif fonts, and ADR-003 D02 TikZ LTR hooks.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_3"
    job_artifacts_dir.mkdir(parents=True)

    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    with manager.create_session(
        job_artifacts_dir=job_artifacts_dir,
        job_id=3,
        raw_metadata={"title": "Preamble Test"},
        canonical_markdown="Some text",
        asset_resolver=resolver,
    ) as session:
        preamble_text = session.preamble_path.read_text(encoding="utf-8")
        assert "\\usepackage{amsmath}" in preamble_text
        assert "\\usepackage{amssymb}" in preamble_text
        assert "\\usepackage{etoolbox}" in preamble_text
        assert "\\usepackage{tikz}" in preamble_text
        assert "\\usepackage{pgfplots}" in preamble_text
        assert "\\pgfplotsset{compat=1.18}" in preamble_text
        assert "\\usepackage[bidi=default,provide=*]{babel}" in preamble_text
        assert "\\babelprovide[main,import]{persian}" in preamble_text
        assert "\\babelprovide[import]{english}" in preamble_text
        assert "\\babelfont{rm}{FreeSerif}" in preamble_text
        assert "\\babelfont{sf}{FreeSerif}" in preamble_text
        assert "\\AtBeginEnvironment{tikzpicture}{\\begin{otherlanguage*}{english}}" in preamble_text
        assert "\\AtEndEnvironment{tikzpicture}{\\end{otherlanguage*}}" in preamble_text


def test_staging_copies_local_assets_with_path_containment(tmp_path: Path):
    """
    Referenced local image crops inside job_artifacts_dir are resolved and copied
    into the staging workspace, while unreferenced assets are ignored.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_4"
    job_artifacts_dir.mkdir(parents=True)

    # Valid referenced crop
    crop_1 = job_artifacts_dir / "crop_101.png"
    crop_1.write_bytes(b"\x89PNG\r\n\x1a\nFake PNG Data 101")

    # Unreferenced asset in artifact directory
    unreferenced = job_artifacts_dir / "crop_unreferenced.png"
    unreferenced.write_bytes(b"\x89PNG\r\n\x1a\nUnreferenced PNG Data")

    markdown = "# Analysis\n\nHere is a visual crop:\n![Crop 101](crop_101.png)\n"
    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    with manager.create_session(
        job_artifacts_dir=job_artifacts_dir,
        job_id=4,
        raw_metadata={"title": "Asset Test"},
        canonical_markdown=markdown,
        asset_resolver=resolver,
    ) as session:
        assert session.staged_asset_count == 1

        # Check copied asset inside staging
        copied_crop = session.staging_dir / "assets" / "crop_101.png"
        assert copied_crop.is_file()
        assert copied_crop.read_bytes() == b"\x89PNG\r\n\x1a\nFake PNG Data 101"

        # Check unreferenced file was not copied
        assert not (session.staging_dir / "crop_unreferenced.png").exists()
        assert not (session.staging_dir / "assets" / "crop_unreferenced.png").exists()

        # Check staged document.qmd has rewritten reference
        qmd_text = session.qmd_path.read_text(encoding="utf-8")
        assert "assets/crop_101.png" in qmd_text


def test_staging_rejects_path_traversal(tmp_path: Path):
    """
    Asset references attempting directory traversal outside job_artifacts_dir
    must be rejected with AssetAccessError and staging purged.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_5"
    job_artifacts_dir.mkdir(parents=True)

    # Place a secret outside the job boundary
    secret_file = tmp_path / "secret.png"
    secret_file.write_bytes(b"\x89PNG\r\n\x1a\nSecret Data")

    # Path traversal reference escaping job boundary
    markdown = "# Danger\n\n![Secret](../../secret.png)\n"
    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    staging_parent = job_artifacts_dir / ".pdf_staging"

    with pytest.raises(AssetAccessError):
        with manager.create_session(
            job_artifacts_dir=job_artifacts_dir,
            job_id=5,
            raw_metadata={"title": "Traversal Test"},
            canonical_markdown=markdown,
            asset_resolver=resolver,
        ):
            pass  # pragma: no cover

    # Ensure no leftover staging directory remains
    if staging_parent.exists():
        assert list(staging_parent.iterdir()) == []


def test_staging_cleans_up_on_unhandled_exception(tmp_path: Path):
    """
    Simulates an unhandled exception inside the context block.
    The staging directory must be cleanly removed without masking the error.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_6"
    job_artifacts_dir.mkdir(parents=True)

    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    captured_staging_dir = None

    with pytest.raises(RuntimeError, match="Simulated worker crash"):
        with manager.create_session(
            job_artifacts_dir=job_artifacts_dir,
            job_id=6,
            raw_metadata={"title": "Crash Test"},
            canonical_markdown="Body",
            asset_resolver=resolver,
        ) as session:
            captured_staging_dir = session.staging_dir
            assert captured_staging_dir.exists()
            raise RuntimeError("Simulated worker crash")

    assert captured_staging_dir is not None
    assert not captured_staging_dir.exists()
    assert not (job_artifacts_dir / ".pdf_staging").exists()


def test_staging_staged_document_front_matter_and_preamble(tmp_path: Path):
    """
    document.qmd contains normalized YAML front matter with include-in-header
    referencing polpo_preamble.tex and the canonical markdown body.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_7"
    job_artifacts_dir.mkdir(parents=True)

    raw_meta = {
        "title": "Comprehensive Report",
        "subtitle": "Subtitle Note",
        "documentclass": "article",
        "toc": True,
        "number-sections": True,
        "format": {
            "pdf": {
                "papersize": "a4",
            }
        },
    }
    canonical_body = "# Chapter 1\n\nMathematical formula: $x^2 + y^2 = z^2$."
    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    with manager.create_session(
        job_artifacts_dir=job_artifacts_dir,
        job_id=7,
        raw_metadata=raw_meta,
        canonical_markdown=canonical_body,
        asset_resolver=resolver,
    ) as session:
        qmd_content = session.qmd_path.read_text(encoding="utf-8")
        assert qmd_content.startswith("---\n")
        assert "title: \"Comprehensive Report\"" in qmd_content
        assert "subtitle: \"Subtitle Note\"" in qmd_content
        assert "format:" in qmd_content
        assert "pdf:" in qmd_content
        assert "documentclass: \"article\"" in qmd_content
        assert "file: \"polpo_preamble.tex\"" in qmd_content
        assert "papersize: \"a4\"" in qmd_content
        assert "toc: true" in qmd_content
        assert "number-sections: true" in qmd_content
        assert "---\n\n# Chapter 1" in qmd_content
        assert "Mathematical formula: $x^2 + y^2 = z^2$." in qmd_content


def test_staging_canonical_source_immutability(tmp_path: Path):
    """
    Canonical source file on disk is never modified in place during staging.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_8"
    job_artifacts_dir.mkdir(parents=True)

    source_file = job_artifacts_dir / "output_8_v1.md"
    original_content = "# Source Title\n\nOriginal immutable source text."
    source_file.write_text(original_content, encoding="utf-8")
    original_mtime = source_file.stat().st_mtime_ns

    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    with manager.create_session(
        job_artifacts_dir=job_artifacts_dir,
        job_id=8,
        raw_metadata={"title": "Source Immutability"},
        canonical_markdown=original_content,
        asset_resolver=resolver,
    ) as session:
        assert session.qmd_path.exists()
        # Verify canonical file has not been touched
        assert source_file.read_text(encoding="utf-8") == original_content
        assert source_file.stat().st_mtime_ns == original_mtime

    assert source_file.read_text(encoding="utf-8") == original_content


def test_staging_custom_templates_dir(tmp_path: Path):
    """
    StagingWorkspaceManager correctly utilizes a custom templates_dir and raises
    FileNotFoundError if polpo_preamble.tex is missing.
    """
    custom_templates = tmp_path / "custom_templates"
    custom_templates.mkdir()
    custom_preamble = custom_templates / "polpo_preamble.tex"
    custom_preamble.write_text("% Custom Preamble Marker\n", encoding="utf-8")

    job_artifacts_dir = tmp_path / "artifacts" / "job_9"
    job_artifacts_dir.mkdir(parents=True)

    manager = StagingWorkspaceManager(templates_dir=custom_templates)
    resolver = DocumentAssetResolver()

    with manager.create_session(
        job_artifacts_dir=job_artifacts_dir,
        job_id=9,
        raw_metadata={},
        canonical_markdown="Body",
        asset_resolver=resolver,
    ) as session:
        assert session.preamble_path.read_text(encoding="utf-8") == "% Custom Preamble Marker\n"

    # Missing preamble in custom templates dir
    empty_templates = tmp_path / "empty_templates"
    empty_templates.mkdir()
    bad_manager = StagingWorkspaceManager(templates_dir=empty_templates)

    with pytest.raises(FileNotFoundError, match="polpo_preamble.tex"):
        with bad_manager.create_session(
            job_artifacts_dir=job_artifacts_dir,
            job_id=9,
            raw_metadata={},
            canonical_markdown="Body",
            asset_resolver=resolver,
        ):
            pass  # pragma: no cover


def test_staging_deterministic_cleanup_on_generator_close(tmp_path: Path):
    """
    Simulates premature generator termination / cancellation.
    The staging directory must be cleanly purged.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_10"
    job_artifacts_dir.mkdir(parents=True)

    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    cm = manager.create_session(
        job_artifacts_dir=job_artifacts_dir,
        job_id=10,
        raw_metadata={},
        canonical_markdown="Body",
        asset_resolver=resolver,
    )

    session = cm.__enter__()
    staging_dir = session.staging_dir
    assert staging_dir.exists()

    # Exit context manager prematurely
    cm.__exit__(None, None, None)
    assert not staging_dir.exists()


def test_staging_positional_arguments_compatibility(tmp_path: Path):
    """
    create_session must support positional invocation conforming to the contract:
    create_session(job_artifacts_dir, job_id, raw_metadata, canonical_markdown, asset_resolver)
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_11"
    job_artifacts_dir.mkdir(parents=True)

    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    with manager.create_session(
        job_artifacts_dir,
        11,
        {"title": "Positional Invocation"},
        "Positional Markdown Body",
        resolver,
    ) as session:
        assert session.staging_dir.exists()
        assert session.qmd_path.is_file()

    assert not session.staging_dir.exists()


def test_staging_yaml_empty_collections_semantic_preservation(tmp_path: Path):
    """
    Empty sequences and mappings in normalized metadata (e.g. authors: [], keywords: [],
    geometry: [], classoption: []) must be serialized as empty YAML collections,
    not stringified or omitted.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_12"
    job_artifacts_dir.mkdir(parents=True)

    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    raw_meta = {
        "title": "Empty Collection Test",
        "authors": [],
        "keywords": [],
        "geometry": [],
        "classoption": [],
        "format": {
            "pdf": {
                "toc": True,
            }
        },
    }

    with manager.create_session(
        job_artifacts_dir=job_artifacts_dir,
        job_id=12,
        raw_metadata=raw_meta,
        canonical_markdown="Body",
        asset_resolver=resolver,
    ) as session:
        qmd_content = session.qmd_path.read_text(encoding="utf-8")
        assert "authors: []" in qmd_content
        assert 'authors: "[]"' not in qmd_content
        assert "keywords: []" in qmd_content
        assert 'keywords: "[]"' not in qmd_content
        assert "geometry: []" in qmd_content
        assert 'geometry: "[]"' not in qmd_content
        assert "classoption: []" in qmd_content
        assert 'classoption: "[]"' not in qmd_content

        # Semantic AST verification via Pandoc (when available in environment)
        pandoc_bin = shutil.which("pandoc")
        if not pandoc_bin:
            # Skip only the AST verification sub-check if Pandoc is unavailable in PATH;
            # direct YAML-output assertions above remain active and validated.
            return

        proc = subprocess.run(
            [pandoc_bin, "-f", "markdown", "-t", "json"],
            input=qmd_content,
            text=True,
            capture_output=True,
            check=True,
        )
        ast = json.loads(proc.stdout)
        meta = ast.get("meta", {})
        assert meta["authors"]["t"] == "MetaList"
        assert meta["authors"]["c"] == []
        assert meta["keywords"]["t"] == "MetaList"
        assert meta["keywords"]["c"] == []
        assert meta["geometry"]["t"] == "MetaList"
        assert meta["geometry"]["c"] == []
        assert meta["classoption"]["t"] == "MetaList"
        assert meta["classoption"]["c"] == []
        assert meta["format"]["t"] == "MetaMap"


def test_staging_cleanup_failure_observable_on_normal_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    If staging workspace removal fails during normal context exit,
    StagingCleanupError must be raised to make the failure observable.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_13"
    job_artifacts_dir.mkdir(parents=True)

    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    import shutil
    def failing_rmtree(path, *args, **kwargs):
        raise PermissionError(f"Simulated cleanup permission error for {path}")

    monkeypatch.setattr(shutil, "rmtree", failing_rmtree)

    with pytest.raises(StagingCleanupError, match="Failed to remove staging workspace"):
        with manager.create_session(
            job_artifacts_dir=job_artifacts_dir,
            job_id=13,
            raw_metadata={},
            canonical_markdown="Body",
            asset_resolver=resolver,
        ):
            pass


def test_staging_cleanup_failure_preserves_primary_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    If a primary exception is propagating from the context block, a secondary
    cleanup failure must NOT mask the original exception.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_14"
    job_artifacts_dir.mkdir(parents=True)

    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    import shutil
    def failing_rmtree(path, *args, **kwargs):
        raise PermissionError(f"Simulated cleanup permission error for {path}")

    monkeypatch.setattr(shutil, "rmtree", failing_rmtree)

    with pytest.raises(RuntimeError, match="Primary execution error"):
        with manager.create_session(
            job_artifacts_dir=job_artifacts_dir,
            job_id=14,
            raw_metadata={},
            canonical_markdown="Body",
            asset_resolver=resolver,
        ):
            raise RuntimeError("Primary execution error")


def test_staging_concurrent_sessions_safety(tmp_path: Path):
    """
    Two concurrent staging sessions under the same job_artifacts_dir must remain
    isolated. Exiting session A must not remove session B's workspace or prematurely
    remove .pdf_staging while session B is active.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_15"
    job_artifacts_dir.mkdir(parents=True)

    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    cm_a = manager.create_session(
        job_artifacts_dir=job_artifacts_dir,
        job_id=15,
        raw_metadata={"title": "Session A"},
        canonical_markdown="Body A",
        asset_resolver=resolver,
    )
    cm_b = manager.create_session(
        job_artifacts_dir=job_artifacts_dir,
        job_id=15,
        raw_metadata={"title": "Session B"},
        canonical_markdown="Body B",
        asset_resolver=resolver,
    )

    session_a = cm_a.__enter__()
    session_b = cm_b.__enter__()

    assert session_a.staging_dir.exists()
    assert session_b.staging_dir.exists()
    assert session_a.staging_dir != session_b.staging_dir
    assert (job_artifacts_dir / ".pdf_staging").exists()

    # Session A exits
    cm_a.__exit__(None, None, None)
    assert not session_a.staging_dir.exists()
    assert session_b.staging_dir.exists()
    assert (job_artifacts_dir / ".pdf_staging").exists()

    # Session B exits
    cm_b.__exit__(None, None, None)
    assert not session_b.staging_dir.exists()
    assert not (job_artifacts_dir / ".pdf_staging").exists()


def test_staging_markdown_leading_horizontal_rule_preservation(tmp_path: Path):
    """
    A canonical Markdown body starting with a horizontal rule '---' and containing
    subsequent horizontal rules must not be mistaken for YAML front matter.
    All body sections and horizontal rules must be preserved.
    """
    job_artifacts_dir = tmp_path / "artifacts" / "job_16"
    job_artifacts_dir.mkdir(parents=True)

    manager = StagingWorkspaceManager()
    resolver = DocumentAssetResolver()

    canonical_body = "---\n\n# Introduction\n\nLegitimate body paragraph.\n\n---\n\n# Section 2\n\nMore text.\n"

    with manager.create_session(
        job_artifacts_dir=job_artifacts_dir,
        job_id=16,
        raw_metadata={"title": "HR Preservation"},
        canonical_markdown=canonical_body,
        asset_resolver=resolver,
    ) as session:
        qmd_content = session.qmd_path.read_text(encoding="utf-8")
        assert "# Introduction" in qmd_content
        assert "Legitimate body paragraph." in qmd_content
        assert "# Section 2" in qmd_content
        assert "More text." in qmd_content
        assert "---\n\n# Introduction" in qmd_content
