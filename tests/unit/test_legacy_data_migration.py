"""Tests for cross-platform data directory resolution and legacy data migration (TICK-REL-02)."""

import os
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch, MagicMock
import pytest

from infrastructure.paths import (
    DataMigrationConflictError,
    DataMigrationReport,
    get_legacy_user_data_dir,
    get_user_data_dir,
    get_user_log_dir,
    migrate_legacy_user_data_if_needed,
)


@pytest.mark.skipif(
    sys.platform.startswith(("win", "darwin")),
    reason="XDG_DATA_HOME is honored only on Linux/POSIX per ADR-003 and AGENTS.md",
)
def test_get_user_data_dir_respects_xdg_data_home(tmp_path: Path):
    """When XDG_DATA_HOME is set and non-empty, get_user_data_dir must honor it."""
    custom_xdg = tmp_path / "custom_xdg"
    with patch.dict(os.environ, {"XDG_DATA_HOME": str(custom_xdg)}):
        result = get_user_data_dir()
        assert result == (custom_xdg / "polpot").resolve()
        assert result.is_absolute()


def test_get_user_data_dir_default_platformdirs():
    """When XDG_DATA_HOME is unset, get_user_data_dir uses platformdirs.user_data_path."""
    env = os.environ.copy()
    env.pop("XDG_DATA_HOME", None)
    with patch.dict(os.environ, env, clear=True):
        result = get_user_data_dir()
        assert result.name == "polpot"
        assert result.is_absolute()


def test_get_user_log_dir_is_inside_user_data_dir(tmp_path: Path):
    """get_user_log_dir returns <user_data_dir>/logs."""
    log_dir = get_user_log_dir()
    assert log_dir == (get_user_data_dir() / "logs").resolve()
    assert log_dir.is_absolute()

    if not sys.platform.startswith(("win", "darwin")):
        custom_xdg = tmp_path / "custom_xdg"
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(custom_xdg)}):
            log_dir_xdg = get_user_log_dir()
            assert log_dir_xdg == (custom_xdg / "polpot" / "logs").resolve()


def test_get_user_data_dir_cross_platform_mocking(tmp_path: Path):
    """Verify platform-specific paths on Windows and macOS using mocks."""
    env = os.environ.copy()
    env.pop("XDG_DATA_HOME", None)

    # Windows mock
    with patch.dict(os.environ, env, clear=True), \
         patch("platformdirs.user_data_path", return_value=tmp_path / "AppData" / "Local" / "polpot"):
        win_path = get_user_data_dir()
        assert win_path == tmp_path / "AppData" / "Local" / "polpot"

    # macOS mock
    with patch.dict(os.environ, env, clear=True), \
         patch("platformdirs.user_data_path", return_value=tmp_path / "Library" / "Application Support" / "polpot"):
        mac_path = get_user_data_dir()
        assert mac_path == tmp_path / "Library" / "Application Support" / "polpot"


def test_migration_identical_source_and_destination(tmp_path: Path):
    """When source and destination resolve to the same path (e.g. Linux default), no migration occurs."""
    target = tmp_path / "polpot"
    report = migrate_legacy_user_data_if_needed(target_dir=target, legacy_dir=target)
    assert not report.migrated
    assert report.reason == "source_and_destination_identical"


def test_migration_legacy_directory_not_found(tmp_path: Path):
    """When legacy directory does not exist, migration gracefully reports no-op."""
    target = tmp_path / "new_target"
    legacy = tmp_path / "nonexistent_legacy"
    report = migrate_legacy_user_data_if_needed(target_dir=target, legacy_dir=legacy)
    assert not report.migrated
    assert report.reason == "legacy_directory_not_found"


def test_migration_empty_legacy_directory(tmp_path: Path):
    """When legacy directory exists but contains no database or vault, reports no legacy data."""
    target = tmp_path / "new_target"
    legacy = tmp_path / "empty_legacy"
    legacy.mkdir(parents=True)
    report = migrate_legacy_user_data_if_needed(target_dir=target, legacy_dir=legacy)
    assert not report.migrated
    assert report.reason == "no_legacy_data_present"


def test_migration_wal_database_and_artifacts(tmp_path: Path):
    """Verify legacy migration correctly flushes and copies SQLite WAL database, vault, and artifacts."""
    legacy_dir = tmp_path / "legacy_polpot"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "polpot.db"

    # Create real SQLite database in WAL mode with uncheckpointed data
    conn = sqlite3.connect(str(legacy_db))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, note TEXT);")
    conn.execute("INSERT INTO test_data (note) VALUES ('active_wal_commit');")
    conn.commit()
    # Close connection without explicit checkpoint so WAL file remains active on disk
    conn.close()

    assert (legacy_dir / "polpot.db-wal").exists() or legacy_db.exists()

    # Create synthetic legacy vault and artifact
    legacy_vault = legacy_dir / "credentials.enc"
    legacy_vault.write_text("encrypted_vault_bytes", encoding="utf-8")

    legacy_artifacts = legacy_dir / "artifacts" / "job_1"
    legacy_artifacts.mkdir(parents=True)
    (legacy_artifacts / "source.pdf").write_bytes(b"%PDF-1.4 mock")

    target_dir = tmp_path / "new_polpot"

    # Run migration
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
    assert report.reason == "migration_completed_successfully"
    assert "polpot.db" in report.migrated_items
    assert "credentials.enc" in report.migrated_items
    assert "artifacts" in report.migrated_items

    # Verify destination database has the committed WAL data
    dest_db = target_dir / "polpot.db"
    assert dest_db.is_file()
    dest_conn = sqlite3.connect(str(dest_db))
    cur = dest_conn.cursor()
    cur.execute("SELECT note FROM test_data WHERE id=1;")
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "active_wal_commit"
    dest_conn.close()

    # Verify destination vault and artifacts
    assert (target_dir / "credentials.enc").read_text(encoding="utf-8") == "encrypted_vault_bytes"
    assert (target_dir / "artifacts" / "job_1" / "source.pdf").read_bytes() == b"%PDF-1.4 mock"

    # Verify legacy files were renamed to .migrated for safe recovery and idempotency
    assert (legacy_dir / "polpot.db.migrated").exists()
    assert (legacy_dir / "credentials.enc.migrated").exists()
    assert (legacy_dir / "artifacts.migrated").exists()
    assert (legacy_dir / ".migrated_to_platformdirs").exists()

    # Idempotency check: running migration again immediately returns already_migrated
    repeat_report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert not repeat_report.migrated
    assert repeat_report.reason == "already_migrated"


