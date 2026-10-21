# ============================================================
#  infrastructure/persistence/sqlite/instance_lock.py
#  Cross-Process Single-Instance Lock for SQLite Database
# ============================================================

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import fcntl
except ImportError:
    fcntl = None  # Non-POSIX systems (e.g. Windows)

try:
    import msvcrt
except ImportError:
    msvcrt = None  # Non-Windows systems (e.g. Linux, macOS)

try:
    import _winapi
except ImportError:
    _winapi = None  # Windows-only built-in

from core.exceptions.domain_exceptions import ApplicationInstanceLockedError


class SQLiteInstanceLock:
    """
    Operating-system level cross-process advisory lock protecting a local SQLite
    database from concurrent multi-instance corruption.

    Uses OS kernel advisory file locking:
      - POSIX (Linux, macOS): `fcntl.flock` with `LOCK_EX | LOCK_NB`.
      - Windows: `msvcrt.locking` with `LK_NBLCK` on an out-of-band offset beyond metadata.

    Kernel locks are tied to the process lifecycle: if the owning process crashes,
    is killed via SIGKILL / TerminateProcess, or terminates unexpectedly, the OS
    kernel automatically releases the lock. The lock file remains on disk to
    prevent inode race conditions upon re-acquisition.
    """

    _WINDOWS_LOCK_OFFSET: int = 1_048_576  # 1 MB offset, well beyond header metadata

    # Win32 CreateFile constants (not exported by internal _winapi module)
    _GENERIC_READ: int = 0x80000000
    _GENERIC_WRITE: int = 0x40000000
    _FILE_SHARE_READ: int = 0x00000001
    _FILE_SHARE_WRITE: int = 0x00000002
    _FILE_SHARE_DELETE: int = 0x00000004
    _OPEN_EXISTING: int = 3
    _OPEN_ALWAYS: int = 4
    _FILE_ATTRIBUTE_NORMAL: int = 0x00000080

    def __init__(self, db_path: Path | str, lock_path: Optional[Path | str] = None):
        self.db_path = str(db_path)
        # Check for in-memory SQLite paths (e.g. :memory:, file:memdb?mode=memory, file::memory:)
        if (
            self.db_path == ":memory:"
            or not self.db_path
            or "mode=memory" in self.db_path
            or self.db_path.startswith("file::memory:")
        ):
            self.lock_path: Optional[Path] = None
        elif lock_path is not None:
            self.lock_path = Path(lock_path).resolve()
        else:
            p = Path(db_path).resolve()
            self.lock_path = p.with_suffix(p.suffix + ".lock")

        self._file_obj = None
        self._fd: Optional[int] = None
        self._is_locked: bool = False

    @property
    def is_locked(self) -> bool:
        """Returns True if this instance currently holds the active file lock."""
        return self._is_locked

    def acquire(self) -> bool:
        """
        Attempts to acquire an exclusive, non-blocking lock.

        Returns:
            True if ownership was acquired, or False if another process holds it.

        Raises:
            OSError: If opening or creating the lock file encounters filesystem/permission errors.
            NotImplementedError: If neither fcntl nor msvcrt is available.
        """
        if self.lock_path is None:
            self._is_locked = True
            return True

        if self._is_locked and (self._file_obj is not None or self._fd is not None):
            return True

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is not None:
            return self._acquire_posix()
        elif msvcrt is not None:
            return self._acquire_windows()
        else:
            raise NotImplementedError(
                "Cross-process single-instance locking requires POSIX 'fcntl' or Windows 'msvcrt'."
            )

    def _acquire_posix(self) -> bool:
        file_obj = None
        # Step 1: Open or create the lock file. Propagate filesystem/permission errors instead of masking them.
        try:
            file_obj = open(self.lock_path, "a+")
        except OSError:
            raise

        # Step 2: Attempt non-blocking advisory lock.
        try:
            fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            import errno
            if file_obj is not None:
                try:
                    file_obj.close()
                except Exception:
                    pass
            # Lock contention surfaces as BlockingIOError or EAGAIN / EWOULDBLOCK / EACCES
            if isinstance(exc, BlockingIOError) or getattr(exc, "errno", None) in (
                errno.EACCES,
                errno.EAGAIN,
                errno.EWOULDBLOCK,
            ):
                return False
            raise

        # Step 3: Write process metadata into the lock file
        try:
            file_obj.seek(0)
            file_obj.truncate()
            metadata = {
                "pid": os.getpid(),
                "acquired_at": datetime.now(timezone.utc).isoformat(),
                "process_name": sys.argv[0] if sys.argv else "polpo",
            }
            file_obj.write(json.dumps(metadata) + "\n")
            file_obj.flush()

            self._file_obj = file_obj
            self._is_locked = True
            return True
        except Exception:
            try:
                fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                file_obj.close()
            except Exception:
                pass
            raise

    def _acquire_windows(self) -> bool:
        if self.lock_path is None:
            return True

        handle = None
        fd = None
        try:
            if _winapi is not None:
                # Open with full sharing flags (read, write, delete) so other
                # inspection handles or tempdir cleanup can open/read metadata
                # without ERROR_SHARING_VIOLATION (WinError 32).
                handle = _winapi.CreateFile(
                    str(self.lock_path),
                    self._GENERIC_READ | self._GENERIC_WRITE,
                    self._FILE_SHARE_READ | self._FILE_SHARE_WRITE | self._FILE_SHARE_DELETE,
                    0,
                    self._OPEN_ALWAYS,
                    self._FILE_ATTRIBUTE_NORMAL,
                    0,
                )
                fd = msvcrt.open_osfhandle(handle, os.O_RDWR | getattr(os, "O_BINARY", 0))
            else:
                fd = os.open(
                    self.lock_path,
                    os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0),
                )
        except OSError:
            raise

        try:
            # Move pointer to lock region beyond metadata header to avoid blocking
            # cooperative readers attempting to inspect holder PID.
            os.lseek(fd, self._WINDOWS_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except (OSError, IOError):
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
            elif handle is not None and _winapi is not None:
                try:
                    _winapi.CloseHandle(handle)
                except Exception:
                    pass
            return False

        try:
            # Successfully locked advisory byte. Now write padded metadata header at offset 0.
            metadata = {
                "pid": os.getpid(),
                "acquired_at": datetime.now(timezone.utc).isoformat(),
                "process_name": sys.argv[0] if sys.argv else "polpo",
            }
            payload = (json.dumps(metadata) + "\n").encode("utf-8")
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, payload.ljust(1024))

            self._fd = fd
            self._is_locked = True
            return True
        except Exception:
            try:
                os.lseek(fd, self._WINDOWS_LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            try:
                os.close(fd)
            except Exception:
                pass
            raise

    def release(self) -> None:
        """
        Releases the acquired lock and closes the file descriptor.
        Clears the metadata in the lock file before releasing so that subsequent
        inspectors do not see stale holder PIDs.
        The lock file is intentionally not unlinked to avoid inode race conditions.
        """
        if not self._is_locked:
            self._file_obj = None
            self._fd = None
            return

        try:
            if fcntl is not None and self._file_obj is not None:
                try:
                    self._file_obj.seek(0)
                    self._file_obj.truncate(0)
                    self._file_obj.flush()
                except Exception:
                    pass
                try:
                    fcntl.flock(self._file_obj.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            elif msvcrt is not None and self._fd is not None:
                try:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    os.write(self._fd, b" " * 1024)
                except Exception:
                    pass
                try:
                    os.lseek(self._fd, self._WINDOWS_LOCK_OFFSET, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
        finally:
            if self._file_obj is not None:
                try:
                    self._file_obj.close()
                except Exception:
                    pass
                self._file_obj = None
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except Exception:
                    pass
                self._fd = None
            self._is_locked = False

    def get_holder_pid(self) -> Optional[int]:
        """
        Attempts to read the PID of the current lock holder from the lock file.
        Returns None if no process currently holds the lock, or if the file cannot be read/parsed.
        """
        if self._is_locked:
            return os.getpid()

        if self.lock_path is None or not self.lock_path.is_file():
            return None

        # Verify whether the lock is actively held before reporting a PID
        if fcntl is not None:
            try:
                with open(self.lock_path, "r+") as probe_f:
                    try:
                        fcntl.flock(probe_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        # Exclusive lock succeeded: lock is FREE, no current holder!
                        fcntl.flock(probe_f.fileno(), fcntl.LOCK_UN)
                        return None
                    except (BlockingIOError, OSError):
                        # Lock is actively held by another process
                        pass
            except OSError:
                pass

            content = ""
            try:
                with open(self.lock_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                content = ""

        elif _winapi is not None and msvcrt is not None:
            handle = None
            content = ""
            try:
                handle = _winapi.CreateFile(
                    str(self.lock_path),
                    self._GENERIC_READ | self._GENERIC_WRITE,
                    self._FILE_SHARE_READ | self._FILE_SHARE_WRITE | self._FILE_SHARE_DELETE,
                    0,
                    self._OPEN_EXISTING,
                    self._FILE_ATTRIBUTE_NORMAL,
                    0,
                )
                fd = msvcrt.open_osfhandle(handle, os.O_RDWR | getattr(os, "O_BINARY", 0))
                try:
                    os.lseek(fd, self._WINDOWS_LOCK_OFFSET, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    # Succeeded in acquiring lock byte: lock is FREE!
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    os.close(fd)
                    return None
                except (OSError, IOError):
                    # Actively locked by another process
                    pass
                os.lseek(fd, 0, os.SEEK_SET)
                data_bytes = os.read(fd, 1024)
                content = data_bytes.decode("utf-8", errors="ignore")
                os.close(fd)
                handle = None
            except Exception:
                if handle is not None and _winapi is not None:
                    try:
                        _winapi.CloseHandle(handle)
                    except Exception:
                        pass
                content = ""
        else:
            content = ""
            try:
                with open(self.lock_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                content = ""

        if content:
            try:
                lines = content.splitlines()
                first_line = lines[0].strip() if lines else ""
                if first_line:
                    data = json.loads(first_line)
                    pid = data.get("pid")
                    if isinstance(pid, int):
                        return pid
            except Exception:
                pass
        return None

    def __enter__(self):
        if not self.acquire():
            holder_pid = self.get_holder_pid()
            pid_str = f" (PID {holder_pid})" if holder_pid else ""
            raise ApplicationInstanceLockedError(
                f"Another application instance{pid_str} is currently running and accessing "
                f"the database at '{self.db_path}'.",
                lock_path=str(self.lock_path) if self.lock_path else None,
                holder_pid=holder_pid,
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass
