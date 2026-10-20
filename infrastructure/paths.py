"""Path resolution for application assets, runtime binaries, and user data directories.

Provides deterministic location of bundled third-party runtimes (Pandoc, Node.js),
packaged assets across development checkouts and frozen binary bundles, cross-platform
user data and log directories via platformdirs, and safe legacy data migration.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import errno
import hashlib
import json
import ntpath
import os
from pathlib import Path, PurePath, PureWindowsPath
import re
import shutil
import sqlite3
import sys
from typing import Any, Optional, Union
from urllib.parse import urlparse
import uuid

import platformdirs

from core.entities.artifact import resolve_canonical_file_path


class DataMigrationConflictError(RuntimeError):
    """Raised when legacy and destination directories both contain conflicting data."""
    pass


@dataclass
class DataMigrationReport:
    """Diagnostic report describing legacy user data migration execution."""
    migrated: bool
    source: Path
    destination: Path
    reason: str
    migrated_items: list[str] = field(default_factory=list)


def get_runtime_resource_path(relative_path: str | Path) -> Path:
    """Resolve the absolute path to a bundled runtime binary or static resource.

    In a frozen bundle (e.g. PyInstaller or Briefcase):
    - Under PyInstaller, resources are extracted/packaged at `sys._MEIPASS`.
    - Under Briefcase or other frozen frameworks without `_MEIPASS`, resources
      are resolved relative to the executable root.

    In unfrozen development checkout:
    - Resources are resolved relative to the repository root (two levels above
      `infrastructure/paths.py`).

    Args:
        relative_path: Relative path string or Path from the application root
                       (e.g., 'resources/runtimes.json').

    Returns:
        Resolved absolute Path.
    """
    path_obj = Path(relative_path)
    if path_obj.is_absolute():
        return path_obj

    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS") and sys._MEIPASS:
            base_dir = Path(sys._MEIPASS)
        else:
            base_dir = Path(sys.executable).resolve().parent
    else:
        # Development checkout: repo root is the parent of the infrastructure directory
        base_dir = Path(__file__).resolve().parent.parent

    return (base_dir / path_obj).resolve()


def get_user_data_dir(app_slug: str = "polpot") -> Path:
    """Return the platform-appropriate writable user data directory.

    Respects XDG_DATA_HOME if explicitly set and non-empty in the environment
    on Linux/POSIX systems. On Windows and macOS, resolves standard per-platform
    locations via platformdirs:
    - Linux / POSIX: ~/.local/share/polpot (honoring XDG_DATA_HOME)
    - macOS: ~/Library/Application Support/polpot
    - Windows: %LOCALAPPDATA%/polpot

    Args:
        app_slug: Canonical directory slug for user data storage.

    Returns:
        Resolved absolute Path to user data directory.
    """
    if not sys.platform.startswith(("win", "darwin")):
        xdg_env = os.environ.get("XDG_DATA_HOME")
        if xdg_env and xdg_env.strip():
            return (Path(xdg_env).expanduser() / app_slug).resolve()

    return platformdirs.user_data_path(app_slug, appauthor=False).resolve()


def get_user_log_dir(app_slug: str = "polpot") -> Path:
    """Return the application log directory located inside the user data area.

    Args:
        app_slug: Canonical directory slug for user data storage.

    Returns:
        Resolved absolute Path to user logs directory.
    """
    return (get_user_data_dir(app_slug) / "logs").resolve()


def get_legacy_user_data_dir(app_slug: str = "polpot") -> Path:
    """Return the historical fallback user data directory (~/.local/share/polpot).

    Args:
        app_slug: Canonical directory slug for user data storage.

    Returns:
        Resolved absolute Path to historical legacy data directory.
    """
    return (Path.home() / ".local" / "share" / app_slug).resolve()


def get_collision_free_archive_path(base_dst: Path, reserved: Optional[set[Path]] = None) -> Path:
    """Computes a non-existent archive path based on base_dst.

    Guarantees:
    - Never unlinks, deletes, or overwrites any existing file or directory.
    - If base_dst does not exist and is not in reserved, returns base_dst.
    - If base_dst exists or is reserved, appends an incrementing counter suffix
      (.1, .2, ...) until an unused, unreserved path is found.

    Args:
        base_dst: Preferred archive destination path.
        reserved: Optional set of paths already reserved by the current operation.

    Returns:
        Non-existent, collision-free Path.
    """
    reserved_set = reserved or set()
    if not base_dst.exists() and base_dst not in reserved_set:
        return base_dst

    counter = 1
    while True:
        candidate = base_dst.with_name(f"{base_dst.name}.{counter}")
        if not candidate.exists() and candidate not in reserved_set:
            return candidate
        counter += 1


def _safe_archive_path(src: Path, preferred_dst: Path, reserved: Optional[set[Path]] = None) -> Optional[Path]:
    """Safely moves src to a collision-free archive destination.

    Guarantees:
    - Never unlinks, recursively deletes, or overwrites an existing archive.
    - If preferred_dst already exists, computes a collision-free path via
      get_collision_free_archive_path (.1, .2, ...).
    - Preserves all pre-existing archives intact alongside newly archived data.

    Args:
        src: Source file or directory to archive.
        preferred_dst: Target archive path (e.g., path.migrated).
        reserved: Optional set of paths already planned or written during this operation.

    Returns:
        The actual Path where src was archived, or None if src does not exist.
    """
    if not src.exists():
        return None
    actual_dst = get_collision_free_archive_path(preferred_dst, reserved)
    shutil.move(str(src), str(actual_dst))
    return actual_dst


def _detect_artifact_conflicts(legacy_dir: Path, target_dir: Path) -> list[str]:
    """Scans all files in legacy_dir and checks for collisions in target_dir.

    A conflict occurs when an item exists at the same relative path in both directories
    and either:
    1. One is a file and the other is a directory.
    2. Both are files but their binary contents differ.

    Files with identical binary content are safe to merge and not flagged as conflicts.

    Args:
        legacy_dir: Source legacy artifacts directory.
        target_dir: Target destination artifacts directory.

    Returns:
        Sorted list of relative path strings of conflicting files.
    """
    conflicts: list[str] = []
    if not legacy_dir.is_dir() or not target_dir.is_dir():
        return conflicts

    for root, dirs, files in os.walk(legacy_dir):
        rel_root = Path(root).relative_to(legacy_dir)
        for fname in files:
            src_file = Path(root) / fname
            rel_file = rel_root / fname if str(rel_root) != "." else Path(fname)
            dest_file = target_dir / rel_file
            if dest_file.exists():
                if not dest_file.is_file() or not src_file.is_file():
                    conflicts.append(str(rel_file))
                else:
                    if src_file.read_bytes() != dest_file.read_bytes():
                        conflicts.append(str(rel_file))

        for dname in dirs:
            rel_dir = rel_root / dname if str(rel_root) != "." else Path(dname)
            dest_dir = target_dir / rel_dir
            if dest_dir.exists() and not dest_dir.is_dir():
                conflicts.append(str(rel_dir))

    return sorted(conflicts)


HEX_64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_STAGES = {"db_published", "vault_published", "artifacts_published", "source_archived"}


def _compute_file_sha256(path: Path) -> str:
    """Compute SHA-256 digest of a file in streaming chunks."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _compute_directory_manifest(dir_path: Path) -> list[dict[str, Any]]:
    """Compute sorted relative manifest of files with SHA-256 and size in a directory."""
    manifest: list[dict[str, Any]] = []
    if not dir_path.is_dir():
        return manifest
    for root, dirs, files in os.walk(dir_path):
        rel_root = Path(root).relative_to(dir_path)
        for fname in sorted(files):
            fpath = Path(root) / fname
            rel_f = str(rel_root / fname if str(rel_root) != "." else Path(fname))
            manifest.append({
                "rel_path": rel_f,
                "sha256": _compute_file_sha256(fpath),
                "size": fpath.stat().st_size,
            })
    return sorted(manifest, key=lambda x: x["rel_path"])


