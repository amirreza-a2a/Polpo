# ============================================================
#  tests/unit/test_legacy_document_backfill.py
#  Tests for Legacy Document Version Backfill (Ticket 10E.3a-05)
# ============================================================

import hashlib
from pathlib import Path, PureWindowsPath, PurePosixPath
import stat
import pytest

from core.entities.job import Job, JobStatus
from infrastructure.persistence.sqlite.connection import SQLiteDatabaseManager
from infrastructure.persistence.sqlite.migration_runner import SQLiteMigrationRunner
from infrastructure.persistence.sqlite.unit_of_work import SQLiteUnitOfWork, SQLiteUnitOfWorkFactory
from application.services.legacy_document_backfill import (
    backfill_legacy_document_versions,
    resolve_canonical_file_path,
)


@pytest.fixture
def db_manager(tmp_path: Path) -> SQLiteDatabaseManager:
    db_file = tmp_path / "test_polpot.db"
    mgr = SQLiteDatabaseManager(str(db_file))
    runner = SQLiteMigrationRunner(db_manager=mgr)
    runner.run_migrations()
    return mgr


@pytest.fixture
def uow_factory(db_manager: SQLiteDatabaseManager) -> SQLiteUnitOfWorkFactory:
    return SQLiteUnitOfWorkFactory(db_manager)


def _create_test_job(
    uow: SQLiteUnitOfWork,
    file_name: str = "test.pdf",
    output_path: str = None,
    watermark: int = 0,
    status: JobStatus = JobStatus.DONE,
) -> Job:
    cur = uow._conn.cursor()
    cur.execute(
        """
        INSERT INTO jobs (
            file_name, file_path, status, output_path,
            output_artifact_version_watermark, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            file_name,
            f"/input/{file_name}",
            status.value,
            output_path,
            watermark,
            "2026-09-19T10:00:00+00:00",
            "2026-09-19T10:30:00+00:00",
        ),
    )
    job_id = cur.lastrowid
    return uow.jobs.get_by_id(job_id)


def test_backfill_existing_job_with_file(uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    doc_file = tmp_path / "output_1_v1.md"
    content = "# Document Title\n\nSome canonical text."
    doc_file.write_bytes(content.encode("utf-8"))
    expected_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

    with uow_factory.create() as uow:
        job = _create_test_job(uow, output_path=str(doc_file))
        job_id = job.id
        uow.commit()

    summary = backfill_legacy_document_versions(uow_factory)

    assert summary.scanned_jobs >= 1
    assert summary.valid_documents == 1
    assert summary.quarantined_documents == 0
    assert summary.newly_inserted_versions == 1

    with uow_factory.create() as uow:
        latest = uow.document_versions.get_latest_document_version(job_id)
        assert latest is not None
        assert latest.version == 1
        assert latest.sha256 == expected_sha256
        assert latest.integrity_status == "VALID"
        assert latest.published_by == "LEGACY_BACKFILL"
        assert latest.output_path == str(doc_file)
        assert latest.created_at == "2026-09-19T10:30:00+00:00"


def test_backfill_empty_file_valid(uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    empty_file = tmp_path / "output_2_v1.md"
    empty_file.write_bytes(b"")
    expected_empty_sha256 = hashlib.sha256(b"").hexdigest()
    assert expected_empty_sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    with uow_factory.create() as uow:
        job = _create_test_job(uow, output_path=str(empty_file))
        job_id = job.id
        uow.commit()

    summary = backfill_legacy_document_versions(uow_factory)
    assert summary.valid_documents == 1
    assert summary.quarantined_documents == 0

    with uow_factory.create() as uow:
        latest = uow.document_versions.get_latest_document_version(job_id)
        assert latest is not None
        assert latest.version == 1
        assert latest.sha256 == expected_empty_sha256
        assert latest.integrity_status == "VALID"


def test_backfill_missing_file_quarantined(uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    missing_file = tmp_path / "nonexistent_output.md"

    with uow_factory.create() as uow:
        job = _create_test_job(uow, output_path=str(missing_file))
        job_id = job.id
        uow.commit()

    summary = backfill_legacy_document_versions(uow_factory)
    assert summary.quarantined_documents == 1
    assert summary.valid_documents == 0

    with uow_factory.create() as uow:
        latest = uow.document_versions.get_latest_document_version(job_id)
        assert latest is not None
        assert latest.version == 1
        assert latest.sha256 is None
        assert latest.integrity_status == "QUARANTINED"
        assert latest.published_by == "LEGACY_BACKFILL"


def test_backfill_unreadable_file_quarantined(uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    unreadable_file = tmp_path / "unreadable_output.md"
    unreadable_file.write_text("secret content")
    unreadable_file.chmod(0)

    try:
        with uow_factory.create() as uow:
            job = _create_test_job(uow, output_path=str(unreadable_file))
            job_id = job.id
            uow.commit()

        summary = backfill_legacy_document_versions(uow_factory)
        # Note: on some environments running as root chmod(0) is readable; handle both
        with uow_factory.create() as uow:
            latest = uow.document_versions.get_latest_document_version(job_id)
            assert latest is not None
            if summary.quarantined_documents == 1:
                assert latest.sha256 is None
                assert latest.integrity_status == "QUARANTINED"
            else:
                assert latest.integrity_status == "VALID"
    finally:
        unreadable_file.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_backfill_idempotent(uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    doc_file = tmp_path / "output_5_v1.md"
    doc_file.write_text("Content")

    with uow_factory.create() as uow:
        job = _create_test_job(uow, output_path=str(doc_file))
        job_id = job.id
        uow.commit()

    summary1 = backfill_legacy_document_versions(uow_factory)
    assert summary1.newly_inserted_versions == 1

    summary2 = backfill_legacy_document_versions(uow_factory)
    assert summary2.newly_inserted_versions == 0

    with uow_factory.create() as uow:
        all_versions = uow.document_versions.get_by_job_id(job_id)
        assert len(all_versions) == 1


def test_backfill_null_output_path_skipped(uow_factory: SQLiteUnitOfWorkFactory):
    with uow_factory.create() as uow:
        job1 = _create_test_job(uow, output_path=None)
        job2 = _create_test_job(uow, output_path="")
        job3 = _create_test_job(uow, output_path="   ")
        j1_id, j2_id, j3_id = job1.id, job2.id, job3.id
        uow.commit()

    summary = backfill_legacy_document_versions(uow_factory)
    assert summary.scanned_jobs == 0
    assert summary.newly_inserted_versions == 0

    with uow_factory.create() as uow:
        assert uow.document_versions.get_latest_document_version(j1_id) is None
        assert uow.document_versions.get_latest_document_version(j2_id) is None
        assert uow.document_versions.get_latest_document_version(j3_id) is None


def test_backfill_version_from_filename(uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    doc_file = tmp_path / "output_7_v4.md"
    doc_file.write_text("Content v4")

    with uow_factory.create() as uow:
        # Deliberately set watermark to 99 to ensure watermark is ignored
        job = _create_test_job(uow, output_path=str(doc_file), watermark=99)
        job_id = job.id
        uow.commit()

    backfill_legacy_document_versions(uow_factory)

    with uow_factory.create() as uow:
        latest = uow.document_versions.get_latest_document_version(job_id)
        assert latest is not None
        assert latest.version == 4  # Extracted from v4, not watermark 99!


def test_backfill_quarantine_sets_error_message(uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    missing_file = tmp_path / "missing_file_to_quarantine.md"

    with uow_factory.create() as uow:
        job = _create_test_job(uow, output_path=str(missing_file))
        job_id = job.id
        uow.commit()

    backfill_legacy_document_versions(uow_factory)

    with uow_factory.create() as uow:
        updated_job = uow.jobs.get_by_id(job_id)
        assert updated_job is not None
        assert updated_job.error_message is not None
        assert "[INTEGRITY_QUARANTINE]" in updated_job.error_message


def test_backfill_file_uri_support(uow_factory: SQLiteUnitOfWorkFactory, tmp_path: Path):
    doc_file = tmp_path / "output_10_v2.md"
    doc_file.write_text("URI content")

    uri = doc_file.resolve().as_uri()

    with uow_factory.create() as uow:
        job = _create_test_job(uow, output_path=uri)
        job_id = job.id
        uow.commit()

    backfill_legacy_document_versions(uow_factory)

    with uow_factory.create() as uow:
        latest = uow.document_versions.get_latest_document_version(job_id)
        assert latest is not None
        assert latest.version == 2
        assert latest.integrity_status == "VALID"
        assert latest.output_path == uri


def test_resolve_canonical_file_path_accepts_legacy_two_slash_uri():
    """
    Regression test for Windows legacy two-slash URIs (e.g. file://C:/...) where urlparse
    would interpret the drive letter as netloc and drop it, resulting in path truncation.
    """
    # Legacy Windows-style two-slash URIs (evaluated lexically across platforms)
    expected_c = PureWindowsPath("C:/artifacts/job_1/output_1_v1.md")
    p_c = resolve_canonical_file_path("file://C:/artifacts/job_1/output_1_v1.md")
    assert p_c.as_posix() == expected_c.as_posix()

    expected_d = PureWindowsPath("D:/data/output_2_v1.md")
    p_d = resolve_canonical_file_path("file://D:/data/output_2_v1.md")
    assert p_d.as_posix() == expected_d.as_posix()

    # Lowercase drive letter legacy two-slash
    expected_lower = PureWindowsPath("c:/artifacts/job_3/output_3_v1.md")
    p_lower = resolve_canonical_file_path("file://c:/artifacts/job_3/output_3_v1.md")
    assert p_lower.as_posix().lower() == expected_lower.as_posix().lower()

    # Standard RFC 8089 three-slash URIs with drive letters
    p_rfc_c = resolve_canonical_file_path("file:///C:/artifacts/job_1/output_1_v1.md")
    assert p_rfc_c.as_posix().lower() == expected_c.as_posix().lower()

    p_rfc_d = resolve_canonical_file_path("file:///D:/data/output_2_v1.md")
    assert p_rfc_d.as_posix().lower() == expected_d.as_posix().lower()

    # Standard RFC 8089 POSIX URI (host-native Path on POSIX)
    p_posix = resolve_canonical_file_path("file:///tmp/artifacts/job_1/output_1_v1.md")
    assert p_posix.as_posix() == "/tmp/artifacts/job_1/output_1_v1.md"
    assert p_posix == Path("/tmp/artifacts/job_1/output_1_v1.md")

    # Raw filesystem paths
    p_raw = resolve_canonical_file_path("/tmp/artifacts/output.md")
    assert p_raw == Path("/tmp/artifacts/output.md")

    p_raw_win = resolve_canonical_file_path("C:/artifacts/output.md")
    assert p_raw_win.as_posix().lower() == PureWindowsPath("C:/artifacts/output.md").as_posix().lower()


def test_resolve_canonical_file_path_preserves_literal_percent_sequences():
    """
    Regression test for double percent-decoding in resolve_canonical_file_path().
    Verifies that literal '%20' in filenames (encoded as '%2520' in URIs) is not
    erroneously converted into a space character, while genuine spaces are decoded properly.
    """
    # 1. Path with literal '%20' in filename (encoded as %2520)
    posix_literal_pct = PurePosixPath("/tmp/artifacts/report_%20_v1.md")
    uri_literal_pct = posix_literal_pct.as_uri()
    assert "%2520" in uri_literal_pct

    resolved_literal = resolve_canonical_file_path(uri_literal_pct)
    assert resolved_literal.as_posix() == posix_literal_pct.as_posix()
    assert resolved_literal.name == "report_%20_v1.md"

    # 2. Path with genuine space in filename (encoded as %20)
    posix_with_space = PurePosixPath("/tmp/artifacts/report space v1.md")
    uri_with_space = posix_with_space.as_uri()
    assert "%20" in uri_with_space
    assert "%25" not in uri_with_space

    resolved_space = resolve_canonical_file_path(uri_with_space)
    assert resolved_space.as_posix() == posix_with_space.as_posix()
    assert resolved_space.name == "report space v1.md"

    # 3. Windows vector with literal '%20' in filename
    win_path = PureWindowsPath("C:/artifacts/job_1/report_%20_v1.md")
    win_uri = win_path.as_uri()
    assert "%2520" in win_uri

    resolved_win = resolve_canonical_file_path(win_uri)
    assert resolved_win.as_posix().lower() == win_path.as_posix().lower()
    assert resolved_win.name == "report_%20_v1.md"