def test_migration_conflict_both_databases_exist(tmp_path: Path):
    """When both source and destination have active databases, raise DataMigrationConflictError and preserve both."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "polpot.db"
    legacy_db.write_text("legacy db content")

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)
    target_db = target_dir / "polpot.db"
    target_db.write_text("target db content")

    with pytest.raises(DataMigrationConflictError) as exc_info:
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    assert "Data migration conflict" in str(exc_info.value)
    # Both databases remain completely untouched
    assert legacy_db.read_text() == "legacy db content"
    assert target_db.read_text() == "target db content"


def test_migration_failure_without_destructive_cleanup(tmp_path: Path):
    """When an error occurs during migration, staging files are cleaned up and source data is preserved."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "polpot.db"

    # Create real valid SQLite source database
    conn = sqlite3.connect(str(legacy_db))
    conn.execute("CREATE TABLE original (val TEXT);")
    conn.execute("INSERT INTO original VALUES ('important_data');")
    conn.commit()
    conn.close()

    target_dir = tmp_path / "target"

    real_connect = sqlite3.connect

    def faulty_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        mock_conn = MagicMock(wraps=conn)
        mock_conn.backup.side_effect = sqlite3.OperationalError("Simulated disk error")
        return mock_conn

    # Simulate I/O or backup error during SQLite page backup
    with patch("infrastructure.paths.sqlite3.connect", side_effect=faulty_connect):
        with pytest.raises(sqlite3.OperationalError):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # 1. Source database remains intact and unmodified
    assert legacy_db.is_file()
    check_conn = sqlite3.connect(str(legacy_db))
    row = check_conn.execute("SELECT val FROM original;").fetchone()
    assert row[0] == "important_data"
    check_conn.close()

    # 2. Destination staging file was cleanly unlinked and destination DB was not created
    assert not (target_dir / "polpot.db.tmp_migrating").exists()
    assert not (target_dir / "polpot.db").exists()

    # 3. Migration marker was not written
    assert not (legacy_dir / ".migrated_to_platformdirs").exists()


def test_migration_safe_archive_overwrites_existing_migrated_directories(tmp_path: Path):
    """When legacy directory contains artifacts and artifacts.migrated already exists,
    the existing archive is preserved untouched, and the newly archived directory survives alongside it.
    """
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_artifacts = legacy_dir / "artifacts"
    legacy_artifacts.mkdir(parents=True)
    (legacy_artifacts / "new_doc.pdf").write_bytes(b"new content")

    # Pre-existing .migrated directory from an earlier archive run
    existing_migrated = legacy_dir / "artifacts.migrated"
    existing_migrated.mkdir(parents=True)
    (existing_migrated / "old_doc.pdf").write_bytes(b"old content")

    target_dir = tmp_path / "target"

    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
    # Target received new content
    assert (target_dir / "artifacts" / "new_doc.pdf").read_bytes() == b"new content"
    # Pre-existing archive was preserved completely intact
    assert (legacy_dir / "artifacts.migrated" / "old_doc.pdf").read_bytes() == b"old content"
    assert not (legacy_dir / "artifacts.migrated" / "new_doc.pdf").exists()
    # New archive was stored without collision in artifacts.migrated.1
    assert (legacy_dir / "artifacts.migrated.1" / "new_doc.pdf").read_bytes() == b"new content"


def test_migration_safe_archive_preserves_existing_migrated_files(tmp_path: Path):
    """When polpot.db.migrated already exists, the existing archive is preserved and the new archive uses .migrated.1."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "polpot.db"

    conn = sqlite3.connect(str(legacy_db))
    conn.execute("CREATE TABLE t (x TEXT);")
    conn.execute("INSERT INTO t VALUES ('new_db');")
    conn.commit()
    conn.close()

    # Pre-existing archive file
    existing_archive = legacy_dir / "polpot.db.migrated"
    existing_archive.write_bytes(b"pre_existing_archive_payload")

    target_dir = tmp_path / "target"

    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
    # Original archive file survived completely unmodified
    assert existing_archive.read_bytes() == b"pre_existing_archive_payload"
    # Newly archived database was moved to polpot.db.migrated.1
    new_archive = legacy_dir / "polpot.db.migrated.1"
    assert new_archive.is_file()
    check_conn = sqlite3.connect(str(new_archive))
    row = check_conn.execute("SELECT x FROM t;").fetchone()
    assert row[0] == "new_db"
    check_conn.close()


def test_migration_artifact_conflict_different_content_raises_error(tmp_path: Path):
    """When target artifacts directory already contains a file of the same name with different content,
    migration raises DataMigrationConflictError and preserves all source and destination data untouched.
    """
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_artifacts = legacy_dir / "artifacts" / "sub"
    legacy_artifacts.mkdir(parents=True)
    legacy_file = legacy_artifacts / "data.bin"
    legacy_file.write_bytes(b"legacy_unique_bytes")

    target_dir = tmp_path / "target"
    target_artifacts = target_dir / "artifacts" / "sub"
    target_artifacts.mkdir(parents=True)
    target_file = target_artifacts / "data.bin"
    target_file.write_bytes(b"target_conflicting_bytes")

    with pytest.raises(DataMigrationConflictError) as exc_info:
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    assert "Conflicting artifact files" in str(exc_info.value)
    # Both files remain completely intact and unmodified
    assert legacy_file.read_bytes() == b"legacy_unique_bytes"
    assert target_file.read_bytes() == b"target_conflicting_bytes"
    # Migration was aborted without moving or archiving legacy data
    assert (legacy_dir / "artifacts").is_dir()
    assert not (legacy_dir / "MIGRATION_RECEIPT.json").exists()


def test_migration_artifact_identical_content_merges_without_conflict(tmp_path: Path):
    """When target artifacts directory contains identical content, migration succeeds and merges new files."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_artifacts = legacy_dir / "artifacts"
    legacy_artifacts.mkdir(parents=True)
    (legacy_artifacts / "shared.txt").write_bytes(b"identical_payload")
    (legacy_artifacts / "legacy_only.txt").write_bytes(b"legacy_only_payload")

    target_dir = tmp_path / "target"
    target_artifacts = target_dir / "artifacts"
    target_artifacts.mkdir(parents=True)
    (target_artifacts / "shared.txt").write_bytes(b"identical_payload")
    (target_artifacts / "target_only.txt").write_bytes(b"target_only_payload")

    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated

    # Destination contains merged set
    assert (target_artifacts / "shared.txt").read_bytes() == b"identical_payload"
    assert (target_artifacts / "target_only.txt").read_bytes() == b"target_only_payload"
    assert (target_artifacts / "legacy_only.txt").read_bytes() == b"legacy_only_payload"