def _verify_archived_content(path: Path, intent: dict[str, Any]) -> bool:
    """Verifies that an archived file or directory matches its recorded archive intent."""
    if intent.get("is_dir", False):
        if not path.is_dir():
            return False
        expected_manifest = intent.get("manifest", [])
        actual_manifest = _compute_directory_manifest(path)
        if len(expected_manifest) != len(actual_manifest):
            return False
        for exp, act in zip(expected_manifest, actual_manifest):
            if exp["rel_path"] != act["rel_path"] or exp["sha256"] != act["sha256"] or exp["size"] != act["size"]:
                return False
        return True
    else:
        if not path.is_file():
            return False
        if _compute_file_sha256(path) != intent.get("sha256"):
            return False
        if path.stat().st_size != intent.get("size"):
            return False
        return True


def _save_journal(journal_path: Path, data: dict[str, Any]) -> None:
    """Atomically and durably persist migration journal state to disk.

    Durability guarantees:
    - Writes to a temporary sibling file, flushes userspace buffers, and invokes
      os.fsync() on the file descriptor.
    - Atomically replaces the destination file using os.replace().
    - On POSIX platforms, invokes os.fsync() on the parent directory to persist
      the directory entry change.
    - Guarantees durability across process crashes, unhandled exceptions, and
      process termination signals (SIGTERM/SIGKILL).
    - Platform limitation: Hardware-level power-loss durability is constrained by
      OS disk write-cache policies and is not guaranteed on Windows or filesystems
      that ignore directory fsync.
    """
    temp_journal = journal_path.with_name(f"{journal_path.name}.tmp.{uuid.uuid4().hex[:8]}")
    serialized = json.dumps(data, indent=2)
    with open(temp_journal, "w", encoding="utf-8") as f:
        f.write(serialized)
        f.flush()
        os.fsync(f.fileno())

    temp_journal.replace(journal_path)

    # Sync parent directory on POSIX systems where supported
    if not sys.platform.startswith("win"):
        try:
            dir_fd = os.open(str(journal_path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass


def _quarantine_and_raise(journal_path: Path, reason: str, exc: Optional[Exception] = None) -> None:
    """Quarantine a malformed or corrupted journal file and raise DataMigrationConflictError."""
    quarantine_name = f"{journal_path.name}.corrupt.{int(datetime.now(timezone.utc).timestamp())}"
    quarantine_path = journal_path.with_name(quarantine_name)
    try:
        journal_path.replace(quarantine_path)
    except Exception:
        pass
    kind = "Corrupted" if ("JSON parse error" in reason or "corrupt" in reason.lower()) else "Malformed"
    msg = (
        f"{kind} migration journal found at '{journal_path}' ({reason}). "
        f"Quarantined to '{quarantine_name}'. Migration aborted to prevent data loss."
    )
    if exc is not None:
        raise DataMigrationConflictError(msg) from exc
    raise DataMigrationConflictError(msg)


def _load_and_validate_journal(
    journal_path: Path, expected_source: Path, expected_dest: Path
) -> tuple[dict[str, Any], bool]:
    """Inspects, validates schema, and checks source/dest matching and stage consistency for an in-progress journal.

    Validation rules:
    - Supported journal version (integer 1).
    - Exact expected source and destination identities.
    - Allowed stages_completed values and valid stage dependencies.
    - Required types and contents of intents, archive_intents, and archived_records.
    - Valid SHA-256 digests (64-character lowercase hex) and non-negative file sizes.
    - Artifact manifest entries with safe relative paths (rejects absolute paths and parent traversal).
    - Consistency between source_archived, archived_records, and actual archive state on disk.

    Returns:
        tuple of (journal_data, is_resuming).

    Raises:
        DataMigrationConflictError: If the journal is malformed, corrupted, inconsistent,
                                    or specifies a different source or destination directory.
    """
    if not journal_path.exists():
        return {}, False

    try:
        content = journal_path.read_text(encoding="utf-8")
        loaded = json.loads(content)
        if not isinstance(loaded, dict):
            _quarantine_and_raise(journal_path, "Journal root must be a JSON object")
    except Exception as exc:
        _quarantine_and_raise(journal_path, f"JSON parse error: {exc}", exc=exc)

    # 1. Version validation
    version = loaded.get("version")
    if version != 1 or isinstance(version, bool):
        _quarantine_and_raise(journal_path, f"Unsupported journal version '{version}'. Expected integer 1")

    # 2. Source and Destination validation
    if loaded.get("source") != str(expected_source) or loaded.get("destination") != str(expected_dest):
        raise DataMigrationConflictError(
            f"Mismatched migration journal found at '{journal_path}'. Journal records source '{loaded.get('source')}' "
            f"and destination '{loaded.get('destination')}', but current migration requested source "
            f"'{expected_source}' and destination '{expected_dest}'. Resolve manually to prevent data corruption."
        )

    # 3. Stages completed validation
    stages = loaded.get("stages_completed")
    if not isinstance(stages, list):
        _quarantine_and_raise(journal_path, "'stages_completed' must be a list")

    for stage in stages:
        if stage not in ALLOWED_STAGES:
            _quarantine_and_raise(journal_path, f"Invalid stage '{stage}' in 'stages_completed'")

    # 4. Intents validation
    intents = loaded.get("intents")
    if intents is not None:
        if not isinstance(intents, dict):
            _quarantine_and_raise(journal_path, "'intents' must be a dictionary")
        for itype, ival in intents.items():
            if itype not in {"db", "vault", "artifacts"}:
                _quarantine_and_raise(journal_path, f"Unknown intent type '{itype}'")
            if not isinstance(ival, dict):
                _quarantine_and_raise(journal_path, f"Intent value for '{itype}' must be a dictionary")

            target_val = ival.get("target")
            if not isinstance(target_val, str) or not target_val.strip():
                _quarantine_and_raise(journal_path, f"Missing or invalid target path in intent '{itype}'")

            if itype in {"db", "vault"}:
                sha = ival.get("sha256")
                size = ival.get("size")
                if not isinstance(sha, str) or not HEX_64_PATTERN.match(sha):
                    _quarantine_and_raise(journal_path, f"Invalid SHA-256 digest in intent '{itype}': '{sha}'")
                if not isinstance(size, int) or size < 0:
                    _quarantine_and_raise(journal_path, f"Invalid file size in intent '{itype}': {size}")
            elif itype == "artifacts":
                manifest = ival.get("manifest")
                if not isinstance(manifest, list):
                    _quarantine_and_raise(journal_path, "Artifact manifest must be a list")
                for entry in manifest:
                    if not isinstance(entry, dict):
                        _quarantine_and_raise(journal_path, "Artifact manifest entry must be a dictionary")
                    rel_p = entry.get("rel_path")
                    if not isinstance(rel_p, str) or not rel_p.strip():
                        _quarantine_and_raise(journal_path, "Missing 'rel_path' in artifact manifest entry")
                    path_obj = Path(rel_p)
                    if path_obj.is_absolute() or rel_p.startswith(("/", "\\")) or (len(rel_p) > 1 and rel_p[1] == ":"):
                        _quarantine_and_raise(journal_path, f"Artifact manifest contains absolute path '{rel_p}'")
                    if ".." in path_obj.parts:
                        _quarantine_and_raise(journal_path, f"Artifact manifest contains parent traversal '{rel_p}'")
                    sha = entry.get("sha256")
                    size = entry.get("size")
                    if not isinstance(sha, str) or not HEX_64_PATTERN.match(sha):
                        _quarantine_and_raise(journal_path, f"Invalid SHA-256 digest in artifact manifest entry: '{sha}'")
                    if not isinstance(size, int) or size < 0:
                        _quarantine_and_raise(journal_path, f"Invalid size in artifact manifest entry: {size}")

    # 5. Archive intents validation
    archive_intents = loaded.get("archive_intents")
    if archive_intents is not None:
        if not isinstance(archive_intents, dict):
            _quarantine_and_raise(journal_path, "'archive_intents' must be a dictionary")
        for aitem, ainfo in archive_intents.items():
            if not isinstance(ainfo, dict):
                _quarantine_and_raise(journal_path, f"Archive intent for '{aitem}' must be a dictionary")
            src_str = ainfo.get("source")
            dst_str = ainfo.get("planned_dst")
            if not isinstance(src_str, str) or not isinstance(dst_str, str):
                _quarantine_and_raise(journal_path, f"Invalid source or planned_dst in archive intent '{aitem}'")
            if not ainfo.get("is_dir", False):
                sha = ainfo.get("sha256")
                size = ainfo.get("size")
                if not isinstance(sha, str) or not HEX_64_PATTERN.match(sha):
                    _quarantine_and_raise(journal_path, f"Invalid SHA-256 in archive intent '{aitem}'")
                if not isinstance(size, int) or size < 0:
                    _quarantine_and_raise(journal_path, f"Invalid size in archive intent '{aitem}'")

    # 6. Archived records validation
    archived_records = loaded.get("archived_records")
    if archived_records is not None:
        if not isinstance(archived_records, dict):
            _quarantine_and_raise(journal_path, "'archived_records' must be a dictionary")
        for k, v in archived_records.items():
            if not isinstance(k, str) or not isinstance(v, str):
                _quarantine_and_raise(journal_path, "Archived record key and value must be strings")

    # 7. Stage Dependency and Archival Consistency Validation
    if "source_archived" in stages:
        pub_stages = {"db_published", "vault_published", "artifacts_published"} & set(stages)
        if not pub_stages:
            _quarantine_and_raise(journal_path, "'source_archived' claimed without any completed publish stages")

        # Verify all recorded intents were completed
        if intents:
            if "db" in intents and "db_published" not in stages:
                _quarantine_and_raise(journal_path, "db intent recorded but db_published not completed")
            if "vault" in intents and "vault_published" not in stages:
                _quarantine_and_raise(journal_path, "vault intent recorded but vault_published not completed")
            if "artifacts" in intents and "artifacts_published" not in stages:
                _quarantine_and_raise(journal_path, "artifacts intent recorded but artifacts_published not completed")

        # Verify that archived_records is non-empty and every archived item actually exists on disk
        if not archived_records:
            _quarantine_and_raise(journal_path, "'source_archived' claimed but 'archived_records' is empty")

        for rname, rpath_str in archived_records.items():
            rpath = Path(rpath_str)
            if not rpath.exists():
                _quarantine_and_raise(journal_path, f"'source_archived' claimed but archived file '{rpath}' is missing on disk")

        # Verify that legacy source files do not still exist on disk alongside source_archived claim
        legacy_db = expected_source / "polpot.db"
        legacy_vault = expected_source / "credentials.enc"
        legacy_artifacts = expected_source / "artifacts"
        if "polpot.db" in archived_records and legacy_db.exists():
            _quarantine_and_raise(journal_path, f"'source_archived' claimed for polpot.db but legacy source still exists at '{legacy_db}'")
        if "credentials.enc" in archived_records and legacy_vault.exists():
            _quarantine_and_raise(journal_path, f"'source_archived' claimed for credentials.enc but legacy source still exists at '{legacy_vault}'")
        if "artifacts" in archived_records and legacy_artifacts.exists():
            _quarantine_and_raise(journal_path, f"'source_archived' claimed for artifacts but legacy source still exists at '{legacy_artifacts}'")

    return loaded, True


def _validate_receipt(
    receipt_path: Path, expected_source: Path, expected_dest: Path
) -> dict[str, Any]:
    """Validates an existing MIGRATION_RECEIPT.json.

    Returns:
        Parsed receipt dictionary.

    Raises:
        DataMigrationConflictError: If receipt is corrupt, incomplete, or points to mismatched paths.
    """
    try:
        content = receipt_path.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("Receipt root must be a JSON object.")
    except Exception as exc:
        raise DataMigrationConflictError(
            f"Corrupted migration receipt found at '{receipt_path}': {exc}. Resolve manually."
        ) from exc

    if data.get("status") != "completed":
        raise DataMigrationConflictError(
            f"Incomplete migration receipt found at '{receipt_path}' with status '{data.get('status')}'. "
            "Migration cannot be assumed complete. Resolve manually."
        )

    if data.get("source") != str(expected_source) or data.get("destination") != str(expected_dest):
        raise DataMigrationConflictError(
            f"Mismatched migration receipt at '{receipt_path}': specifies source '{data.get('source')}' "
            f"and destination '{data.get('destination')}', but current configuration expects source "
            f"'{expected_source}' and destination '{expected_dest}'. Resolve manually."
        )

    return data


class _MigrationLock:
    """Inter-process cooperative lock to prevent concurrent migrations on the same target.

    Safety & Concurrency Guarantees:
    - Generates a unique instance token (UUID4) recorded in the lockfile payload.
    - Uses atomic O_CREAT | O_EXCL to claim the lock.
    - Stale-lock recovery verifies process liveness via OS primitives (os.kill(pid, 0)
      or Windows OpenProcess) and serializes recovery through an auxiliary atomic
      recovery lock (.migration.lock.recovery).
    - Prevents stale-recovery races: re-verifies dead-PID state under the recovery
      lock before replacing, ensuring a newly acquired lock by another process is never unlinked.
    - Owner-only release: release() strictly verifies that the lockfile on disk contains
      its own unique token before unlinking.
    """

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd: Optional[int] = None
        self._token = uuid.uuid4().hex
        self._pid = os.getpid()

    def acquire(self) -> None:
        payload = json.dumps({
            "pid": self._pid,
            "token": self._token,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        try:
            self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(self._fd, payload.encode("utf-8"))
            return
        except FileExistsError:
            pass

        # File exists: inspect for liveness and attempt safe recovery
        self._attempt_stale_recovery(payload)

    def _attempt_stale_recovery(self, payload: str) -> None:
        is_stale, lock_pid = self._check_lock_stale()
        if not is_stale:
            raise DataMigrationConflictError(
                f"Migration lockfile exists at '{self.lock_path}' and is held by active process (PID {lock_pid}). "
                "Another migration process may be running. Wait for it to finish."
            )

        # Stale lock candidate detected: serialize recovery via atomic recovery lock
        recovery_path = self.lock_path.with_name(f"{self.lock_path.name}.recovery")
        recovery_fd: Optional[int] = None
        try:
            recovery_fd = os.open(str(recovery_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            # Another process is currently executing recovery
            raise DataMigrationConflictError(
                f"Migration lock recovery contention at '{self.lock_path}'. Another process is currently resolving the lock."
            )

        try:
            # Under recovery lock, re-verify whether self.lock_path is STILL stale
            is_stale_again, current_pid = self._check_lock_stale()
            if not is_stale_again and current_pid is not None:
                raise DataMigrationConflictError(
                    f"Migration lockfile at '{self.lock_path}' was acquired by active process (PID {current_pid})."
                )

            # Unlink verified stale lock
            if self.lock_path.exists():
                try:
                    self.lock_path.unlink()
                except OSError:
                    pass

            self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(self._fd, payload.encode("utf-8"))
        finally:
            if recovery_fd is not None:
                try:
                    os.close(recovery_fd)
                except OSError:
                    pass
            if recovery_path.exists():
                try:
                    recovery_path.unlink()
                except OSError:
                    pass

    def _check_lock_stale(self) -> tuple[bool, Optional[int]]:
        """Returns (is_stale, pid)."""
        if not self.lock_path.exists():
            return True, None
        try:
            content = json.loads(self.lock_path.read_text(encoding="utf-8"))
            pid = content.get("pid")
            if not isinstance(pid, int):
                return True, None
            if not self._is_pid_alive(pid):
                return True, pid
            return False, pid
        except Exception:
            # Corrupted lock content is treated as stale/invalid
            return True, None

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        if sys.platform == "win32":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                SYNCHRONIZE = 0x00100000
                h_proc = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
                if h_proc == 0:
                    return False
                kernel32.CloseHandle(h_proc)
                return True
            except Exception:
                return True
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError as err:
                return err.errno == errno.EPERM

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        if self.lock_path.exists():
            try:
                content = json.loads(self.lock_path.read_text(encoding="utf-8"))
                if content.get("token") == self._token:
                    self.lock_path.unlink()
            except Exception:
                pass


@contextmanager
def _migration_lock(target_dir: Path):
    lock = _MigrationLock(target_dir / ".migration.lock")
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def _is_windows_path(p: Union[str, PurePath]) -> bool:
    """Detects whether a path string or PurePath represents a Windows drive or UNC path."""
    s = str(p)
    return (len(s) >= 2 and s[0].isalpha() and s[1] == ":") or s.startswith(("\\\\", "//"))


def _is_local_uri_authority(netloc: str) -> bool:
    """Checks whether a file URI authority component refers to the local filesystem.

    Under RFC 8089 Section 2, the authority component of a file URI denotes the host.
    An empty authority (e.g. file:///path) or the case-insensitive string 'localhost'
    (e.g. file://localhost/path) explicitly identifies the local host.
    In legacy Windows two-slash compatibility forms (e.g. file://C:/path), Python's
    urlparse treats the drive letter (e.g. 'C:') as the netloc; this also represents
    a local Windows drive rather than a remote network authority.

    Any other non-empty authority (e.g. 'remote-host', IP addresses, UNC server names)
    denotes a non-local or foreign host. Such URIs must not have their authority
    stripped or discarded when determining whether an artifact belongs to the local
    legacy migration root.
    """
    if not netloc or netloc.lower() == "localhost":
        return True
    if len(netloc) == 2 and netloc[0].isalpha() and netloc[1] == ":":
        return True
    return False


def _remap_persisted_artifact_path(
    path_or_uri: Optional[str],
    legacy_artifacts_root: Union[Path, PurePath],
    target_artifacts_root: Union[Path, PurePath],
) -> Optional[str]:
    """Safely remaps a persisted artifact path/URI if it points inside legacy_artifacts_root.

    Preserves:
    - Standard RFC 8089 file:/// URIs, percent encoding (e.g. %20 and %2520), and raw filesystem paths.
    - Relative paths beneath the artifact root.
    - External file references, unrelated URIs, and escaping paths without modification.
    """
    if path_or_uri is None:
        return None
    raw_val = str(path_or_uri).strip()
    if not raw_val:
        return path_or_uri

    try:
        parsed = urlparse(raw_val)
    except Exception:
        parsed = None

    is_uri = parsed is not None and parsed.scheme.lower() == "file"
    if is_uri:
        if not _is_local_uri_authority(parsed.netloc):
            return path_or_uri

    try:
        resolved_candidate = resolve_canonical_file_path(raw_val)
    except Exception:
        return path_or_uri

    is_win = (
        _is_windows_path(legacy_artifacts_root)
        or _is_windows_path(target_artifacts_root)
        or _is_windows_path(resolved_candidate)
    )

    if is_win:
        legacy_norm = ntpath.normpath(str(legacy_artifacts_root))
        target_norm = ntpath.normpath(str(target_artifacts_root))
        candidate_norm = ntpath.normpath(str(resolved_candidate))

        legacy_pure = PureWindowsPath(legacy_norm)
        target_pure = PureWindowsPath(target_norm)
        candidate_pure = PureWindowsPath(candidate_norm)

        try:
            if not candidate_pure.is_relative_to(legacy_pure):
                return path_or_uri
            rel = candidate_pure.relative_to(legacy_pure)
        except ValueError:
            return path_or_uri

        if not rel.parts or rel.parts == (".",):
            return path_or_uri

        new_target = target_pure / rel
        if is_uri:
            return new_target.as_uri()
        if "/" in raw_val and "\\" not in raw_val:
            return new_target.as_posix()
        return str(new_target)

    else:
        legacy_path = Path(legacy_artifacts_root)
        target_path = Path(target_artifacts_root)
        candidate_path = Path(resolved_candidate)

        legacy_resolved = legacy_path.resolve()
        target_resolved = target_path.resolve()
        candidate_resolved = candidate_path.resolve()

        try:
            if not candidate_resolved.is_relative_to(legacy_resolved):
                return path_or_uri
            rel = candidate_resolved.relative_to(legacy_resolved)
        except ValueError:
            return path_or_uri

        if not rel.parts or rel.parts == (".",):
            return path_or_uri

        new_target = target_resolved / rel
        if is_uri:
            return new_target.as_uri()
        return str(new_target)


def _remap_database_artifact_references(
    conn: sqlite3.Connection,
    legacy_artifacts_root: Path,
    target_artifacts_root: Path,
) -> int:
    """Inspects SQLite schema and updates all persisted application-owned artifact references.

    Updates:
    - jobs.output_path
    - jobs.file_path (only if pointing inside legacy_artifacts_root)
    - document_versions.output_path (if table exists)
    - pipeline2_jobs.input_path and output_path (if table exists)
    - visual_regions.active_artifact_uri (if table exists)

    Returns:
        Total number of record fields updated.
    """
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    existing_tables = {row[0] for row in cur.fetchall()}

    remapped_count = 0

    # 1. Primary jobs table
    if "jobs" in existing_tables:
        cur.execute("PRAGMA table_info(jobs);")
        job_cols = {row[1] for row in cur.fetchall()}

        if "output_path" in job_cols:
            cur.execute("SELECT id, output_path FROM jobs WHERE output_path IS NOT NULL AND TRIM(output_path) != '';")
            for jid, out_p in cur.fetchall():
                new_p = _remap_persisted_artifact_path(out_p, legacy_artifacts_root, target_artifacts_root)
                if new_p != out_p:
                    cur.execute("UPDATE jobs SET output_path = ? WHERE id = ?;", (new_p, jid))
                    remapped_count += 1

        if "file_path" in job_cols:
            cur.execute("SELECT id, file_path FROM jobs WHERE file_path IS NOT NULL AND TRIM(file_path) != '';")
            for jid, f_p in cur.fetchall():
                new_p = _remap_persisted_artifact_path(f_p, legacy_artifacts_root, target_artifacts_root)
                if new_p != f_p:
                    cur.execute("UPDATE jobs SET file_path = ? WHERE id = ?;", (new_p, jid))
                    remapped_count += 1

    # 2. Immutable document versions table (schema 004+)
    if "document_versions" in existing_tables:
        cur.execute("PRAGMA table_info(document_versions);")
        doc_cols = {row[1] for row in cur.fetchall()}

        if "output_path" in doc_cols:
            cur.execute(
                "SELECT id, output_path FROM document_versions WHERE output_path IS NOT NULL AND TRIM(output_path) != '';"
            )
            for did, out_p in cur.fetchall():
                new_p = _remap_persisted_artifact_path(out_p, legacy_artifacts_root, target_artifacts_root)
                if new_p != out_p:
                    cur.execute("UPDATE document_versions SET output_path = ? WHERE id = ?;", (new_p, did))
                    remapped_count += 1

    # 3. Pipeline 2 jobs table
    if "pipeline2_jobs" in existing_tables:
        cur.execute("PRAGMA table_info(pipeline2_jobs);")
        p2_cols = {row[1] for row in cur.fetchall()}

        if "input_path" in p2_cols:
            cur.execute(
                "SELECT id, input_path FROM pipeline2_jobs WHERE input_path IS NOT NULL AND TRIM(input_path) != '';"
            )
            for p2id, in_p in cur.fetchall():
                new_p = _remap_persisted_artifact_path(in_p, legacy_artifacts_root, target_artifacts_root)
                if new_p != in_p:
                    cur.execute("UPDATE pipeline2_jobs SET input_path = ? WHERE id = ?;", (new_p, p2id))
                    remapped_count += 1

        if "output_path" in p2_cols:
            cur.execute(
                "SELECT id, output_path FROM pipeline2_jobs WHERE output_path IS NOT NULL AND TRIM(output_path) != '';"
            )
            for p2id, out_p in cur.fetchall():
                new_p = _remap_persisted_artifact_path(out_p, legacy_artifacts_root, target_artifacts_root)
                if new_p != out_p:
                    cur.execute("UPDATE pipeline2_jobs SET output_path = ? WHERE id = ?;", (new_p, p2id))
                    remapped_count += 1

    # 4. Visual regions table (schema 002+)
    if "visual_regions" in existing_tables:
        cur.execute("PRAGMA table_info(visual_regions);")
        vr_cols = {row[1] for row in cur.fetchall()}

        if "active_artifact_uri" in vr_cols:
            cur.execute(
                "SELECT id, active_artifact_uri FROM visual_regions "
                "WHERE active_artifact_uri IS NOT NULL AND TRIM(active_artifact_uri) != '';"
            )
            for vrid, art_uri in cur.fetchall():
                new_uri = _remap_persisted_artifact_path(art_uri, legacy_artifacts_root, target_artifacts_root)
                if new_uri != art_uri:
                    cur.execute("UPDATE visual_regions SET active_artifact_uri = ? WHERE id = ?;", (new_uri, vrid))
                    remapped_count += 1

    return remapped_count


def migrate_legacy_user_data_if_needed(
    target_dir: Optional[Path] = None,
    legacy_dir: Optional[Path] = None,
    app_slug: str = "polpot",
) -> DataMigrationReport:
    """Safely and idempotently discovers and migrates legacy application data.

    Migrates from legacy `~/.local/share/polpot` to the platform-appropriate destination
    (e.g., on macOS or Windows).

    Safety guarantees:
    - If target_dir == legacy_dir (standard Linux), returns immediately (no-op).
    - If legacy_dir does not exist, returns immediately.
    - Ordered State Inspection: Acquires the migration lock BEFORE reading or reconciling
      the mutable journal, receipts, or data directories to eliminate state-read races.
    - Strict Journal Validation: Validates version, source/dest matching, allowed stages,
      stage dependencies, intents, non-negative sizes, valid SHA-256 digests, and relative
      manifest paths (rejecting absolute paths and parent traversal).
    - Resumable Receipt Finalization: Writing legacy receipt, target receipt, marker, and journal
      cleanup is repeatable and reconciles automatically across interrupted attempts.
    - Publication Crash Protection: Records a durable intent (SHA-256 and size) before publishing
      destination assets. An interrupted run distinguishes published outputs from foreign collisions.
    - Durable Archive Intent: Records planned destination and content identity in archive_intents
      before moving files. Interrupted moves are reconciled on resume and fully included in the receipt.
    - Non-Destructive Archiving: Legacy items are archived using get_collision_free_archive_path,
      guaranteeing that existing .migrated archives are NEVER unlinked, deleted, or overwritten.
    - Concurrency Lock: Acquires an atomic lockfile in the target directory with mutual-exclusion
      stale recovery and owner-only release.
    - SQLite Concurrency Limitation: In SQLite WAL mode, an idle connection does not hold an OS
      file lock between queries. Transient 'BEGIN EXCLUSIVE' verifies that no writer or active
      transaction is running before archival, but cannot detect an external idle connection without
      an application-level coordinator.

    Args:
        target_dir: Destination user data directory (defaults to get_user_data_dir()).
        legacy_dir: Source legacy data directory (defaults to get_legacy_user_data_dir()).
        app_slug: Canonical directory slug.

    Returns:
        DataMigrationReport describing the actions taken.

    Raises:
        DataMigrationConflictError: If conflicting source and destination databases, vaults,
                                    or artifact files exist, or if receipts/journals are invalid.
    """
    resolved_target = (target_dir or get_user_data_dir(app_slug)).resolve()
    resolved_legacy = (legacy_dir or get_legacy_user_data_dir(app_slug)).resolve()

    if resolved_target == resolved_legacy:
        return DataMigrationReport(
            migrated=False,
            source=resolved_legacy,
            destination=resolved_target,
            reason="source_and_destination_identical",
        )

    if not resolved_legacy.exists():
        return DataMigrationReport(
            migrated=False,
            source=resolved_legacy,
            destination=resolved_target,
            reason="legacy_directory_not_found",
        )

    resolved_target.mkdir(parents=True, exist_ok=True)

    # All mutable state inspection and migration execution is locked
    with _migration_lock(resolved_target):
        receipt_file = resolved_legacy / "MIGRATION_RECEIPT.json"
        target_receipt = resolved_target / "MIGRATION_RECEIPT.json"
        marker_file = resolved_legacy / ".migrated_to_platformdirs"
        journal_file = resolved_target / ".migration_journal.json"

        # 1. Check in-progress journal for crash recovery BEFORE any early returns
        journal_data, is_resuming = _load_and_validate_journal(journal_file, resolved_legacy, resolved_target)

        # 2. Check receipt and completion state if NOT resuming from an active journal
        if not is_resuming:
            if receipt_file.exists():
                valid_receipt = _validate_receipt(receipt_file, resolved_legacy, resolved_target)
                if not target_receipt.exists():
                    temp_tr = target_receipt.with_name(f"{target_receipt.name}.tmp")
                    temp_tr.write_text(json.dumps(valid_receipt, indent=2), encoding="utf-8")
                    temp_tr.replace(target_receipt)
                if not marker_file.exists():
                    marker_file.write_text(f"Migrated on {valid_receipt.get('migrated_at')}\n", encoding="utf-8")
                return DataMigrationReport(
                    migrated=False,
                    source=resolved_legacy,
                    destination=resolved_target,
                    reason="already_migrated",
                    migrated_items=valid_receipt.get("migrated_items", []),
                )
            elif target_receipt.exists():
                valid_receipt = _validate_receipt(target_receipt, resolved_legacy, resolved_target)
                temp_lr = receipt_file.with_name(f"{receipt_file.name}.tmp")
                temp_lr.write_text(json.dumps(valid_receipt, indent=2), encoding="utf-8")
                temp_lr.replace(receipt_file)
                if not marker_file.exists():
                    marker_file.write_text(f"Migrated on {valid_receipt.get('migrated_at')}\n", encoding="utf-8")
                return DataMigrationReport(
                    migrated=False,
                    source=resolved_legacy,
                    destination=resolved_target,
                    reason="already_migrated",
                    migrated_items=valid_receipt.get("migrated_items", []),
                )
            elif marker_file.exists():
                legacy_db = resolved_legacy / "polpot.db"
                legacy_vault = resolved_legacy / "credentials.enc"
                legacy_artifacts = resolved_legacy / "artifacts"
                if legacy_db.is_file() or legacy_vault.is_file() or legacy_artifacts.is_dir():
                    raise DataMigrationConflictError(
                        f"Migration marker exists at '{marker_file}', but unmigrated legacy files are present "
                        "without a completed migration receipt. Resolve manually to prevent data loss."
                    )
                return DataMigrationReport(
                    migrated=False,
                    source=resolved_legacy,
                    destination=resolved_target,
                    reason="already_migrated",
                )

        legacy_db = resolved_legacy / "polpot.db"
        legacy_vault = resolved_legacy / "credentials.enc"
        legacy_artifacts = resolved_legacy / "artifacts"

        has_legacy_db = legacy_db.is_file()
        has_legacy_vault = legacy_vault.is_file()
        has_legacy_artifacts = legacy_artifacts.is_dir()

        # 3. If no legacy data is present AND we are not resuming an in-progress migration, exit
        if not (has_legacy_db or has_legacy_vault or has_legacy_artifacts) and not is_resuming:
            return DataMigrationReport(
                migrated=False,
                source=resolved_legacy,
                destination=resolved_target,
                reason="no_legacy_data_present",
            )

        target_db = resolved_target / "polpot.db"
        target_vault = resolved_target / "credentials.enc"
        target_artifacts = resolved_target / "artifacts"

        stages_completed: list[str] = journal_data.get("stages_completed", [])
        intents: dict[str, Any] = journal_data.get("intents", {})

        # Pre-flight Collision & Intent Guards (BEFORE modifying any files)
        # 1. Database collision
        db_intent = intents.get("db")
        if target_db.exists():
            if is_resuming and db_intent:
                target_sha = _compute_file_sha256(target_db)
                if target_sha != db_intent.get("sha256"):
                    raise DataMigrationConflictError(
                        f"Data migration conflict: Destination database '{target_db}' content differs from "
                        f"recorded migration intent (expected sha256 {db_intent.get('sha256')}, got {target_sha}). "
                        "Migration aborted to prevent data loss."
                    )
                try:
                    test_conn = sqlite3.connect(str(target_db))
                    try:
                        check_row = test_conn.execute("PRAGMA quick_check;").fetchone()
                        if not check_row or check_row[0] != "ok":
                            raise DataMigrationConflictError(
                                f"Target database '{target_db}' from prior interrupted run failed integrity check."
                            )
                    finally:
                        test_conn.close()
                except Exception as exc:
                    raise DataMigrationConflictError(
                        f"Data migration conflict: Destination database '{target_db}' integrity check failed: {exc}"
                    ) from exc

                if "db_published" not in stages_completed:
                    stages_completed.append("db_published")
                    _save_journal(journal_file, journal_data)
            elif has_legacy_db:
                raise DataMigrationConflictError(
                    f"Data migration conflict: Legacy database exists at '{legacy_db}' and destination "
                    f"database already exists at '{target_db}'. Neither database was modified. "
                    "Resolve the collision manually to prevent data loss."
                )

        # 2. Vault collision
        vault_intent = intents.get("vault")
        if target_vault.exists():
            if is_resuming and vault_intent:
                target_vault_sha = _compute_file_sha256(target_vault)
                if target_vault_sha != vault_intent.get("sha256"):
                    raise DataMigrationConflictError(
                        f"Data migration conflict: Destination vault '{target_vault}' content differs from "
                        f"recorded migration intent (expected sha256 {vault_intent.get('sha256')}, got {target_vault_sha}). "
                        "Migration aborted to prevent data loss."
                    )
                if "vault_published" not in stages_completed:
                    stages_completed.append("vault_published")
                    _save_journal(journal_file, journal_data)
            elif has_legacy_vault:
                if legacy_vault.read_bytes() != target_vault.read_bytes():
                    raise DataMigrationConflictError(
                        f"Data migration conflict: Legacy credential vault at '{legacy_vault}' conflicts with "
                        f"existing destination vault at '{target_vault}'. Neither file was modified. "
                        "Resolve the collision manually to prevent data loss."
                    )

        # 3. Artifact collisions
        artifacts_intent = intents.get("artifacts")
        if target_artifacts.exists():
            if is_resuming and artifacts_intent:
                manifest = artifacts_intent.get("manifest", [])
                for item in manifest:
                    dest_f = target_artifacts / item["rel_path"]
                    if dest_f.exists():
                        actual_sha = _compute_file_sha256(dest_f)
                        if actual_sha != item["sha256"]:
                            raise DataMigrationConflictError(
                                f"Data migration conflict: Destination artifact file '{dest_f}' differs from "
                                f"recorded migration intent (expected sha256 {item['sha256']}, got {actual_sha}). "
                                "Migration aborted to prevent data loss."
                            )
            elif has_legacy_artifacts:
                artifact_conflicts = _detect_artifact_conflicts(legacy_artifacts, target_artifacts)
                if artifact_conflicts:
                    raise DataMigrationConflictError(
                        f"Data migration conflict: Conflicting artifact files detected in legacy artifacts "
                        f"and destination artifacts: {artifact_conflicts}. Content differs between source and destination. "
                        f"Neither directory was modified. Resolve the collision manually to prevent data loss."
                    )

        if not is_resuming:
            journal_data = {
                "version": 1,
                "source": str(resolved_legacy),
                "destination": str(resolved_target),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "stages_completed": [],
                "intents": {},
                "archived_records": {},
                "archive_intents": {},
            }
            _save_journal(journal_file, journal_data)

        def _checkpoint_journal(stage: str) -> None:
            if stage not in journal_data["stages_completed"]:
                journal_data["stages_completed"].append(stage)
            _save_journal(journal_file, journal_data)

        migrated_items: list[str] = []
        temp_dest: Optional[Path] = None
        temp_vault: Optional[Path] = None

        try:
            # Stage 1: Migrate SQLite Database (WAL-aware)
            if "db_published" not in journal_data["stages_completed"]:
                if has_legacy_db:
                    temp_dest = resolved_target / "polpot.db.tmp_migrating"
                    if temp_dest.exists():
                        temp_dest.unlink()

                    src_conn = sqlite3.connect(str(legacy_db), timeout=10.0)
                    try:
                        try:
                            src_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                        except Exception:
                            pass

                        dest_conn = sqlite3.connect(str(temp_dest), timeout=10.0)
                        try:
                            src_conn.backup(dest_conn)

                            dest_conn.execute("BEGIN IMMEDIATE;")
                            try:
                                _remap_database_artifact_references(
                                    dest_conn,
                                    legacy_artifacts_root=legacy_artifacts,
                                    target_artifacts_root=target_artifacts,
                                )
                                dest_conn.commit()
                            except Exception:
                                dest_conn.rollback()
                                raise

                            quick_check = dest_conn.execute("PRAGMA quick_check;").fetchone()
                            if not quick_check or quick_check[0] != "ok":
                                raise RuntimeError(
                                    f"Database integrity verification failed on temporary target: {quick_check}"
                                )
                        finally:
                            dest_conn.close()
                    finally:
                        src_conn.close()

                    # Record intent BEFORE atomic publication
                    db_sha = _compute_file_sha256(temp_dest)
                    db_size = temp_dest.stat().st_size
                    journal_data.setdefault("intents", {})["db"] = {
                        "target": str(target_db),
                        "sha256": db_sha,
                        "size": db_size,
                    }
                    _save_journal(journal_file, journal_data)

                    temp_dest.replace(target_db)
                    _checkpoint_journal("db_published")
                    migrated_items.append("polpot.db")
            else:
                if not target_db.is_file():
                    raise RuntimeError(f"Published destination database '{target_db}' is missing.")
                db_intent = journal_data.get("intents", {}).get("db")
                if db_intent and _compute_file_sha256(target_db) != db_intent.get("sha256"):
                    raise DataMigrationConflictError(
                        f"Destination database '{target_db}' does not match recorded migration intent."
                    )
                migrated_items.append("polpot.db")

            # Stage 2: Migrate Credential Vault
            if "vault_published" not in journal_data["stages_completed"]:
                if has_legacy_vault:
                    temp_vault = resolved_target / "credentials.enc.tmp_migrating"
                    if temp_vault.exists():
                        temp_vault.unlink()
                    shutil.copy2(legacy_vault, temp_vault)
                    vault_sha = _compute_file_sha256(temp_vault)
                    vault_size = temp_vault.stat().st_size

                    # Record intent BEFORE atomic publication
                    journal_data.setdefault("intents", {})["vault"] = {
                        "target": str(target_vault),
                        "sha256": vault_sha,
                        "size": vault_size,
                    }
                    _save_journal(journal_file, journal_data)

                    temp_vault.replace(target_vault)
                    _checkpoint_journal("vault_published")
                    migrated_items.append("credentials.enc")
            else:
                if not target_vault.is_file():
                    raise RuntimeError(f"Published destination vault '{target_vault}' is missing.")
                vault_intent = journal_data.get("intents", {}).get("vault")
                if vault_intent and _compute_file_sha256(target_vault) != vault_intent.get("sha256"):
                    raise DataMigrationConflictError(
                        f"Destination vault '{target_vault}' does not match recorded migration intent."
                    )
                migrated_items.append("credentials.enc")

            # Stage 3: Migrate Artifacts (Merge without overwrite)
            if "artifacts_published" not in journal_data["stages_completed"]:
                if has_legacy_artifacts:
                    manifest: list[dict[str, Any]] = []
                    for root, dirs, files in os.walk(legacy_artifacts):
                        rel_root = Path(root).relative_to(legacy_artifacts)
                        for fname in sorted(files):
                            src_f = Path(root) / fname
                            rel_f = str(rel_root / fname if str(rel_root) != "." else Path(fname))
                            manifest.append({
                                "rel_path": rel_f,
                                "sha256": _compute_file_sha256(src_f),
                                "size": src_f.stat().st_size,
                            })

                    # Record intent BEFORE copying files
                    journal_data.setdefault("intents", {})["artifacts"] = {
                        "target": str(target_artifacts),
                        "manifest": manifest,
                    }
                    _save_journal(journal_file, journal_data)

                    target_artifacts.mkdir(parents=True, exist_ok=True)
                    was_merged = False
                    for item in manifest:
                        rel_p = Path(item["rel_path"])
                        dest_f = target_artifacts / rel_p
                        src_f = legacy_artifacts / rel_p
                        if dest_f.exists():
                            was_merged = True
                            if _compute_file_sha256(dest_f) != item["sha256"]:
                                raise DataMigrationConflictError(
                                    f"Artifact conflict: '{dest_f}' content changed unexpectedly during copy."
                                )
                        else:
                            dest_f.parent.mkdir(parents=True, exist_ok=True)
                            temp_f = dest_f.with_name(f"{dest_f.name}.tmp_migrating")
                            shutil.copy2(src_f, temp_f)
                            temp_f.replace(dest_f)

                    _checkpoint_journal("artifacts_published")
                    migrated_items.append("artifacts (merged)" if was_merged else "artifacts")
            else:
                if not target_artifacts.is_dir():
                    raise RuntimeError(f"Published destination artifacts directory '{target_artifacts}' is missing.")
                artifacts_intent = journal_data.get("intents", {}).get("artifacts")
                if artifacts_intent:
                    for item in artifacts_intent.get("manifest", []):
                        dest_f = target_artifacts / item["rel_path"]
                        if not dest_f.is_file() or _compute_file_sha256(dest_f) != item["sha256"]:
                            raise DataMigrationConflictError(
                                f"Artifact file '{dest_f}' missing or modified from recorded migration intent."
                            )
                migrated_items.append("artifacts")

            # Stage 4: Post-Publication Verification
            if "db" in journal_data.get("intents", {}):
                if not target_db.is_file() or _compute_file_sha256(target_db) != journal_data["intents"]["db"]["sha256"]:
                    raise RuntimeError(f"Post-publication verification failed: '{target_db}' is missing or invalid.")
            if "vault" in journal_data.get("intents", {}):
                if not target_vault.is_file() or _compute_file_sha256(target_vault) != journal_data["intents"]["vault"]["sha256"]:
                    raise RuntimeError(f"Post-publication verification failed: '{target_vault}' is missing or invalid.")
            if "artifacts" in journal_data.get("intents", {}):
                if not target_artifacts.is_dir():
                    raise RuntimeError(f"Post-publication verification failed: '{target_artifacts}' is missing.")
                for item in journal_data["intents"]["artifacts"].get("manifest", []):
                    dest_f = target_artifacts / item["rel_path"]
                    if not dest_f.is_file() or _compute_file_sha256(dest_f) != item["sha256"]:
                        raise RuntimeError(f"Post-publication verification failed: artifact '{dest_f}' is missing or invalid.")

            # Stage 5: Collision-Safe Source Archival with Durable Archive Intent
            archived_records: dict[str, str] = journal_data.setdefault("archived_records", {})
            archive_intents: dict[str, Any] = journal_data.setdefault("archive_intents", {})
            reserved_archive_targets: set[Path] = {Path(p) for p in archived_records.values()}

            # Reconcile pending archive intents from prior interrupted runs first
            for item_name, aintent in list(archive_intents.items()):
                if item_name in archived_records:
                    archive_intents.pop(item_name, None)
                    continue
                src_p = Path(aintent["source"])
                dst_p = Path(aintent["planned_dst"])
                if not src_p.exists() and dst_p.exists():
                    # Move completed before journal update; verify content matches intent
                    if _verify_archived_content(dst_p, aintent):
                        archived_records[item_name] = str(dst_p)
                        reserved_archive_targets.add(dst_p)
                        archive_intents.pop(item_name, None)
                        _save_journal(journal_file, journal_data)
                    else:
                        raise DataMigrationConflictError(
                            f"Archived item at '{dst_p}' does not match recorded archive intent for '{item_name}'."
                        )
                elif src_p.exists() and not dst_p.exists():
                    # Move did not occur; keep intent for re-execution
                    pass
                elif src_p.exists() and dst_p.exists():
                    raise DataMigrationConflictError(
                        f"Ambiguous archive state: both source '{src_p}' and planned archive '{dst_p}' exist."
                    )
                else:
                    raise DataMigrationConflictError(
                        f"Archive intent failed: neither source '{src_p}' nor planned archive '{dst_p}' exists."
                    )

            if "source_archived" not in journal_data["stages_completed"]:
                # Check exclusive lock on legacy_db before archiving
                if legacy_db.exists() and "polpot.db" not in archived_records:
                    try:
                        verify_conn = sqlite3.connect(str(legacy_db), timeout=0.5)
                        try:
                            verify_conn.execute("BEGIN EXCLUSIVE;")
                            verify_conn.rollback()
                        finally:
                            verify_conn.close()
                    except sqlite3.OperationalError as exc:
                        raise RuntimeError(
                            f"Cannot archive legacy database '{legacy_db}': database is locked by another process."
                        ) from exc

                items_to_archive = [
                    (legacy_db, resolved_legacy / "polpot.db.migrated"),
                    (resolved_legacy / "polpot.db-wal", resolved_legacy / "polpot.db-wal.migrated"),
                    (resolved_legacy / "polpot.db-shm", resolved_legacy / "polpot.db-shm.migrated"),
                    (legacy_vault, resolved_legacy / "credentials.enc.migrated"),
                    (legacy_artifacts, resolved_legacy / "artifacts.migrated"),
                ]

                for src_item, pref_dst in items_to_archive:
                    if src_item.name in archived_records:
                        continue
                    if not src_item.exists():
                        continue

                    is_dir = src_item.is_dir()
                    if is_dir:
                        manifest = _compute_directory_manifest(src_item)
                        size = sum(m["size"] for m in manifest)
                        sha256 = ""
                    else:
                        sha256 = _compute_file_sha256(src_item)
                        size = src_item.stat().st_size
                        manifest = []

                    # Pre-calculate collision-free archive destination
                    planned_dst = get_collision_free_archive_path(pref_dst, reserved=reserved_archive_targets)

                    # Persist archive intent BEFORE moving the item
                    intent_record = {
                        "source": str(src_item),
                        "planned_dst": str(planned_dst),
                        "is_dir": is_dir,
                        "sha256": sha256,
                        "size": size,
                        "manifest": manifest,
                    }
                    archive_intents[src_item.name] = intent_record
                    _save_journal(journal_file, journal_data)

                    # Execute move via _safe_archive_path
                    safe_dst = _safe_archive_path(src_item, planned_dst, reserved=reserved_archive_targets)
                    if safe_dst is None:
                        raise RuntimeError(f"Archive failed: source '{src_item}' disappeared.")

                    # Verify archived item
                    if not _verify_archived_content(safe_dst, intent_record):
                        raise RuntimeError(f"Archive verification failed after moving '{src_item}' to '{safe_dst}'.")

                    # Mark completed
                    reserved_archive_targets.add(safe_dst)
                    archived_records[src_item.name] = str(safe_dst)
                    archive_intents.pop(src_item.name, None)
                    _save_journal(journal_file, journal_data)

                _checkpoint_journal("source_archived")

            # Stage 6: Persistent JSON Receipt Creation & Resumable Finalization
            receipt_data = {
                "version": 1,
                "status": "completed",
                "migrated_at": journal_data.get("started_at") or datetime.now(timezone.utc).isoformat(),
                "source": str(resolved_legacy),
                "destination": str(resolved_target),
                "migrated_items": migrated_items,
                "archived_records": archived_records,
            }
            receipt_json = json.dumps(receipt_data, indent=2)

            # 6a. Legacy receipt
            temp_receipt = receipt_file.with_name(f"{receipt_file.name}.tmp")
            temp_receipt.write_text(receipt_json, encoding="utf-8")
            temp_receipt.replace(receipt_file)

            # 6b. Destination receipt
            temp_target_receipt = target_receipt.with_name(f"{target_receipt.name}.tmp")
            temp_target_receipt.write_text(receipt_json, encoding="utf-8")
            temp_target_receipt.replace(target_receipt)

            # 6c. Completion marker
            temp_marker = marker_file.with_name(f"{marker_file.name}.tmp")
            temp_marker.write_text(f"Migrated on {receipt_data['migrated_at']}\n", encoding="utf-8")
            temp_marker.replace(marker_file)

            # 6d. Journal cleanup
            if journal_file.exists():
                try:
                    journal_file.unlink()
                except Exception:
                    pass

        except Exception:
            if temp_dest is not None and temp_dest.exists():
                try:
                    temp_dest.unlink()
                except Exception:
                    pass
            if temp_vault is not None and temp_vault.exists():
                try:
                    temp_vault.unlink()
                except Exception:
                    pass
            if target_artifacts.exists():
                for root, _, files in os.walk(target_artifacts):
                    for fname in files:
                        if fname.endswith(".tmp_migrating"):
                            try:
                                (Path(root) / fname).unlink()
                            except Exception:
                                pass
            raise

    return DataMigrationReport(
        migrated=True,
        source=resolved_legacy,
        destination=resolved_target,
        reason="migration_completed_successfully",
        migrated_items=migrated_items,
    )
