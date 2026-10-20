"""Tests for preserving artifact references during legacy data migration (TICK-REL-02-REF)."""

import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import sqlite3
import pytest

from application.services.document_publication_service import DocumentPublicationService
from application.services.legacy_document_backfill import (
    backfill_legacy_document_versions,
)
from core.entities.artifact import resolve_canonical_file_path
from core.entities.document_version import PublishIntentRecord
from infrastructure.paths import (
    DataMigrationConflictError,
    migrate_legacy_user_data_if_needed,
)
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWorkFactory


def _setup_migrated_db(db_path: Path) -> SQLiteDatabaseManager:
    """Creates a real SQLite database with schema version 4 applied."""
    mgr = SQLiteDatabaseManager(str(db_path))
    runner = SQLiteMigrationRunner(mgr)
    runner.run_migrations()
    return mgr


# ==============================================================================
# Category A: Basic Relocation
# ==============================================================================


def test_basic_artifact_relocation(tmp_path: Path):
    """
    Category A: Verify that migrating legacy data updates persisted references
    in jobs and document_versions to point to the new destination artifact directory,
    preserving relative paths, file content, and SHA-256 digest.
    """
    legacy_dir = tmp_path / "legacy_polpot"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target_polpot"

    # Setup legacy database with full schema
    legacy_db = legacy_dir / "polpot.db"
    mgr = _setup_migrated_db(legacy_db)

    # Create synthetic legacy artifact file
    legacy_artifacts = legacy_dir / "artifacts"
    job_art_dir = legacy_artifacts / "job_42"
    job_art_dir.mkdir(parents=True)
    doc_file = job_art_dir / "output_42_v1.md"
    content = "# Chapter 1\n\nCanonical text content."
    doc_file.write_bytes(content.encode("utf-8"))
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

    # Also create an ingested source PDF inside the legacy artifacts
    source_pdf = job_art_dir / "source.pdf"
    pdf_bytes = b"%PDF-1.4 mock source document"
    source_pdf.write_bytes(pdf_bytes)

    # Insert Job and DocumentVersion records with legacy paths
    output_uri = doc_file.resolve().as_uri()
    source_uri = source_pdf.resolve().as_uri()

    conn = sqlite3.connect(str(legacy_db))
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO jobs (
            id, file_name, file_path, status, output_path,
            output_artifact_version_watermark, created_at, updated_at
        ) VALUES (42, 'source.pdf', ?, 'done', ?, 1, '2026-10-01T12:00:00+00:00', '2026-10-01T12:30:00+00:00')
        """,
        (source_uri, output_uri),
    )
    cur.execute(
        """
        INSERT INTO document_versions (
            job_id, version, output_path, sha256, integrity_status, published_by, created_at
        ) VALUES (42, 1, ?, ?, 'VALID', 'TEST', '2026-10-01T12:30:00+00:00')
        """,
        (output_uri, content_sha256),
    )
    conn.commit()
    conn.close()

    # Run data migration
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
    assert report.reason == "migration_completed_successfully"

    # Verify target artifact files exist on disk
    target_artifacts = target_dir / "artifacts"
    expected_doc_file = target_artifacts / "job_42" / "output_42_v1.md"
    expected_source_file = target_artifacts / "job_42" / "source.pdf"
    assert expected_doc_file.is_file()
    assert expected_source_file.is_file()
    assert expected_doc_file.read_text(encoding="utf-8") == content
    assert hashlib.sha256(expected_doc_file.read_bytes()).hexdigest() == content_sha256

    # Verify persisted database records in target_dir resolve to the target files
    target_db = target_dir / "polpot.db"
    dest_conn = sqlite3.connect(str(target_db))
    dest_conn.row_factory = sqlite3.Row
    dcur = dest_conn.cursor()

    dcur.execute("SELECT output_path, file_path FROM jobs WHERE id = 42")
    job_row = dcur.fetchone()
    assert job_row is not None
    migrated_job_output = job_row["output_path"]
    migrated_job_file = job_row["file_path"]

    # Persisted references must now point to the target location
    assert resolve_canonical_file_path(migrated_job_output).resolve() == expected_doc_file.resolve()
    assert resolve_canonical_file_path(migrated_job_file).resolve() == expected_source_file.resolve()

    dcur.execute("SELECT output_path, sha256, integrity_status FROM document_versions WHERE job_id = 42 AND version = 1")
    doc_row = dcur.fetchone()
    assert doc_row is not None
    assert resolve_canonical_file_path(doc_row["output_path"]).resolve() == expected_doc_file.resolve()
    assert doc_row["sha256"] == content_sha256
    assert doc_row["integrity_status"] == "VALID"

    dest_conn.close()


# ==============================================================================
# Category B: URI and Path Compatibility
# ==============================================================================


def test_uri_compatibility_spaces_and_unicode_and_percent(tmp_path: Path):
    """
    Category B: Verify URI compatibility with spaces, Unicode characters, literal percent signs,
    and raw filesystem paths across multiple tables (jobs, pipeline2_jobs, visual_regions).
    """
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"

    legacy_db = legacy_dir / "polpot.db"
    _setup_migrated_db(legacy_db)

    legacy_artifacts = legacy_dir / "artifacts"
    job_dir = legacy_artifacts / "job_10"
    job_dir.mkdir(parents=True)

    # 1. File with space in name
    file_space = job_dir / "output with space_v1.md"
    file_space.write_text("space content", encoding="utf-8")

    # 2. File with Unicode characters (Persian)
    file_unicode = job_dir / "گزارش_پژوهشی_v1.md"
    file_unicode.write_text("unicode content", encoding="utf-8")

    # 3. File with literal percent in name (e.g. output_%20_literal_v1.md)
    file_pct = job_dir / "output_%20_literal_v1.md"
    file_pct.write_text("percent content", encoding="utf-8")

    # 4. Raw filesystem path (without file:// scheme)
    file_raw = job_dir / "raw_output_v1.md"
    file_raw.write_text("raw path content", encoding="utf-8")

    # 5. Crop image for visual_regions
    crop_file = job_dir / "crop_p1_1.jpg"
    crop_file.write_bytes(b"\xff\xd8\xff JPEG mock")

    conn = sqlite3.connect(str(legacy_db))
    cur = conn.cursor()

    # Job 1: Space in name (URI)
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (10, 'space.pdf', '/tmp/input.pdf', 'done', ?, '2026-10-01T00:00:00', '2026-10-01T00:00:00')",
        (file_space.resolve().as_uri(),),
    )

    # Job 2: Unicode in name (URI)
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (11, 'unicode.pdf', '/tmp/input.pdf', 'done', ?, '2026-10-01T00:00:00', '2026-10-01T00:00:00')",
        (file_unicode.resolve().as_uri(),),
    )

    # Job 3: Literal %20 in name (URI with %2520)
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (12, 'pct.pdf', '/tmp/input.pdf', 'done', ?, '2026-10-01T00:00:00', '2026-10-01T00:00:00')",
        (file_pct.resolve().as_uri(),),
    )

    # Job 4: Raw filesystem path
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (13, 'raw.pdf', '/tmp/input.pdf', 'done', ?, '2026-10-01T00:00:00', '2026-10-01T00:00:00')",
        (str(file_raw.resolve()),),
    )

    # Pipeline2 job pointing to file_space as input and file_raw as output
    cur.execute(
        "INSERT INTO pipeline2_jobs (id, source_job_id, status, input_path, output_path, created_at, updated_at) "
        "VALUES (100, 10, 'done', ?, ?, '2026-10-01T00:00:00', '2026-10-01T00:00:00')",
        (file_space.resolve().as_uri(), str(file_raw.resolve())),
    )

    # Visual region with active_artifact_uri
    cur.execute(
        """
        INSERT INTO visual_regions (
            job_id, region_id, page_number, display_order, origin,
            sync_status, active_artifact_version, active_artifact_uri, created_at, updated_at
        ) VALUES (10, 'reg-uuid-1', 1, 1, 'user_manual', 'synced', 1, ?, '2026-10-01T00:00:00', '2026-10-01T00:00:00')
        """,
        (crop_file.resolve().as_uri(),),
    )

    conn.commit()
    conn.close()

    # Migrate
    migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    target_artifacts = target_dir / "artifacts"
    target_db = target_dir / "polpot.db"
    dest_conn = sqlite3.connect(str(target_db))
    dest_conn.row_factory = sqlite3.Row
    dcur = dest_conn.cursor()

    # Verify Job 1 (Space)
    dcur.execute("SELECT output_path FROM jobs WHERE id = 10")
    row10 = dcur.fetchone()
    assert "%20" in row10["output_path"]
    expected_space = (target_artifacts / "job_10" / "output with space_v1.md").resolve()
    assert resolve_canonical_file_path(row10["output_path"]).resolve() == expected_space

    # Verify Job 2 (Unicode)
    dcur.execute("SELECT output_path FROM jobs WHERE id = 11")
    row11 = dcur.fetchone()
    expected_unicode = (target_artifacts / "job_10" / "گزارش_پژوهشی_v1.md").resolve()
    assert resolve_canonical_file_path(row11["output_path"]).resolve() == expected_unicode

    # Verify Job 3 (Literal %20 preserved, encoded as %2520 in URI)
    dcur.execute("SELECT output_path FROM jobs WHERE id = 12")
    row12 = dcur.fetchone()
    assert "%2520" in row12["output_path"]
    expected_pct = (target_artifacts / "job_10" / "output_%20_literal_v1.md").resolve()
    assert resolve_canonical_file_path(row12["output_path"]).resolve() == expected_pct

    # Verify Job 4 (Raw path remains raw path)
    dcur.execute("SELECT output_path FROM jobs WHERE id = 13")
    row13 = dcur.fetchone()
    assert not row13["output_path"].startswith("file://")
    expected_raw = (target_artifacts / "job_10" / "raw_output_v1.md").resolve()
    assert Path(row13["output_path"]).resolve() == expected_raw

    # Verify Pipeline2 job
    dcur.execute("SELECT input_path, output_path FROM pipeline2_jobs WHERE id = 100")
    p2_row = dcur.fetchone()
    assert resolve_canonical_file_path(p2_row["input_path"]).resolve() == expected_space
    assert Path(p2_row["output_path"]).resolve() == expected_raw

    # Verify Visual Region
    dcur.execute("SELECT active_artifact_uri FROM visual_regions WHERE region_id = 'reg-uuid-1'")
    vr_row = dcur.fetchone()
    expected_crop = (target_artifacts / "job_10" / "crop_p1_1.jpg").resolve()
    assert resolve_canonical_file_path(vr_row["active_artifact_uri"]).resolve() == expected_crop

    dest_conn.close()


def test_windows_uri_and_path_compatibility(tmp_path: Path):
    """
    Category B: Verify Windows two-slash, RFC 8089 three-slash, and drive-letter raw paths.
    """
    from infrastructure.paths import _remap_persisted_artifact_path  # expected private helper

    legacy_root = PureWindowsPath("C:/Users/test/AppData/Local/polpot/artifacts")
    target_root = PureWindowsPath("D:/PolpoData/artifacts")

    # 1. RFC 8089 URI (lowercase and uppercase scheme)
    uri_rfc = "file:///C:/Users/test/AppData/Local/polpot/artifacts/job_1/output_1_v1.md"
    remapped_rfc = _remap_persisted_artifact_path(uri_rfc, legacy_root, target_root)
    assert remapped_rfc == "file:///D:/PolpoData/artifacts/job_1/output_1_v1.md"

    uri_rfc_upper = "FILE:///C:/Users/test/AppData/Local/polpot/artifacts/job_1/output_1_v1.md"
    remapped_rfc_upper = _remap_persisted_artifact_path(uri_rfc_upper, legacy_root, target_root)
    assert remapped_rfc_upper == "file:///D:/PolpoData/artifacts/job_1/output_1_v1.md"

    # 2. Legacy two-slash URI (lowercase and uppercase scheme)
    uri_2slash = "file://C:/Users/test/AppData/Local/polpot/artifacts/job_1/output_1_v1.md"
    remapped_2slash = _remap_persisted_artifact_path(uri_2slash, legacy_root, target_root)
    assert remapped_2slash == "file:///D:/PolpoData/artifacts/job_1/output_1_v1.md"

    uri_2slash_upper = "FILE://C:/Users/test/AppData/Local/polpot/artifacts/job_1/output_1_v1.md"
    remapped_2slash_upper = _remap_persisted_artifact_path(uri_2slash_upper, legacy_root, target_root)
    assert remapped_2slash_upper == "file:///D:/PolpoData/artifacts/job_1/output_1_v1.md"

    # Non-local authorities on Windows must NOT be remapped
    uri_remote_upper = "FILE://remote-host/C:/Users/test/AppData/Local/polpot/artifacts/job_1/output_1_v1.md"
    assert _remap_persisted_artifact_path(uri_remote_upper, legacy_root, target_root) == uri_remote_upper
    uri_remote_title = "File://remote-host/C:/Users/test/AppData/Local/polpot/artifacts/job_1/output_1_v1.md"
    assert _remap_persisted_artifact_path(uri_remote_title, legacy_root, target_root) == uri_remote_title

    # 3. Raw Windows path with forward slashes
    raw_forward = "C:/Users/test/AppData/Local/polpot/artifacts/job_1/output_1_v1.md"
    remapped_fwd = _remap_persisted_artifact_path(raw_forward, legacy_root, target_root)
    assert remapped_fwd == "D:/PolpoData/artifacts/job_1/output_1_v1.md"

    # 4. Raw Windows path with backslashes
    raw_back = r"C:\Users\test\AppData\Local\polpot\artifacts\job_1\output_1_v1.md"
    remapped_back = _remap_persisted_artifact_path(raw_back, legacy_root, target_root)
    assert remapped_back == r"D:\PolpoData\artifacts\job_1\output_1_v1.md"


# ==============================================================================
# Category C: Path Ownership and Non-Rewriting
# ==============================================================================


def test_path_ownership_and_non_rewriting(tmp_path: Path):
    """
    Category C: Verify that external paths, unrelated URIs, directory traversal attempts,
    and symlinks pointing outside the artifact root are NOT rewritten.
    """
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"

    legacy_db = legacy_dir / "polpot.db"
    _setup_migrated_db(legacy_db)

    legacy_artifacts = legacy_dir / "artifacts"
    legacy_artifacts.mkdir(parents=True)

    # External source file outside artifact root
    external_dir = tmp_path / "user_documents"
    external_dir.mkdir(parents=True)
    external_source = external_dir / "original_presentation.pdf"
    external_source.write_bytes(b"%PDF external")

    # Symlink inside artifacts pointing OUTSIDE to external_dir
    symlink_outside = legacy_artifacts / "symlink_dir"
    try:
        os.symlink(str(external_dir), str(symlink_outside))
        has_symlink = True
    except (OSError, NotImplementedError):
        has_symlink = False

    conn = sqlite3.connect(str(legacy_db))
    cur = conn.cursor()

    # Job with external raw path
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (1, 'doc.pdf', ?, 'done', NULL, '2026-10-01', '2026-10-01')",
        (str(external_source),),
    )

    # Job with external URI
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (2, 'doc.pdf', ?, 'done', NULL, '2026-10-01', '2026-10-01')",
        (external_source.resolve().as_uri(),),
    )

    # Job with unrelated URI scheme
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (3, 'doc.pdf', 'https://example.com/remote.pdf', 'done', NULL, '2026-10-01', '2026-10-01')",
    )

    # Job with directory traversal attempt in output_path
    traversal_path = f"{legacy_artifacts}/../user_documents/escaped.md"
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (4, 'doc.pdf', '/tmp/input.pdf', 'done', ?, '2026-10-01', '2026-10-01')",
        (traversal_path,),
    )

    if has_symlink:
        symlink_path = str(symlink_outside / "escaped.md")
        cur.execute(
            "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
            "VALUES (5, 'doc.pdf', '/tmp/input.pdf', 'done', ?, '2026-10-01', '2026-10-01')",
            (symlink_path,),
        )

    conn.commit()
    conn.close()

    # Migrate
    migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    target_db = target_dir / "polpot.db"
    dest_conn = sqlite3.connect(str(target_db))
    dest_conn.row_factory = sqlite3.Row
    dcur = dest_conn.cursor()

    # Job 1: External raw path preserved exactly
    dcur.execute("SELECT file_path FROM jobs WHERE id = 1")
    assert dcur.fetchone()["file_path"] == str(external_source)

    # Job 2: External URI preserved exactly
    dcur.execute("SELECT file_path FROM jobs WHERE id = 2")
    assert dcur.fetchone()["file_path"] == external_source.resolve().as_uri()

    # Job 3: Remote URI preserved exactly
    dcur.execute("SELECT file_path FROM jobs WHERE id = 3")
    assert dcur.fetchone()["file_path"] == "https://example.com/remote.pdf"

    # Job 4: Traversal path preserved without remapping (must NOT be remapped into target_dir)
    dcur.execute("SELECT output_path FROM jobs WHERE id = 4")
    assert dcur.fetchone()["output_path"] == traversal_path

    if has_symlink:
        # Job 5: Symlink escaping outside must NOT be remapped
        dcur.execute("SELECT output_path FROM jobs WHERE id = 5")
        assert dcur.fetchone()["output_path"] == symlink_path

    dest_conn.close()


def test_file_uri_non_empty_non_drive_authority_not_remapped(tmp_path: Path):
    """
    Category C: Prove that file:// URIs with a non-empty, non-drive authority
    (e.g., file://remote-host/<legacy-artifacts-root>/job_1/output.md) are NOT silently
    converted into local target URIs, even when their path component lexically falls
    beneath legacy_artifacts_root.

    Local authorities (empty authority file:///..., file://localhost/..., and legacy Windows
    file://C:/...) MUST continue to be remapped when beneath legacy_artifacts_root.
    """
    from infrastructure.paths import _remap_persisted_artifact_path

    legacy_artifacts = tmp_path / "legacy" / "artifacts"
    target_artifacts = tmp_path / "target" / "artifacts"
    legacy_artifacts.mkdir(parents=True)
    target_artifacts.mkdir(parents=True)

    legacy_posix = "/" + legacy_artifacts.as_posix().lstrip("/")

    # 1. Non-empty, non-drive authorities pointing lexically beneath legacy_artifacts
    # Scheme capitalization must not bypass authority validation (RFC 3986 Section 3.1)
    remote_host_uri = f"file://remote-host{legacy_posix}/job_1/output.md"
    remote_host_uri_upper = f"FILE://remote-host{legacy_posix}/job_1/output.md"
    remote_host_uri_title = f"File://remote-host{legacy_posix}/job_1/output.md"
    server_uri = f"file://fileserver.corp.internal{legacy_posix}/job_1/output.md"
    ip_uri = f"file://192.168.1.100{legacy_posix}/job_1/output.md"

    assert _remap_persisted_artifact_path(remote_host_uri, legacy_artifacts, target_artifacts) == remote_host_uri
    assert _remap_persisted_artifact_path(remote_host_uri_upper, legacy_artifacts, target_artifacts) == remote_host_uri_upper
    assert _remap_persisted_artifact_path(remote_host_uri_title, legacy_artifacts, target_artifacts) == remote_host_uri_title
    assert _remap_persisted_artifact_path(server_uri, legacy_artifacts, target_artifacts) == server_uri
    assert _remap_persisted_artifact_path(ip_uri, legacy_artifacts, target_artifacts) == ip_uri

    # 2. Local authorities MUST be remapped regardless of scheme capitalization
    local_rfc_uri = f"file://{legacy_posix}/job_1/output.md"
    local_rfc_uri_upper = f"FILE://{legacy_posix}/job_1/output.md"
    expected_target_rfc = (target_artifacts / "job_1" / "output.md").resolve().as_uri()
    assert _remap_persisted_artifact_path(local_rfc_uri, legacy_artifacts, target_artifacts) == expected_target_rfc
    assert _remap_persisted_artifact_path(local_rfc_uri_upper, legacy_artifacts, target_artifacts) == expected_target_rfc

    localhost_uri = f"file://localhost{legacy_posix}/job_1/output.md"
    localhost_uri_title = f"File://localhost{legacy_posix}/job_1/output.md"
    assert _remap_persisted_artifact_path(localhost_uri, legacy_artifacts, target_artifacts) == expected_target_rfc
    assert _remap_persisted_artifact_path(localhost_uri_title, legacy_artifacts, target_artifacts) == expected_target_rfc

    # 3. Full database migration integration test verifying SQLite persistence
    legacy_dir = tmp_path / "legacy_full"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target_full"

    legacy_db = legacy_dir / "polpot.db"
    _setup_migrated_db(legacy_db)

    legacy_art_full = legacy_dir / "artifacts"
    job_art_dir = legacy_art_full / "job_50"
    job_art_dir.mkdir(parents=True)
    doc_file = job_art_dir / "output.md"
    doc_file.write_bytes(b"Local artifact content")

    doc_posix = "/" + doc_file.resolve().as_posix().lstrip("/")

    # Local URI pointing to real local file (standard, uppercase scheme, and localhost TitleCase)
    local_job_uri = doc_file.resolve().as_uri()
    local_job_uri_upper = f"FILE://{doc_posix}"
    local_job_uri_localhost = f"File://localhost{doc_posix}"

    # Remote URIs with non-empty, non-drive authorities under various scheme capitalizations
    remote_job_uri = f"file://remote-host{doc_posix}"
    remote_job_uri_upper = f"FILE://remote-host{doc_posix}"
    remote_job_uri_title = f"File://remote-host{doc_posix}"

    conn = sqlite3.connect(str(legacy_db))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (50, 'doc1.pdf', '/tmp/input.pdf', 'done', ?, '2026-10-01', '2026-10-01')",
        (local_job_uri,),
    )
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (51, 'doc2.pdf', '/tmp/input.pdf', 'done', ?, '2026-10-01', '2026-10-01')",
        (remote_job_uri,),
    )
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (52, 'doc3.pdf', '/tmp/input.pdf', 'done', ?, '2026-10-01', '2026-10-01')",
        (local_job_uri_upper,),
    )
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (53, 'doc4.pdf', '/tmp/input.pdf', 'done', ?, '2026-10-01', '2026-10-01')",
        (remote_job_uri_upper,),
    )
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (54, 'doc5.pdf', '/tmp/input.pdf', 'done', ?, '2026-10-01', '2026-10-01')",
        (remote_job_uri_title,),
    )
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (55, 'doc6.pdf', '/tmp/input.pdf', 'done', ?, '2026-10-01', '2026-10-01')",
        (local_job_uri_localhost,),
    )
    conn.commit()
    conn.close()

    migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    target_db = target_dir / "polpot.db"
    dest_conn = sqlite3.connect(str(target_db))
    dest_conn.row_factory = sqlite3.Row
    dcur = dest_conn.cursor()

    expected_target_file = (target_dir / "artifacts" / "job_50" / "output.md").resolve()

    # Job 50 (local file:/// URI) must be remapped to target
    dcur.execute("SELECT output_path FROM jobs WHERE id = 50")
    assert resolve_canonical_file_path(dcur.fetchone()["output_path"]).resolve() == expected_target_file

    # Job 51 (remote file://remote-host URI) must NOT be remapped to target; original URI must be preserved
    dcur.execute("SELECT output_path FROM jobs WHERE id = 51")
    assert dcur.fetchone()["output_path"] == remote_job_uri

    # Job 52 (local FILE:/// URI) must be remapped to target
    dcur.execute("SELECT output_path FROM jobs WHERE id = 52")
    assert resolve_canonical_file_path(dcur.fetchone()["output_path"]).resolve() == expected_target_file

    # Job 53 (remote FILE://remote-host URI) must NOT be remapped to target; original URI must be preserved
    dcur.execute("SELECT output_path FROM jobs WHERE id = 53")
    assert dcur.fetchone()["output_path"] == remote_job_uri_upper

    # Job 54 (remote File://remote-host URI) must NOT be remapped to target; original URI must be preserved
    dcur.execute("SELECT output_path FROM jobs WHERE id = 54")
    assert dcur.fetchone()["output_path"] == remote_job_uri_title

    # Job 55 (local File://localhost URI) must be remapped to target
    dcur.execute("SELECT output_path FROM jobs WHERE id = 55")
    assert resolve_canonical_file_path(dcur.fetchone()["output_path"]).resolve() == expected_target_file

    dest_conn.close()


def test_unrelated_schemes_and_relative_paths_immune_to_working_directory(tmp_path: Path):
    """
    Category C: Ensure _remap_persisted_artifact_path and database migration are completely
    immune to the current working directory (os.getcwd()). Even when the process is executing
    from within legacy_artifacts_root:
    - Remote/unrelated schemes (https://, http://, ftp://, s3://) must NEVER be remapped.
    - Unrooted relative paths (e.g. 'doc.pdf', 'relative/doc.pdf') must NEVER be remapped.
    - In-legacy absolute paths and local file:/// URIs must still be remapped correctly.
    """
    from infrastructure.paths import _remap_persisted_artifact_path

    legacy_dir = tmp_path / "legacy_app"
    target_dir = tmp_path / "target_app"
    legacy_artifacts = legacy_dir / "artifacts"
    target_artifacts = target_dir / "artifacts"

    legacy_artifacts.mkdir(parents=True)
    target_artifacts.mkdir(parents=True)

    job_dir = legacy_artifacts / "job_77"
    job_dir.mkdir(parents=True)
    doc_file = job_dir / "output_77_v1.md"
    doc_file.write_text("persisted content", encoding="utf-8")

    orig_cwd = os.getcwd()
    try:
        # Intentionally change working directory to inside the legacy artifacts tree
        os.chdir(str(legacy_artifacts))

        # 1. Non-file URI schemes
        for remote_url in [
            "https://example.com/reports/source.pdf",
            "http://example.com/reports/source.pdf",
            "ftp://files.example.com/source.pdf",
            "s3://my-bucket/documents/doc.pdf",
        ]:
            remapped = _remap_persisted_artifact_path(remote_url, legacy_artifacts, target_artifacts)
            assert remapped == remote_url, f"Remote URL {remote_url} was incorrectly remapped to {remapped}"

        # 2. Unrooted relative paths
        for rel_path in ["relative/doc.pdf", "doc.pdf", "./job_77/output.md"]:
            remapped = _remap_persisted_artifact_path(rel_path, legacy_artifacts, target_artifacts)
            assert remapped == rel_path, f"Relative path {rel_path} was incorrectly remapped to {remapped}"

        # 3. Legitimate in-legacy URI must still be remapped accurately
        legit_uri = doc_file.resolve().as_uri()
        remapped_legit = _remap_persisted_artifact_path(legit_uri, legacy_artifacts, target_artifacts)
        expected_target_uri = (target_artifacts / "job_77" / "output_77_v1.md").resolve().as_uri()
        assert remapped_legit == expected_target_uri

        # 4. Legitimate in-legacy raw filesystem path
        legit_path = str(doc_file.resolve())
        remapped_path = _remap_persisted_artifact_path(legit_path, legacy_artifacts, target_artifacts)
        expected_target_path = str((target_artifacts / "job_77" / "output_77_v1.md").resolve())
        assert remapped_path == expected_target_path

    finally:
        os.chdir(orig_cwd)




# ==============================================================================
# Category D: Backfill and Artifact-Read Integration
# ==============================================================================


def test_backfill_and_artifact_read_integration(tmp_path: Path):
    """
    Category D: End-to-end integration test demonstrating that after migration,
    backfill_legacy_document_versions does NOT quarantine documents whose containing
    directory was relocated, and the migrated document is readable.
    """
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"

    legacy_db = legacy_dir / "polpot.db"
    _setup_migrated_db(legacy_db)

    legacy_artifacts = legacy_dir / "artifacts"
    job_art_dir = legacy_artifacts / "job_99"
    job_art_dir.mkdir(parents=True)
    doc_file = job_art_dir / "output_99_v1.md"
    doc_content = "# Title\n\nPreserved content."
    doc_file.write_bytes(doc_content.encode("utf-8"))
    expected_sha256 = hashlib.sha256(doc_content.encode("utf-8")).hexdigest()

    conn = sqlite3.connect(str(legacy_db))
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO jobs (
            id, file_name, file_path, status, output_path,
            output_artifact_version_watermark, created_at, updated_at
        ) VALUES (99, 'paper.pdf', 'file:///input/paper.pdf', 'done', ?, 0, '2026-10-01', '2026-10-01')
        """,
        (doc_file.resolve().as_uri(),),
    )
    conn.commit()
    conn.close()

    # Migrate data directory
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated

    # In legacy_dir, artifacts directory should be archived, so old path does not exist
    assert not doc_file.exists()
    assert (legacy_dir / "artifacts.migrated").exists()

    # Target artifact file must exist
    target_doc_file = target_dir / "artifacts" / "job_99" / "output_99_v1.md"
    assert target_doc_file.is_file()

    # Run backfill routine on the target database (as happens during application startup)
    target_mgr = SQLiteDatabaseManager(str(target_dir / "polpot.db"))
    target_uow_factory = SQLiteUnitOfWorkFactory(target_mgr)

    summary = backfill_legacy_document_versions(target_uow_factory)

    # Before the fix, summary.quarantined_documents was 1 and valid_documents was 0.
    # After the fix, valid_documents must be 1 and quarantined_documents must be 0!
    assert summary.valid_documents == 1
    assert summary.quarantined_documents == 0

    with target_uow_factory.create() as uow:
        job = uow.jobs.get_by_id(99)
        assert job.error_message is None or "[INTEGRITY_QUARANTINE]" not in job.error_message

        version_record = uow.document_versions.get_latest_document_version(99)
        assert version_record is not None
        assert version_record.version == 1
        assert version_record.integrity_status == "VALID"
        assert version_record.sha256 == expected_sha256

        # The resolved output path must exist on disk and match target_doc_file
        resolved = resolve_canonical_file_path(version_record.output_path)
        assert resolved.resolve() == target_doc_file.resolve()
        assert resolved.read_text(encoding="utf-8") == doc_content


# ==============================================================================
# Category E: Idempotency and Recovery
# ==============================================================================


def test_migration_idempotency_and_resumption(tmp_path: Path):
    """
    Category E: Verify that running migration repeatedly or resuming after partial
    execution does not duplicate, mangle, or repeatedly prepend paths.
    """
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"

    legacy_db = legacy_dir / "polpot.db"
    _setup_migrated_db(legacy_db)

    legacy_artifacts = legacy_dir / "artifacts"
    job_dir = legacy_artifacts / "job_1"
    job_dir.mkdir(parents=True)
    doc_file = job_dir / "output_1_v1.md"
    doc_file.write_text("Hello", encoding="utf-8")

    conn = sqlite3.connect(str(legacy_db))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (1, 'doc.pdf', '/tmp/doc.pdf', 'done', ?, '2026-10-01', '2026-10-01')",
        (doc_file.resolve().as_uri(),),
    )
    conn.commit()
    conn.close()

    # First migration run
    rep1 = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert rep1.migrated

    target_db = target_dir / "polpot.db"
    conn1 = sqlite3.connect(str(target_db))
    cur1 = conn1.cursor()
    cur1.execute("SELECT output_path FROM jobs WHERE id = 1")
    first_mapped_path = cur1.fetchone()[0]
    conn1.close()

    expected_target_file = (target_dir / "artifacts" / "job_1" / "output_1_v1.md").resolve()
    assert resolve_canonical_file_path(first_mapped_path).resolve() == expected_target_file

    # Second migration run (idempotency check)
    rep2 = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert not rep2.migrated
    assert rep2.reason == "already_migrated"

    # Verify paths in target_db were not altered or doubly-prepended
    conn2 = sqlite3.connect(str(target_db))
    cur2 = conn2.cursor()
    cur2.execute("SELECT output_path FROM jobs WHERE id = 1")
    second_mapped_path = cur2.fetchone()[0]
    conn2.close()

    assert second_mapped_path == first_mapped_path


def test_migration_failure_in_remapping_rolls_back_and_allows_retry(tmp_path: Path, monkeypatch):
    """
    Category E: Injected failure during artifact reference remapping must roll back,
    avoid publishing the corrupted target database or receipt, preserve the source data,
    and allow a subsequent migration attempt to succeed.
    """
    import infrastructure.paths as paths_mod

    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"

    legacy_db = legacy_dir / "polpot.db"
    _setup_migrated_db(legacy_db)

    legacy_artifacts = legacy_dir / "artifacts"
    job_dir = legacy_artifacts / "job_1"
    job_dir.mkdir(parents=True)
    doc_file = job_dir / "output_1_v1.md"
    doc_file.write_text("Hello failure test", encoding="utf-8")

    conn = sqlite3.connect(str(legacy_db))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at) "
        "VALUES (1, 'doc.pdf', '/tmp/doc.pdf', 'done', ?, '2026-10-01', '2026-10-01')",
        (doc_file.resolve().as_uri(),),
    )
    conn.commit()
    conn.close()

    # 1. Inject failure into _remap_database_artifact_references
    def faulty_remap(*args, **kwargs):
        raise RuntimeError("Injected disk/memory fault during remapping")

    monkeypatch.setattr(paths_mod, "_remap_database_artifact_references", faulty_remap)

    # First attempt must fail
    with pytest.raises(RuntimeError, match="Injected disk/memory fault during remapping"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Verify atomic rollback: target_db must NOT exist, legacy_db must remain intact
    target_db = target_dir / "polpot.db"
    assert not target_db.exists()
    assert legacy_db.exists()
    assert (legacy_dir / "artifacts").exists()
    assert not (target_dir / "MIGRATION_RECEIPT.json").exists()

    # 2. Remove injected fault and retry migration
    monkeypatch.undo()

    rep = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert rep.migrated
    assert rep.reason == "migration_completed_successfully"

    # Verify target db is published and correctly remapped
    assert target_db.is_file()
    conn_final = sqlite3.connect(str(target_db))
    cur_final = conn_final.cursor()
    cur_final.execute("SELECT output_path FROM jobs WHERE id = 1")
    remapped_path = cur_final.fetchone()[0]
    conn_final.close()

    expected_target_file = (target_dir / "artifacts" / "job_1" / "output_1_v1.md").resolve()
    assert resolve_canonical_file_path(remapped_path).resolve() == expected_target_file


# ==============================================================================
# Category F: Pending Publication Across Migration (Investigation B)
# ==============================================================================


@pytest.mark.parametrize("intent_status", ["PENDING", "FLUSHED"])
def test_pending_publication_rollback_across_migration(tmp_path: Path, intent_status: str):
    """
    Category F: Reproduce pending publication across migration (rollback scenario).
    When an intent is interrupted in Phase 2 before disk activation:
    1. Legacy staging directory and staged crop exist inside legacy artifacts.
    2. publish_intents contains a PENDING or FLUSHED intent referencing staging_path under legacy root.
    3. Migration relocates database and artifacts; archives legacy artifacts to artifacts.migrated.
    4. Startup recovery executes against the target database and destination artifacts root.
    5. Recovery successfully rolls back temporary work without dereferencing the missing
       legacy staging path, unlinks tmp files and promoted crops in destination,
       cleans the destination staging directory via staging_id, deletes the intent row,
       and preserves pre-existing canonical document versions.
    """
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"

    legacy_db = legacy_dir / "polpot.db"
    _setup_migrated_db(legacy_db)

    legacy_artifacts = legacy_dir / "artifacts"
    job_id = 77
    job_dir = legacy_artifacts / f"job_{job_id}"
    job_dir.mkdir(parents=True)

    # Pre-existing canonical version 1
    v1_file = job_dir / f"output_{job_id}_v1.md"
    v1_content = "# Chapter 1: Initial Version\nCanonical content v1."
    v1_file.write_bytes(v1_content.encode("utf-8"))
    v1_sha = hashlib.sha256(v1_content.encode("utf-8")).hexdigest()
    v1_uri = v1_file.resolve().as_uri()

    # Staging area inside legacy artifacts (.staging/<staging_id>/...)
    staging_id = "stg-mig-rollback-77"
    legacy_staging_dir = legacy_artifacts / ".staging" / staging_id
    legacy_staging_dir.mkdir(parents=True)
    crop_data = b"\xff\xd8\xff JPEG staged crop binary"
    crop_sha = hashlib.sha256(crop_data).hexdigest()
    dest_crop_name = f"crop_{job_id}_fig1_v2.jpg"
    staged_crop_file = legacy_staging_dir / dest_crop_name
    staged_crop_file.write_bytes(crop_data)

    # Interrupted publication in Phase 2: tmp file written and crop promoted before crash
    intent_id = f"intent-{intent_status.lower()}-rb-77"
    tmp_output_file = job_dir / f".output_{job_id}_v2.md.{intent_id}.tmp"
    tmp_output_file.write_bytes(b"# Chapter 1: Version 2\nPartial in-flight text.")
    promoted_crop = job_dir / dest_crop_name
    promoted_crop.write_bytes(crop_data)

    manifest_json = json.dumps([
        {
            "staging_id": staging_id,
            "region_id": "fig1",
            "artifact_version": 2,
            "staging_path": str(staged_crop_file.resolve()),
            "dest_filename": dest_crop_name,
            "sha256": crop_sha,
            "size_bytes": len(crop_data),
        }
    ])

    conn = sqlite3.connect(str(legacy_db))
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at)
        VALUES (?, 'paper.pdf', '/tmp/paper.pdf', 'done', ?, '2026-10-01', '2026-10-01')
        """,
        (job_id, v1_uri),
    )
    cur.execute(
        """
        INSERT INTO document_versions (job_id, version, output_path, sha256, integrity_status, published_by, created_at)
        VALUES (?, 1, ?, ?, 'VALID', 'TEST', '2026-10-01')
        """,
        (job_id, v1_uri, v1_sha),
    )
    cur.execute(
        """
        INSERT INTO publish_intents (
            intent_id, job_id, base_version, target_version,
            output_filename, output_sha256, staged_artifacts_manifest, status, created_at
        ) VALUES (?, ?, 1, 2, ?, ?, ?, ?, '2026-10-01')
        """,
        (
            intent_id,
            job_id,
            f"output_{job_id}_v2.md",
            hashlib.sha256(b"target-v2-sha256").hexdigest(),
            manifest_json,
            intent_status,
        ),
    )
    conn.commit()
    conn.close()

    # Step 5: Execute data migration
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
    assert report.reason == "migration_completed_successfully"

    # Legacy artifacts root was archived; original legacy paths DO NOT exist anymore
    assert not legacy_artifacts.exists()
    assert (legacy_dir / "artifacts.migrated").is_dir()
    assert not staged_crop_file.exists()

    target_artifacts = target_dir / "artifacts"
    target_job_dir = target_artifacts / f"job_{job_id}"
    target_tmp_file = target_job_dir / f".output_{job_id}_v2.md.{intent_id}.tmp"
    target_promoted_crop = target_job_dir / dest_crop_name
    target_staged_crop = target_artifacts / ".staging" / staging_id / dest_crop_name

    # Confirm files were migrated to destination before startup recovery
    assert target_tmp_file.is_file()
    assert target_promoted_crop.is_file()
    assert target_staged_crop.is_file()

    # Step 6: Invoke startup publication recovery service against target database & artifacts
    target_db = target_dir / "polpot.db"
    target_mgr = SQLiteDatabaseManager(str(target_db))
    target_uow_factory = SQLiteUnitOfWorkFactory(target_mgr)
    pub_service = DocumentPublicationService(
        uow_factory=target_uow_factory,
        artifacts_dir=target_artifacts,
    )

    results = pub_service.reconcile_startup_intents()

    # Step 7 & 8: Verify recovery outcome
    assert len(results) == 1
    assert results[0].job_id == job_id
    assert results[0].intent_id == intent_id
    assert results[0].action == "ROLLED_BACK_TMP"
    assert results[0].target_version == 2

    # Intent row must be completely deleted
    with target_uow_factory.create() as uow:
        assert uow.publish_intents.get_by_id(intent_id) is None
        assert uow.publish_intents.get_by_job_id(job_id) is None

        # Canonical version 1 must remain intact and point to target artifacts root
        latest_ver = uow.document_versions.get_latest(job_id)
        assert latest_ver is not None
        assert latest_ver.version == 1
        assert latest_ver.integrity_status == "VALID"
        assert latest_ver.sha256 == v1_sha
        expected_v1_target = (target_job_dir / f"output_{job_id}_v1.md").resolve()
        assert resolve_canonical_file_path(latest_ver.output_path).resolve() == expected_v1_target

        job = uow.jobs.get_by_id(job_id)
        assert resolve_canonical_file_path(job.output_path).resolve() == expected_v1_target

    # Temporary files and promoted crops reverted from destination
    assert not target_tmp_file.exists()
    assert not target_promoted_crop.exists()

    # Destination staging directory was cleanly removed via staging_id
    assert not (target_artifacts / ".staging" / staging_id).exists()

    # Canonical v1 output file in target remains intact and readable
    assert expected_v1_target.is_file()
    assert expected_v1_target.read_text(encoding="utf-8") == v1_content


def test_pending_publication_unflushed_clean_across_migration(tmp_path: Path):
    """
    Category F: Interrupted publication immediately after Phase 1 intent reservation.
    1. Legacy staging directory contains staged crops.
    2. publish_intents contains a PENDING intent referencing legacy staging_path.
    3. Neither tmp file nor final file was written yet before the crash.
    4. Migration relocates database and artifacts.
    5. Startup recovery identifies Case 1 (no tmp, no final file) and deletes the pending intent cleanly,
       unlinks any orphaned destination staging directory via staging_id,
       and preserves the pre-existing canonical document version.
    """
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"

    legacy_db = legacy_dir / "polpot.db"
    _setup_migrated_db(legacy_db)

    legacy_artifacts = legacy_dir / "artifacts"
    job_id = 78
    job_dir = legacy_artifacts / f"job_{job_id}"
    job_dir.mkdir(parents=True)

    # Pre-existing canonical version 1
    v1_file = job_dir / f"output_{job_id}_v1.md"
    v1_content = "# Chapter 1: Baseline\nInitial text."
    v1_file.write_bytes(v1_content.encode("utf-8"))
    v1_sha = hashlib.sha256(v1_content.encode("utf-8")).hexdigest()
    v1_uri = v1_file.resolve().as_uri()

    # Staging area inside legacy artifacts (.staging/<staging_id>/...)
    staging_id = "stg-mig-unflushed-78"
    legacy_staging_dir = legacy_artifacts / ".staging" / staging_id
    legacy_staging_dir.mkdir(parents=True)
    crop_data = b"\xff\xd8\xff JPEG crop image binary"
    crop_sha = hashlib.sha256(crop_data).hexdigest()
    dest_crop_name = f"crop_{job_id}_fig1_v2.jpg"
    staged_crop_file = legacy_staging_dir / dest_crop_name
    staged_crop_file.write_bytes(crop_data)

    intent_id = "intent-pending-unflushed-78"
    manifest_json = json.dumps([
        {
            "staging_id": staging_id,
            "region_id": "fig1",
            "artifact_version": 2,
            "staging_path": str(staged_crop_file.resolve()),
            "dest_filename": dest_crop_name,
            "sha256": crop_sha,
            "size_bytes": len(crop_data),
        }
    ])

    conn = sqlite3.connect(str(legacy_db))
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at)
        VALUES (?, 'doc.pdf', '/tmp/doc.pdf', 'done', ?, '2026-10-01', '2026-10-01')
        """,
        (job_id, v1_uri),
    )
    cur.execute(
        """
        INSERT INTO document_versions (job_id, version, output_path, sha256, integrity_status, published_by, created_at)
        VALUES (?, 1, ?, ?, 'VALID', 'TEST', '2026-10-01')
        """,
        (job_id, v1_uri, v1_sha),
    )
    cur.execute(
        """
        INSERT INTO publish_intents (
            intent_id, job_id, base_version, target_version,
            output_filename, output_sha256, staged_artifacts_manifest, status, created_at
        ) VALUES (?, ?, 1, 2, ?, ?, ?, 'PENDING', '2026-10-01')
        """,
        (
            intent_id,
            job_id,
            f"output_{job_id}_v2.md",
            hashlib.sha256(b"v2-sha-unflushed").hexdigest(),
            manifest_json,
        ),
    )
    conn.commit()
    conn.close()

    # Migrate data
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated

    # Legacy artifacts archived
    assert not legacy_artifacts.exists()
    assert (legacy_dir / "artifacts.migrated").is_dir()

    # Recovery on target
    target_artifacts = target_dir / "artifacts"
    target_db = target_dir / "polpot.db"
    target_mgr = SQLiteDatabaseManager(str(target_db))
    target_uow_factory = SQLiteUnitOfWorkFactory(target_mgr)
    pub_service = DocumentPublicationService(
        uow_factory=target_uow_factory,
        artifacts_dir=target_artifacts,
    )

    results = pub_service.reconcile_startup_intents()

    # Case 1: no tmp, no final -> DELETED_PENDING_INTENT
    assert len(results) == 1
    assert results[0].job_id == job_id
    assert results[0].intent_id == intent_id
    assert results[0].action == "DELETED_PENDING_INTENT"
    assert results[0].target_version == 2

    with target_uow_factory.create() as uow:
        assert uow.publish_intents.get_by_id(intent_id) is None

        # Canonical v1 remains intact and remapped to target
        v1_record = uow.document_versions.get_by_version(job_id, 1)
        assert v1_record is not None
        assert v1_record.version == 1
        assert v1_record.integrity_status == "VALID"
        assert v1_record.sha256 == v1_sha
        expected_v1_target = (target_artifacts / f"job_{job_id}" / f"output_{job_id}_v1.md").resolve()
        assert resolve_canonical_file_path(v1_record.output_path).resolve() == expected_v1_target

    # Staging directory in target was cleaned
    assert not (target_artifacts / ".staging" / staging_id).exists()