def test_migration_interrupted_after_db_publication_resumes_safely(tmp_path: Path):
    """When migration fails after database publication, a subsequent run resumes safely via journal."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "polpot.db"
    conn = sqlite3.connect(str(legacy_db))
    conn.execute("CREATE TABLE canary (v INT);")
    conn.execute("INSERT INTO canary VALUES (42);")
    conn.commit()
    conn.close()

    legacy_vault = legacy_dir / "credentials.enc"
    legacy_vault.write_bytes(b"vault_secret")

    target_dir = tmp_path / "target"

    # Inject failure during vault copy (after DB has been published)
    import shutil
    real_copy2 = shutil.copy2

    def failing_copy2(src, dst, *args, **kwargs):
        if "credentials.enc" in str(src):
            raise OSError("Injected vault copy I/O error")
        return real_copy2(src, dst, *args, **kwargs)

    with patch("infrastructure.paths.shutil.copy2", side_effect=failing_copy2):
        with pytest.raises(OSError, match="Injected vault copy I/O error"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # 1. Target DB exists and is intact
    assert (target_dir / "polpot.db").is_file()
    # 2. Vault was not published yet
    assert not (target_dir / "credentials.enc").exists()
    # 3. Source DB was NOT archived yet (preserved)
    assert legacy_db.is_file()
    # 4. In-progress journal exists
    assert (target_dir / ".migration_journal.json").is_file()

    # Retry migration without failure: must resume and complete cleanly
    retry_report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert retry_report.migrated
    assert (target_dir / "credentials.enc").read_bytes() == b"vault_secret"
    # Destination DB still valid
    check_conn = sqlite3.connect(str(target_dir / "polpot.db"))
    assert check_conn.execute("SELECT v FROM canary;").fetchone()[0] == 42
    check_conn.close()
    # Source was archived
    assert (legacy_dir / "polpot.db.migrated").is_file()
    assert (legacy_dir / "credentials.enc.migrated").is_file()
    # Receipt created and journal removed
    assert (legacy_dir / "MIGRATION_RECEIPT.json").is_file()
    assert not (target_dir / ".migration_journal.json").exists()


def test_migration_interrupted_during_source_archival_resumes_safely(tmp_path: Path):
    """When failure occurs during source archival, target is intact and retry finishes archival."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "polpot.db"
    conn = sqlite3.connect(str(legacy_db))
    conn.execute("CREATE TABLE arch_test (id INT);")
    conn.commit()
    conn.close()

    legacy_vault = legacy_dir / "credentials.enc"
    legacy_vault.write_bytes(b"vault_val")

    target_dir = tmp_path / "target"

    from infrastructure.paths import _safe_archive_path
    real_archive = _safe_archive_path

    fail_counter = 0

    def failing_archive(src, dst, *args, **kwargs):
        nonlocal fail_counter
        fail_counter += 1
        if fail_counter == 2:  # Fail on the second item to archive
            raise OSError("Injected archival I/O error")
        return real_archive(src, dst, *args, **kwargs)

    with patch("infrastructure.paths._safe_archive_path", side_effect=failing_archive):
        with pytest.raises(OSError, match="Injected archival I/O error"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Destination DB and vault were both published
    assert (target_dir / "polpot.db").is_file()
    assert (target_dir / "credentials.enc").is_file()
    # Migration was not completed yet
    assert not (legacy_dir / "MIGRATION_RECEIPT.json").exists()
    assert (target_dir / ".migration_journal.json").is_file()

    # Retry without error completes archival and writes receipt
    retry_report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert retry_report.migrated
    assert (legacy_dir / "MIGRATION_RECEIPT.json").is_file()
    assert not (target_dir / ".migration_journal.json").exists()



def test_migration_wal_checkpoint_failure_still_backs_up_consistently(tmp_path: Path):
    """When wal_checkpoint fails (e.g. read lock), backup API still copies committed WAL data."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "polpot.db"

    conn = sqlite3.connect(str(legacy_db))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE wal_check (msg TEXT);")
    conn.execute("INSERT INTO wal_check VALUES ('wal_survives_without_truncate');")
    conn.commit()
    conn.close()

    target_dir = tmp_path / "target"

    # Simulate checkpoint failure by using a connection factory that raises on wal_checkpoint
    real_connect = sqlite3.connect

    class FailingCheckpointConnection(sqlite3.Connection):
        def execute(self, sql, *params):
            if "wal_checkpoint" in sql:
                raise sqlite3.OperationalError("database is locked (simulated checkpoint failure)")
            return super().execute(sql, *params)

    def checkpoint_failing_connect(*args, **kwargs):
        kwargs["factory"] = FailingCheckpointConnection
        return real_connect(*args, **kwargs)

    with patch("infrastructure.paths.sqlite3.connect", side_effect=checkpoint_failing_connect):
        report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    assert report.migrated
    dest_db = target_dir / "polpot.db"
    assert dest_db.is_file()

    check_conn = sqlite3.connect(str(dest_db))
    row = check_conn.execute("SELECT msg FROM wal_check;").fetchone()
    assert row is not None
    assert row[0] == "wal_survives_without_truncate"
    check_conn.close()


def test_migration_receipt_json_created_and_valid(tmp_path: Path):
    """Successful migration writes a structured, valid MIGRATION_RECEIPT.json."""
    import json

    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "polpot.db").write_bytes(b"")
    conn = sqlite3.connect(str(legacy_dir / "polpot.db"))
    conn.execute("CREATE TABLE init (id INT);")
    conn.commit()
    conn.close()

    target_dir = tmp_path / "target"

    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated

    receipt_file = legacy_dir / "MIGRATION_RECEIPT.json"
    assert receipt_file.is_file()

    receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt_data["status"] == "completed"
    assert receipt_data["version"] == 1
    assert "migrated_at" in receipt_data
    assert receipt_data["source"] == str(legacy_dir.resolve())
    assert receipt_data["destination"] == str(target_dir.resolve())
    assert "polpot.db" in receipt_data["migrated_items"]

    target_receipt = target_dir / "MIGRATION_RECEIPT.json"
    assert target_receipt.is_file()


def test_repeated_startup_after_migration_with_container(tmp_path: Path):
    """Container initialization repeatedly succeeds after legacy migration."""
    from interfaces.desktop.composition import DesktopAppContainer

    base_dir = tmp_path / "isolated_user_data"
    base_dir.mkdir(parents=True)

    # Seed legacy dir
    legacy_dir = tmp_path / "legacy_source"
    legacy_dir.mkdir(parents=True)
    src_db = legacy_dir / "polpot.db"
    conn = sqlite3.connect(str(src_db))
    conn.execute("CREATE TABLE canary (id INT);")
    conn.commit()
    conn.close()

    # First migration run
    migrate_legacy_user_data_if_needed(target_dir=base_dir, legacy_dir=legacy_dir)
    assert (base_dir / "polpot.db").is_file()

    # First container startup
    container1 = DesktopAppContainer(
        db_path=base_dir / "polpot.db",
        artifacts_dir=base_dir / "artifacts",
        vault_path=base_dir / "credentials.enc",
        scheduler_tick_interval=100.0,
    )
    container1.initialize()
    container1.shutdown()

    # Second container startup (re-opening existing migrated database)
    container2 = DesktopAppContainer(
        db_path=base_dir / "polpot.db",
        artifacts_dir=base_dir / "artifacts",
        vault_path=base_dir / "credentials.enc",
        scheduler_tick_interval=100.0,
    )
    container2.initialize()

    # Verify canary table preserved across both startups
    check_conn = container2.db_manager.create_connection()
    try:
        tables = [r[0] for r in check_conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        assert "canary" in tables
        assert "schema_version" in tables
    finally:
        check_conn.close()

    container2.shutdown()


def test_migration_crash_after_db_publication_before_checkpoint_resumes_via_intent(tmp_path: Path):
    """When crash occurs right after db publication (before journal checkpoint), intent matches and resume succeeds."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "polpot.db"
    conn = sqlite3.connect(str(legacy_db))
    conn.execute("CREATE TABLE tbl (id INT);")
    conn.execute("INSERT INTO tbl VALUES (777);")
    conn.commit()
    conn.close()

    legacy_vault = legacy_dir / "credentials.enc"
    legacy_vault.write_bytes(b"vault_data")

    target_dir = tmp_path / "target"

    from infrastructure.paths import _save_journal
    real_save_journal = _save_journal
    crashed = False

    def crashing_save_journal(path, data):
        nonlocal crashed
        # If DB published is about to be checkpointed, simulate crash
        if "db_published" in data.get("stages_completed", []) and not crashed:
            crashed = True
            raise RuntimeError("Simulated crash right before db_published checkpoint")
        return real_save_journal(path, data)

    with patch("infrastructure.paths._save_journal", side_effect=crashing_save_journal):
        with pytest.raises(RuntimeError, match="Simulated crash right before db_published checkpoint"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Destination DB exists on disk
    target_db = target_dir / "polpot.db"
    assert target_db.is_file()
    # Journal recorded db intent, but db_published is NOT in stages_completed
    journal_file = target_dir / ".migration_journal.json"
    assert journal_file.is_file()
    import json
    journal_data = json.loads(journal_file.read_text(encoding="utf-8"))
    assert "db" in journal_data["intents"]
    assert "db_published" not in journal_data["stages_completed"]

    # Resume migration: must recognize target_db via intent, not raise conflict, and finish cleanly
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
    check_conn = sqlite3.connect(str(target_db))
    assert check_conn.execute("SELECT id FROM tbl;").fetchone()[0] == 777
    check_conn.close()
    assert (target_dir / "credentials.enc").read_bytes() == b"vault_data"
    assert (legacy_dir / "MIGRATION_RECEIPT.json").is_file()
    assert not journal_file.exists()


def test_migration_crash_after_vault_publication_before_checkpoint_resumes_via_intent(tmp_path: Path):
    """When crash occurs after vault publication before checkpoint, intent matches and resume succeeds."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_vault = legacy_dir / "credentials.enc"
    legacy_vault.write_bytes(b"secret_vault_payload")

    target_dir = tmp_path / "target"

    from infrastructure.paths import _save_journal
    real_save_journal = _save_journal
    crashed = False

    def crashing_save_journal(path, data):
        nonlocal crashed
        if "vault_published" in data.get("stages_completed", []) and not crashed:
            crashed = True
            raise RuntimeError("Simulated crash right before vault_published checkpoint")
        return real_save_journal(path, data)

    with patch("infrastructure.paths._save_journal", side_effect=crashing_save_journal):
        with pytest.raises(RuntimeError, match="Simulated crash right before vault_published checkpoint"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    assert (target_dir / "credentials.enc").is_file()
    journal_file = target_dir / ".migration_journal.json"
    assert journal_file.is_file()
    import json
    journal_data = json.loads(journal_file.read_text(encoding="utf-8"))
    assert "vault" in journal_data["intents"]
    assert "vault_published" not in journal_data["stages_completed"]

    # Resume succeeds
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
    assert (target_dir / "credentials.enc").read_bytes() == b"secret_vault_payload"
    assert not journal_file.exists()


def test_migration_crash_after_artifacts_published_before_checkpoint_resumes_via_intent(tmp_path: Path):
    """When crash occurs after artifact copy before checkpoint, manifest matches and resume succeeds."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_artifacts = legacy_dir / "artifacts" / "job_a"
    legacy_artifacts.mkdir(parents=True)
    (legacy_artifacts / "output.pdf").write_bytes(b"%PDF artifact")

    target_dir = tmp_path / "target"

    from infrastructure.paths import _save_journal
    real_save_journal = _save_journal
    crashed = False

    def crashing_save_journal(path, data):
        nonlocal crashed
        if "artifacts_published" in data.get("stages_completed", []) and not crashed:
            crashed = True
            raise RuntimeError("Simulated crash right before artifacts_published checkpoint")
        return real_save_journal(path, data)

    with patch("infrastructure.paths._save_journal", side_effect=crashing_save_journal):
        with pytest.raises(RuntimeError, match="Simulated crash right before artifacts_published checkpoint"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    assert (target_dir / "artifacts" / "job_a" / "output.pdf").is_file()
    journal_file = target_dir / ".migration_journal.json"
    assert journal_file.is_file()

    # Resume succeeds
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
    assert (target_dir / "artifacts" / "job_a" / "output.pdf").read_bytes() == b"%PDF artifact"
    assert not journal_file.exists()


def test_migration_crash_during_artifact_copy_cleans_tmp_and_resumes(tmp_path: Path):
    """When artifact copy fails halfway through, staging files are cleaned and retry succeeds."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_artifacts = legacy_dir / "artifacts"
    legacy_artifacts.mkdir(parents=True)
    (legacy_artifacts / "file1.txt").write_bytes(b"content 1")
    (legacy_artifacts / "file2.txt").write_bytes(b"content 2")

    target_dir = tmp_path / "target"

    import shutil
    real_copy2 = shutil.copy2

    def failing_copy(src, dst, *args, **kwargs):
        if "file2.txt" in str(src):
            raise OSError("Injected disk error on file2")
        return real_copy2(src, dst, *args, **kwargs)

    with patch("infrastructure.paths.shutil.copy2", side_effect=failing_copy):
        with pytest.raises(OSError, match="Injected disk error on file2"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Ensure no leftover .tmp_migrating files
    for root, _, files in os.walk(target_dir / "artifacts"):
        for fname in files:
            assert not fname.endswith(".tmp_migrating")

    # Retry without failure
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
    assert (target_dir / "artifacts" / "file1.txt").read_bytes() == b"content 1"
    assert (target_dir / "artifacts" / "file2.txt").read_bytes() == b"content 2"


def test_migration_resumed_with_tampered_destination_db_raises_conflict(tmp_path: Path):
    """If destination DB was altered between interrupted run and resume, abort with DataMigrationConflictError."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "polpot.db"
    conn = sqlite3.connect(str(legacy_db))
    conn.execute("CREATE TABLE t (x INT);")
    conn.commit()
    conn.close()

    target_dir = tmp_path / "target"

    from infrastructure.paths import _save_journal
    real_save_journal = _save_journal
    crashed = False

    def crashing_save_journal(path, data):
        nonlocal crashed
        if "db_published" in data.get("stages_completed", []) and not crashed:
            crashed = True
            raise RuntimeError("Crash after db publication")
        return real_save_journal(path, data)

    with patch("infrastructure.paths._save_journal", side_effect=crashing_save_journal):
        with pytest.raises(RuntimeError):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Tamper with target DB
    target_db = target_dir / "polpot.db"
    target_db.write_bytes(b"tampered_bytes_not_matching_sha256")

    # Resume must detect hash mismatch and raise DataMigrationConflictError
    with pytest.raises(DataMigrationConflictError, match="content differs from recorded migration intent"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_resumed_with_tampered_destination_vault_raises_conflict(tmp_path: Path):
    """If destination vault was altered between interrupted run and resume, abort with DataMigrationConflictError."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "credentials.enc").write_bytes(b"vault_original")

    target_dir = tmp_path / "target"

    from infrastructure.paths import _save_journal
    real_save_journal = _save_journal
    crashed = False

    def crashing_save_journal(path, data):
        nonlocal crashed
        if "vault_published" in data.get("stages_completed", []) and not crashed:
            crashed = True
            raise RuntimeError("Crash after vault publication")
        return real_save_journal(path, data)

    with patch("infrastructure.paths._save_journal", side_effect=crashing_save_journal):
        with pytest.raises(RuntimeError):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Tamper with target vault
    (target_dir / "credentials.enc").write_bytes(b"tampered_vault_bytes")

    with pytest.raises(DataMigrationConflictError, match="content differs from recorded migration intent"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_resumed_with_tampered_destination_artifact_raises_conflict(tmp_path: Path):
    """If destination artifact was altered between interrupted run and resume, abort with DataMigrationConflictError."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_artifacts = legacy_dir / "artifacts"
    legacy_artifacts.mkdir(parents=True)
    (legacy_artifacts / "data.bin").write_bytes(b"original_artifact_bytes")

    target_dir = tmp_path / "target"

    from infrastructure.paths import _save_journal
    real_save_journal = _save_journal
    crashed = False

    def crashing_save_journal(path, data):
        nonlocal crashed
        if "artifacts_published" in data.get("stages_completed", []) and not crashed:
            crashed = True
            raise RuntimeError("Crash after artifacts published")
        return real_save_journal(path, data)

    with patch("infrastructure.paths._save_journal", side_effect=crashing_save_journal):
        with pytest.raises(RuntimeError):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Tamper with target artifact
    (target_dir / "artifacts" / "data.bin").write_bytes(b"tampered_artifact_bytes")

    with pytest.raises(DataMigrationConflictError, match="differs from recorded migration intent"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_crash_between_legacy_receipt_and_target_receipt_resumes(tmp_path: Path):
    """When crash occurs after legacy receipt is written but before target receipt, retry finishes finalization."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "credentials.enc").write_bytes(b"vault_ok")

    target_dir = tmp_path / "target"

    target_receipt = target_dir / "MIGRATION_RECEIPT.json"

    # Inject crash before target receipt replace
    crashed = False
    real_replace = Path.replace

    def crashing_replace(self, target):
        nonlocal crashed
        if str(target) == str(target_receipt) and not crashed:
            crashed = True
            raise RuntimeError("Crash before writing target receipt")
        return real_replace(self, target)

    with patch("pathlib.Path.replace", crashing_replace):
        with pytest.raises(RuntimeError, match="Crash before writing target receipt"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Legacy receipt exists, target receipt does not
    assert (legacy_dir / "MIGRATION_RECEIPT.json").is_file()
    assert not target_receipt.exists()
    assert (target_dir / ".migration_journal.json").is_file()

    # Retry finishes finalization
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
    assert target_receipt.is_file()
    assert (legacy_dir / ".migrated_to_platformdirs").is_file()
    assert not (target_dir / ".migration_journal.json").exists()


def test_migration_crash_between_target_receipt_and_marker_resumes(tmp_path: Path):
    """When crash occurs after target receipt is written but before marker, retry finishes finalization."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "credentials.enc").write_bytes(b"vault_ok")

    target_dir = tmp_path / "target"
    marker_file = legacy_dir / ".migrated_to_platformdirs"

    crashed = False
    real_replace = Path.replace

    def crashing_replace(self, target):
        nonlocal crashed
        if str(target) == str(marker_file) and not crashed:
            crashed = True
            raise RuntimeError("Crash before writing completion marker")
        return real_replace(self, target)

    with patch("pathlib.Path.replace", crashing_replace):
        with pytest.raises(RuntimeError, match="Crash before writing completion marker"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    assert (legacy_dir / "MIGRATION_RECEIPT.json").is_file()
    assert (target_dir / "MIGRATION_RECEIPT.json").is_file()
    assert not marker_file.exists()
    assert (target_dir / ".migration_journal.json").is_file()

    # Retry finishes finalization
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
    assert marker_file.is_file()
    assert not (target_dir / ".migration_journal.json").exists()


def test_migration_resumes_when_all_legacy_data_already_archived(tmp_path: Path):
    """When crash occurs after all legacy files were archived, retry does not early-return no_legacy_data_present."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "polpot.db"
    conn = sqlite3.connect(str(legacy_db))
    conn.execute("CREATE TABLE arch_ok (id INT);")
    conn.commit()
    conn.close()

    target_dir = tmp_path / "target"
    legacy_receipt = legacy_dir / "MIGRATION_RECEIPT.json"

    crashed = False
    real_replace = Path.replace

    def crashing_replace(self, target):
        nonlocal crashed
        if str(target) == str(legacy_receipt) and not crashed:
            crashed = True
            raise RuntimeError("Crash right before writing legacy receipt")
        return real_replace(self, target)

    with patch("pathlib.Path.replace", crashing_replace):
        with pytest.raises(RuntimeError, match="Crash right before writing legacy receipt"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Verify legacy DB was archived already
    assert not legacy_db.exists()
    assert (legacy_dir / "polpot.db.migrated").is_file()
    # Receipt does not exist yet
    assert not legacy_receipt.exists()
    # In-progress journal exists
    assert (target_dir / ".migration_journal.json").is_file()

    # Retry: must NOT exit with no_legacy_data_present; it must finalize Stage 6 and succeed!
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
    assert report.reason == "migration_completed_successfully"
    assert legacy_receipt.is_file()
    assert (target_dir / "MIGRATION_RECEIPT.json").is_file()
    assert not (target_dir / ".migration_journal.json").exists()


def test_migration_corrupted_legacy_receipt_raises_conflict(tmp_path: Path):
    """Corrupted JSON in legacy receipt raises DataMigrationConflictError."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "MIGRATION_RECEIPT.json").write_text("{corrupt json", encoding="utf-8")

    target_dir = tmp_path / "target"

    with pytest.raises(DataMigrationConflictError, match="Corrupted migration receipt found"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_incomplete_status_legacy_receipt_raises_conflict(tmp_path: Path):
    """Receipt with non-completed status raises DataMigrationConflictError."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"

    receipt_data = {
        "version": 1,
        "status": "in_progress",
        "source": str(legacy_dir.resolve()),
        "destination": str(target_dir.resolve()),
    }
    (legacy_dir / "MIGRATION_RECEIPT.json").write_text(json.dumps(receipt_data), encoding="utf-8")

    with pytest.raises(DataMigrationConflictError, match="Incomplete migration receipt found"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_mismatched_legacy_receipt_raises_conflict(tmp_path: Path):
    """Receipt pointing to a different destination raises DataMigrationConflictError."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"

    receipt_data = {
        "version": 1,
        "status": "completed",
        "source": str(legacy_dir.resolve()),
        "destination": str((tmp_path / "other_target").resolve()),
    }
    (legacy_dir / "MIGRATION_RECEIPT.json").write_text(json.dumps(receipt_data), encoding="utf-8")

    with pytest.raises(DataMigrationConflictError, match="Mismatched migration receipt"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_corrupted_journal_quarantined_and_raises_conflict(tmp_path: Path):
    """Corrupted migration journal is quarantined and raises DataMigrationConflictError."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "polpot.db").write_bytes(b"")

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)
    journal_file = target_dir / ".migration_journal.json"
    journal_file.write_text("{bad json content", encoding="utf-8")

    with pytest.raises(DataMigrationConflictError, match="Corrupted migration journal found"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Active journal was quarantined
    assert not journal_file.exists()
    quarantined = list(target_dir.glob(".migration_journal.json.corrupt.*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text() == "{bad json content"


def test_migration_malformed_journal_missing_keys_quarantined(tmp_path: Path):
    """Journal missing required schema keys is quarantined and raises DataMigrationConflictError."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "polpot.db").write_bytes(b"")

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)
    journal_file = target_dir / ".migration_journal.json"
    # Missing stages_completed
    journal_file.write_text(json.dumps({"version": 1, "source": str(legacy_dir), "destination": str(target_dir)}))

    with pytest.raises(DataMigrationConflictError, match="Malformed migration journal found"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    assert not journal_file.exists()
    quarantined = list(target_dir.glob(".migration_journal.json.corrupt.*"))
    assert len(quarantined) == 1


def test_migration_mismatched_journal_raises_conflict(tmp_path: Path):
    """Journal specifying a different source/destination raises DataMigrationConflictError."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "polpot.db").write_bytes(b"")

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)
    journal_file = target_dir / ".migration_journal.json"
    journal_data = {
        "version": 1,
        "source": str(tmp_path / "unrelated_source"),
        "destination": str(target_dir),
        "stages_completed": [],
    }
    journal_file.write_text(json.dumps(journal_data))

    with pytest.raises(DataMigrationConflictError, match="Mismatched migration journal found"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_concurrency_lock_active_raises_conflict(tmp_path: Path):
    """When an active process holds .migration.lock, concurrent migration raises DataMigrationConflictError."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "credentials.enc").write_bytes(b"secret")

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    lock_file = target_dir / ".migration.lock"
    # Write current live process PID
    lock_file.write_text(json.dumps({"pid": os.getpid(), "created_at": "now"}))

    with pytest.raises(DataMigrationConflictError, match="Migration lockfile exists"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_concurrency_lock_stale_recovers(tmp_path: Path):
    """When .migration.lock belongs to a dead process, stale lock is cleared and migration proceeds."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "credentials.enc").write_bytes(b"secret")

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    lock_file = target_dir / ".migration.lock"
    # Write PID that definitely does not exist
    lock_file.write_text(json.dumps({"pid": 99999999, "created_at": "old"}))

    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
    assert not lock_file.exists()


def test_migration_active_database_lock_prevents_source_archival(tmp_path: Path):
    """When legacy database is actively locked by another process during archival, operation fails safely."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "polpot.db"
    conn = sqlite3.connect(str(legacy_db))
    conn.execute("CREATE TABLE locked_tbl (x INT);")
    conn.commit()
    conn.close()

    target_dir = tmp_path / "target"

    from infrastructure.paths import _save_journal
    real_save_journal = _save_journal
    holding_conn = None

    def lock_after_db_published(path, data):
        nonlocal holding_conn
        res = real_save_journal(path, data)
        # Acquire exclusive lock on legacy_db after db_published is checkpointed, before archival
        if "db_published" in data.get("stages_completed", []) and holding_conn is None:
            holding_conn = sqlite3.connect(str(legacy_db))
            holding_conn.execute("BEGIN EXCLUSIVE;")
        return res

    try:
        with patch("infrastructure.paths._save_journal", side_effect=lock_after_db_published):
            with pytest.raises(RuntimeError, match="database is locked by another process"):
                migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    finally:
        if holding_conn:
            holding_conn.rollback()
            holding_conn.close()

    # Legacy database was NOT moved or deleted; preserved on disk
    assert legacy_db.is_file()
    assert not (legacy_dir / "polpot.db.migrated").exists()


def test_migration_crash_after_file_archive_move_before_journal_update_resumes(tmp_path: Path):
    """Interruption after moving a file to archive but before updating journal reconciles on resume."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_vault = legacy_dir / "credentials.enc"
    legacy_vault.write_bytes(b"vault_secret_data")

    target_dir = tmp_path / "target"

    from infrastructure.paths import _safe_archive_path
    real_safe_archive = _safe_archive_path

    def crash_after_vault_move(src, dst, *args, **kwargs):
        res = real_safe_archive(src, dst, *args, **kwargs)
        if Path(src).name == "credentials.enc":
            raise RuntimeError("Simulated crash immediately after vault move before journal update")
        return res

    with patch("infrastructure.paths._safe_archive_path", side_effect=crash_after_vault_move):
        with pytest.raises(RuntimeError, match="Simulated crash immediately after vault move"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Vault was published to target
    assert (target_dir / "credentials.enc").read_bytes() == b"vault_secret_data"
    # Vault source is gone, archive exists
    assert not legacy_vault.exists()
    assert (legacy_dir / "credentials.enc.migrated").read_bytes() == b"vault_secret_data"
    # Migration not yet finalized
    assert not (legacy_dir / "MIGRATION_RECEIPT.json").exists()
    assert (target_dir / ".migration_journal.json").is_file()

    # Retry without error reconciles the pending archive intent and produces complete receipt
    retry_report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert retry_report.migrated

    receipt_file = legacy_dir / "MIGRATION_RECEIPT.json"
    assert receipt_file.is_file()
    import json
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt["archived_records"]["credentials.enc"] == str(legacy_dir / "credentials.enc.migrated")
    assert not (target_dir / ".migration_journal.json").exists()


def test_migration_crash_after_directory_archive_move_before_journal_update_resumes(tmp_path: Path):
    """Interruption after moving a directory to archive but before updating journal reconciles on resume."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_artifacts = legacy_dir / "artifacts"
    legacy_artifacts.mkdir(parents=True)
    (legacy_artifacts / "file1.txt").write_text("content1")
    sub = legacy_artifacts / "sub"
    sub.mkdir()
    (sub / "file2.txt").write_text("content2")

    target_dir = tmp_path / "target"

    from infrastructure.paths import _safe_archive_path
    real_safe_archive = _safe_archive_path

    def crash_after_artifacts_move(src, dst, *args, **kwargs):
        res = real_safe_archive(src, dst, *args, **kwargs)
        if Path(src).name == "artifacts":
            raise RuntimeError("Simulated crash immediately after artifacts directory move before journal update")
        return res

    with patch("infrastructure.paths._safe_archive_path", side_effect=crash_after_artifacts_move):
        with pytest.raises(RuntimeError, match="Simulated crash immediately after artifacts directory move"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Artifacts published to target
    assert (target_dir / "artifacts" / "file1.txt").read_text() == "content1"
    assert (target_dir / "artifacts" / "sub" / "file2.txt").read_text() == "content2"
    # Source artifacts dir is gone, archive dir exists
    assert not legacy_artifacts.exists()
    archived_dir = legacy_dir / "artifacts.migrated"
    assert (archived_dir / "file1.txt").read_text() == "content1"
    assert (archived_dir / "sub" / "file2.txt").read_text() == "content2"

    # Retry without error reconciles directory manifest and produces complete receipt
    retry_report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert retry_report.migrated

    receipt_file = legacy_dir / "MIGRATION_RECEIPT.json"
    assert receipt_file.is_file()
    import json
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt["archived_records"]["artifacts"] == str(archived_dir)
    assert not (target_dir / ".migration_journal.json").exists()


def test_migration_crash_after_collision_suffixed_archive_move_resumes(tmp_path: Path):
    """Interruption after moving an item to a collision-suffixed destination (.migrated.1) reconciles on resume."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_vault = legacy_dir / "credentials.enc"
    legacy_vault.write_bytes(b"new_secret")

    # Pre-existing archive file from an earlier archival event
    existing_archive = legacy_dir / "credentials.enc.migrated"
    existing_archive.write_bytes(b"prior_archive_secret")

    target_dir = tmp_path / "target"

    from infrastructure.paths import _safe_archive_path
    real_safe_archive = _safe_archive_path

    def crash_after_vault_move(src, dst, *args, **kwargs):
        res = real_safe_archive(src, dst, *args, **kwargs)
        if Path(src).name == "credentials.enc":
            raise RuntimeError("Simulated crash after collision-suffixed move")
        return res

    with patch("infrastructure.paths._safe_archive_path", side_effect=crash_after_vault_move):
        with pytest.raises(RuntimeError, match="Simulated crash after collision-suffixed move"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Collision-free suffix was used
    suffixed_archive = legacy_dir / "credentials.enc.migrated.1"
    assert existing_archive.read_bytes() == b"prior_archive_secret"
    assert suffixed_archive.read_bytes() == b"new_secret"
    assert not legacy_vault.exists()

    # Retry without error completes and attributes suffixed archive in receipt
    retry_report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert retry_report.migrated
    assert existing_archive.read_bytes() == b"prior_archive_secret"
    assert suffixed_archive.read_bytes() == b"new_secret"

    import json
    receipt = json.loads((legacy_dir / "MIGRATION_RECEIPT.json").read_text(encoding="utf-8"))
    assert receipt["archived_records"]["credentials.enc"] == str(suffixed_archive)


def test_migration_resumed_with_tampered_archive_raises_conflict(tmp_path: Path):
    """When an archive file is tampered with between interruption and resume, conflict is raised."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_vault = legacy_dir / "credentials.enc"
    legacy_vault.write_bytes(b"secret_data")

    target_dir = tmp_path / "target"

    from infrastructure.paths import _safe_archive_path
    real_safe_archive = _safe_archive_path

    def crash_after_vault_move(src, dst, *args, **kwargs):
        res = real_safe_archive(src, dst, *args, **kwargs)
        if Path(src).name == "credentials.enc":
            raise RuntimeError("Crash after vault move")
        return res

    with patch("infrastructure.paths._safe_archive_path", side_effect=crash_after_vault_move):
        with pytest.raises(RuntimeError, match="Crash after vault move"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    # Tamper with the archived file
    archived_file = legacy_dir / "credentials.enc.migrated"
    assert archived_file.is_file()
    archived_file.write_bytes(b"corrupted_tampered_bytes")

    with pytest.raises(DataMigrationConflictError, match="does not match recorded archive intent"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_journal_unsupported_version_quarantined(tmp_path: Path):
    """Journal with unsupported schema version is quarantined and raises DataMigrationConflictError."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "polpot.db").write_bytes(b"")

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)
    journal_file = target_dir / ".migration_journal.json"
    journal_file.write_text(json.dumps({
        "version": 99,
        "source": str(legacy_dir),
        "destination": str(target_dir),
        "stages_completed": [],
    }))

    with pytest.raises(DataMigrationConflictError, match="Unsupported journal version '99'"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    assert not journal_file.exists()
    assert len(list(target_dir.glob(".migration_journal.json.corrupt.*"))) == 1


def test_migration_journal_invalid_stage_name_quarantined(tmp_path: Path):
    """Journal containing an unrecognized stage name is quarantined and raises conflict."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "polpot.db").write_bytes(b"")

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)
    journal_file = target_dir / ".migration_journal.json"
    journal_file.write_text(json.dumps({
        "version": 1,
        "source": str(legacy_dir),
        "destination": str(target_dir),
        "stages_completed": ["invalid_stage_abc"],
    }))

    with pytest.raises(DataMigrationConflictError, match="Invalid stage 'invalid_stage_abc'"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)

    assert not journal_file.exists()
    assert len(list(target_dir.glob(".migration_journal.json.corrupt.*"))) == 1


def test_migration_journal_source_archived_without_publish_stages_quarantined(tmp_path: Path):
    """Journal claiming source_archived without any completed publish stage is rejected."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "polpot.db").write_bytes(b"")

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)
    journal_file = target_dir / ".migration_journal.json"
    journal_file.write_text(json.dumps({
        "version": 1,
        "source": str(legacy_dir),
        "destination": str(target_dir),
        "stages_completed": ["source_archived"],
        "archived_records": {"polpot.db": str(legacy_dir / "polpot.db.migrated")},
    }))

    with pytest.raises(DataMigrationConflictError, match="'source_archived' claimed without any completed publish stages"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_journal_source_archived_with_uncompleted_intent_quarantined(tmp_path: Path):
    """Journal claiming source_archived while a recorded publish intent was not completed is rejected."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    archived_vault = legacy_dir / "credentials.enc.migrated"
    archived_vault.write_bytes(b"vault")

    journal_file = target_dir / ".migration_journal.json"
    journal_file.write_text(json.dumps({
        "version": 1,
        "source": str(legacy_dir),
        "destination": str(target_dir),
        "stages_completed": ["vault_published", "source_archived"],
        "intents": {
            "db": {
                "target": str(target_dir / "polpot.db"),
                "sha256": "0" * 64,
                "size": 100,
            }
        },
        "archived_records": {"credentials.enc": str(archived_vault)},
    }))

    with pytest.raises(DataMigrationConflictError, match="db intent recorded but db_published not completed"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_journal_source_archived_empty_records_quarantined(tmp_path: Path):
    """Journal claiming source_archived with empty archived_records is rejected."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    journal_file = target_dir / ".migration_journal.json"
    journal_file.write_text(json.dumps({
        "version": 1,
        "source": str(legacy_dir),
        "destination": str(target_dir),
        "stages_completed": ["db_published", "source_archived"],
        "archived_records": {},
    }))

    with pytest.raises(DataMigrationConflictError, match="'source_archived' claimed but 'archived_records' is empty"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_journal_source_archived_missing_disk_file_quarantined(tmp_path: Path):
    """Journal claiming source_archived when archived file is missing on disk is rejected."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    missing_archived = legacy_dir / "nonexistent.migrated"

    journal_file = target_dir / ".migration_journal.json"
    journal_file.write_text(json.dumps({
        "version": 1,
        "source": str(legacy_dir),
        "destination": str(target_dir),
        "stages_completed": ["db_published", "source_archived"],
        "archived_records": {"polpot.db": str(missing_archived)},
    }))

    with pytest.raises(DataMigrationConflictError, match="claimed but archived file .* is missing on disk"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_journal_source_archived_legacy_source_still_exists_quarantined(tmp_path: Path):
    """Journal claiming source_archived while legacy source file still exists is rejected."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "polpot.db"
    legacy_db.write_bytes(b"data")

    archived_db = legacy_dir / "polpot.db.migrated"
    archived_db.write_bytes(b"data")

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    journal_file = target_dir / ".migration_journal.json"
    journal_file.write_text(json.dumps({
        "version": 1,
        "source": str(legacy_dir),
        "destination": str(target_dir),
        "stages_completed": ["db_published", "source_archived"],
        "archived_records": {"polpot.db": str(archived_db)},
    }))

    with pytest.raises(DataMigrationConflictError, match="claimed for polpot.db but legacy source still exists"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_journal_invalid_sha256_format_quarantined(tmp_path: Path):
    """Journal with invalid SHA-256 digest format in intent is rejected."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    journal_file = target_dir / ".migration_journal.json"
    journal_file.write_text(json.dumps({
        "version": 1,
        "source": str(legacy_dir),
        "destination": str(target_dir),
        "stages_completed": [],
        "intents": {
            "db": {
                "target": str(target_dir / "polpot.db"),
                "sha256": "not_a_valid_64_hex_hash",
                "size": 50,
            }
        },
    }))

    with pytest.raises(DataMigrationConflictError, match="Invalid SHA-256 digest in intent 'db'"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_journal_negative_file_size_quarantined(tmp_path: Path):
    """Journal with negative file size in intent is rejected."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    journal_file = target_dir / ".migration_journal.json"
    journal_file.write_text(json.dumps({
        "version": 1,
        "source": str(legacy_dir),
        "destination": str(target_dir),
        "stages_completed": [],
        "intents": {
            "vault": {
                "target": str(target_dir / "credentials.enc"),
                "sha256": "a" * 64,
                "size": -10,
            }
        },
    }))

    with pytest.raises(DataMigrationConflictError, match="Invalid file size in intent 'vault'"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_journal_manifest_parent_traversal_quarantined(tmp_path: Path):
    """Journal with parent directory traversal in artifact manifest is rejected."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    journal_file = target_dir / ".migration_journal.json"
    journal_file.write_text(json.dumps({
        "version": 1,
        "source": str(legacy_dir),
        "destination": str(target_dir),
        "stages_completed": [],
        "intents": {
            "artifacts": {
                "target": str(target_dir / "artifacts"),
                "manifest": [
                    {
                        "rel_path": "../traversal_attack.pdf",
                        "sha256": "b" * 64,
                        "size": 100,
                    }
                ],
            }
        },
    }))

    with pytest.raises(DataMigrationConflictError, match="Artifact manifest contains parent traversal"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_journal_manifest_absolute_path_quarantined(tmp_path: Path):
    """Journal with absolute path in artifact manifest is rejected."""
    import json
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    journal_file = target_dir / ".migration_journal.json"
    journal_file.write_text(json.dumps({
        "version": 1,
        "source": str(legacy_dir),
        "destination": str(target_dir),
        "stages_completed": [],
        "intents": {
            "artifacts": {
                "target": str(target_dir / "artifacts"),
                "manifest": [
                    {
                        "rel_path": "/etc/passwd",
                        "sha256": "c" * 64,
                        "size": 500,
                    }
                ],
            }
        },
    }))

    with pytest.raises(DataMigrationConflictError, match="Artifact manifest contains absolute path"):
        migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)


def test_migration_lock_owner_only_release(tmp_path: Path):
    """A lock instance does not unlink a lockfile that contains a different owner's token."""
    import json
    from infrastructure.paths import _MigrationLock

    lock_file = tmp_path / ".migration.lock"
    lock1 = _MigrationLock(lock_file)
    lock1.acquire()
    assert lock_file.exists()

    # Tamper with the lock token to represent another process claiming the lock
    data = json.loads(lock_file.read_text(encoding="utf-8"))
    data["token"] = "different_owner_token_12345"
    lock_file.write_text(json.dumps(data), encoding="utf-8")

    # lock1.release() should safely refuse to delete the file
    lock1.release()
    assert lock_file.exists()

    # Clean up manually
    lock_file.unlink()


def test_migration_lock_multi_process_contention(tmp_path: Path):
    """Concurrent process holding the lock causes migration to fail fast with conflict error."""
    import subprocess
    import sys
    import time

    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "credentials.enc").write_bytes(b"secret")

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)
    lock_file = target_dir / ".migration.lock"

    # Worker script that acquires the lock and sleeps
    worker_code = f"""
import time
from pathlib import Path
from infrastructure.paths import _MigrationLock

lock = _MigrationLock(Path(r"{lock_file}"))
lock.acquire()
time.sleep(1.5)
lock.release()
"""
    proc = subprocess.Popen([sys.executable, "-c", worker_code])
    try:
        # Wait for worker process to acquire lock
        for _ in range(50):
            if lock_file.exists():
                break
            time.sleep(0.05)
        assert lock_file.exists()

        # Concurrent migration must detect active holding PID and raise conflict
        with pytest.raises(DataMigrationConflictError, match="held by active process"):
            migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    finally:
        proc.wait(timeout=5)

    # After worker process exits and releases lock, retry succeeds cleanly
    report = migrate_legacy_user_data_if_needed(target_dir=target_dir, legacy_dir=legacy_dir)
    assert report.migrated
