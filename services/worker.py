# ============================================================
#  services/worker.py  —  Migrated to Application Services
# ============================================================

import sys
import os
import errno
try:
    import fcntl
except ImportError:
    fcntl = None  # POSIX only; unavailable on Windows

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import WORKER_LOCK_FILE
from infrastructure.composition import get_app_container

MAX_AUTO_RETRY = 5

_lock_file_fd = None


def acquire_lock() -> bool:
    """
    Acquires a non-blocking lock on WORKER_LOCK_FILE using fcntl.flock on POSIX.
    On non-POSIX systems where fcntl is unavailable, raises NotImplementedError.
    Returns True if the lock was acquired, or False if another worker owns it.
    """
    if fcntl is None:
        raise NotImplementedError("Single-instance worker file locking requires POSIX 'fcntl'.")

    global _lock_file_fd
    file_fd = None
    try:
        file_fd = open(WORKER_LOCK_FILE, "a+")
        fcntl.flock(file_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        file_fd.seek(0)
        file_fd.truncate()
        file_fd.write(f"{os.getpid()}\n")
        file_fd.flush()
        _lock_file_fd = file_fd
        return True
    except BlockingIOError:
        if file_fd is not None:
            try:
                file_fd.close()
            except Exception:
                pass
        return False
    except OSError as e:
        if file_fd is not None:
            try:
                file_fd.close()
            except Exception:
                pass
        if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            return False
        raise


def release_lock():
    """Releases the lock file and closes the file descriptor."""
    global _lock_file_fd
    if _lock_file_fd is not None:
        try:
            if fcntl is not None:
                fcntl.flock(_lock_file_fd.fileno(), fcntl.LOCK_UN)
            _lock_file_fd.close()
        except Exception:
            pass
        _lock_file_fd = None


def run():
    """
    نقطه ورود اجرای Worker.
    کارها را از طریق JobExecutionService اجرا می‌نماید.
    """
    try:
        acquired = acquire_lock()
    except Exception as e:
        print(f"❌ خطای دسترسی یا فایل‌سیستم در فایل قفل Worker: {e}")
        sys.exit(1)

    if not acquired:
        print("⏭️ Worker دیگری در حال اجراست.")
        return

    try:
        container = get_app_container()

        # ─── اول صف Pipeline 1 ──────────────────────────────
        job = container.job_execution_service.execute_next_job()
        if job is not None:
            print(f"🚀 پردازش جاب #{job.id} (Pipeline 1) پایان یافت. وضعیت: {job.status.value}")
            return

        # ─── اگر خالی بود، صف Pipeline 2 ────────────────────
        p2_job = container.job_execution_service.execute_next_pipeline2_job()
        if p2_job is not None:
            print(f"🚀 پردازش جاب هوشمند #{p2_job.id} (Pipeline 2) پایان یافت. وضعیت: {p2_job.status.value}")
            return

        print("✅ هر دو صف خالی هستند.")

    finally:
        release_lock()


if __name__ == "__main__":
    run()