def test_pending_publication_forward_roll_across_migration(tmp_path: Path):
    """
    Category F: Reproduce pending publication across migration (forward-roll scenario).
    When an intent is interrupted in Phase 3 (disk activation complete, before SQLite commit):
    1. The final Markdown file exists on disk matching intent.output_sha256.
    2. Staged crop was promoted to destination filename.
    3. publish_intents contains a FLUSHED intent referencing staging_path under legacy root.
    4. Migration relocates database and artifacts; archives legacy artifacts to artifacts.migrated.
    5. Startup recovery executes against the target database and destination artifacts root.
    6. Recovery forward-rolls version 2, updates jobs.output_path and document_versions to
       canonical target URI, preserves promoted crops, removes destination staging via staging_id,
       and deletes the intent row.
    """
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"

    legacy_db = legacy_dir / "polpot.db"
    _setup_migrated_db(legacy_db)

    legacy_artifacts = legacy_dir / "artifacts"
    job_id = 88
    job_dir = legacy_artifacts / f"job_{job_id}"
    job_dir.mkdir(parents=True)

    # Pre-existing canonical version 1
    v1_file = job_dir / f"output_{job_id}_v1.md"
    v1_content = "# Version 1 Original Content"
    v1_file.write_bytes(v1_content.encode("utf-8"))
    v1_sha = hashlib.sha256(v1_content.encode("utf-8")).hexdigest()
    v1_uri = v1_file.resolve().as_uri()

    # Staging area inside legacy artifacts
    staging_id = "stg-mig-fwd-88"
    legacy_staging_dir = legacy_artifacts / ".staging" / staging_id
    legacy_staging_dir.mkdir(parents=True)
    crop_data = b"\xff\xd8\xff JPEG promoted crop"
    crop_sha = hashlib.sha256(crop_data).hexdigest()
    dest_crop_name = f"crop_{job_id}_fig2_v2.jpg"
    staged_crop_file = legacy_staging_dir / dest_crop_name
    staged_crop_file.write_bytes(crop_data)

    # Phase 3 complete: final activated markdown file exists and crop was promoted
    v2_content = "# Version 2 Final Published Content\nSuccessfully activated on disk."
    v2_bytes = v2_content.encode("utf-8")
    v2_sha = hashlib.sha256(v2_bytes).hexdigest()
    final_v2_file = job_dir / f"output_{job_id}_v2.md"
    final_v2_file.write_bytes(v2_bytes)

    promoted_crop = job_dir / dest_crop_name
    promoted_crop.write_bytes(crop_data)

    intent_id = "intent-pending-fwd-88"
    manifest_json = json.dumps([
        {
            "staging_id": staging_id,
            "region_id": "fig2",
            "artifact_version": 2,
            "staging_path": str(staged_crop_file.resolve()),
            "dest_filename": dest_crop_name,
            "sha256": crop_sha,
            "size_bytes": len(crop_data),
        }
    ])

    conn = sqlite3.connect(str(legacy_db))
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at)
        VALUES (?, 'analysis.pdf', '/tmp/analysis.pdf', 'done', ?, '2026-10-01', '2026-10-01')
        """,
        (job_id, v1_uri),
    )
    cur.execute(
        """
        INSERT INTO document_versions (job_id, version, output_path, sha256, integrity_status, published_by, created_at)
        VALUES (?, 1, ?, ?, 'VALID', 'TEST', '2026-10-01')
        """,
        (job_id, v1_uri, v1_sha),
    )
    cur.execute(
        """
        INSERT INTO publish_intents (
            intent_id, job_id, base_version, target_version,
            output_filename, output_sha256, staged_artifacts_manifest, status, created_at
        ) VALUES (?, ?, 1, 2, ?, ?, ?, 'FLUSHED', '2026-10-01')
        """,
        (intent_id, job_id, f"output_{job_id}_v2.md", v2_sha, manifest_json),
    )
    conn.commit()
    conn.close()

    # Migrate data
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated

    # Legacy artifacts archived; legacy paths do not exist
    assert not legacy_artifacts.exists()
    assert not staged_crop_file.exists()

    # Invoke startup recovery on target
    target_artifacts = target_dir / "artifacts"
    target_db = target_dir / "polpot.db"
    target_mgr = SQLiteDatabaseManager(str(target_db))
    target_uow_factory = SQLiteUnitOfWorkFactory(target_mgr)
    pub_service = DocumentPublicationService(
        uow_factory=target_uow_factory,
        artifacts_dir=target_artifacts,
    )

    results = pub_service.reconcile_startup_intents()

    # Recovery must forward-roll version 2
    assert len(results) == 1
    assert results[0].job_id == job_id
    assert results[0].intent_id == intent_id
    assert results[0].action == "FORWARD_ROLLED"
    assert results[0].target_version == 2

    target_v2_file = (target_artifacts / f"job_{job_id}" / f"output_{job_id}_v2.md").resolve()
    target_promoted_crop = (target_artifacts / f"job_{job_id}" / dest_crop_name).resolve()

    with target_uow_factory.create() as uow:
        # Intent deleted
        assert uow.publish_intents.get_by_id(intent_id) is None
        assert uow.publish_intents.get_by_job_id(job_id) is None

        # Version 2 created as VALID and points to canonical target URI
        v2_record = uow.document_versions.get_latest(job_id)
        assert v2_record is not None
        assert v2_record.version == 2
        assert v2_record.integrity_status == "VALID"
        assert v2_record.sha256 == v2_sha
        assert v2_record.published_by == "CRASH_RECOVERY_FORWARD_ROLL"
        assert resolve_canonical_file_path(v2_record.output_path).resolve() == target_v2_file

        # Pre-existing version 1 preserved, remapped to target_artifacts, and remains VALID
        v1_record = uow.document_versions.get_by_version(job_id, 1)
        assert v1_record is not None
        assert v1_record.version == 1
        assert v1_record.integrity_status == "VALID"
        assert v1_record.sha256 == v1_sha
        expected_v1_target = (target_artifacts / f"job_{job_id}" / f"output_{job_id}_v1.md").resolve()
        assert resolve_canonical_file_path(v1_record.output_path).resolve() == expected_v1_target

        # jobs.output_path updated to target URI
        job = uow.jobs.get_by_id(job_id)
        assert resolve_canonical_file_path(job.output_path).resolve() == target_v2_file

    # Promoted crop preserved in target job directory
    assert target_promoted_crop.is_file()
    assert hashlib.sha256(target_promoted_crop.read_bytes()).hexdigest() == crop_sha

    # Destination staging directory was cleaned up
    assert not (target_artifacts / ".staging" / staging_id).exists()

    # Final document content is intact and verified
    assert target_v2_file.is_file()
    assert target_v2_file.read_text(encoding="utf-8") == v2_content


def test_pending_publication_malformed_manifest_across_migration(tmp_path: Path):
    """
    Category F: Ensure deterministic recovery when staged_artifacts_manifest contains
    malformed JSON or corrupted values across migration.
    Recovery falls back safely to empty manifest without throwing unhandled exceptions.
    """
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"

    legacy_db = legacy_dir / "polpot.db"
    _setup_migrated_db(legacy_db)

    legacy_artifacts = legacy_dir / "artifacts"
    job_id = 99
    job_dir = legacy_artifacts / f"job_{job_id}"
    job_dir.mkdir(parents=True)

    intent_id = "intent-malformed-99"
    malformed_manifest = "{NOT_VALID_JSON: true, [unclosed"

    conn = sqlite3.connect(str(legacy_db))
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO jobs (id, file_name, file_path, status, output_path, created_at, updated_at)
        VALUES (?, 'test.pdf', '/tmp/test.pdf', 'done', NULL, '2026-10-01', '2026-10-01')
        """,
        (job_id,),
    )
    cur.execute(
        """
        INSERT INTO publish_intents (
            intent_id, job_id, base_version, target_version,
            output_filename, output_sha256, staged_artifacts_manifest, status, created_at
        ) VALUES (?, ?, 0, 1, ?, 'sha-val', ?, 'PENDING', '2026-10-01')
        """,
        (intent_id, job_id, f"output_{job_id}_v1.md", malformed_manifest),
    )
    conn.commit()
    conn.close()

    # Migration succeeds without error
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated

    # Startup recovery runs and deletes pending intent cleanly despite malformed manifest
    target_artifacts = target_dir / "artifacts"
    target_db = target_dir / "polpot.db"
    target_mgr = SQLiteDatabaseManager(str(target_db))
    target_uow_factory = SQLiteUnitOfWorkFactory(target_mgr)
    pub_service = DocumentPublicationService(
        uow_factory=target_uow_factory,
        artifacts_dir=target_artifacts,
    )

    results = pub_service.reconcile_startup_intents()
    assert len(results) == 1
    assert results[0].action == "DELETED_PENDING_INTENT"
    assert results[0].job_id == job_id

    with target_uow_factory.create() as uow:
        assert uow.publish_intents.get_by_id(intent_id) is None
