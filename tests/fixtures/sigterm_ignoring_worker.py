"""Test fixture worker that ignores SIGTERM on POSIX platforms.

Used to test two-phase termination escalation (SIGTERM -> 0.5s wait -> SIGKILL)
in MathJaxProcessSupervisor lifecycle tests.
"""

from __future__ import annotations

import signal
import sys
import time


def main() -> None:
    # Ignore SIGTERM on POSIX
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    # Signal readiness to supervisor
    sys.stdout.write("READY\n")
    sys.stdout.flush()

    # Emit diagnostic message to stderr
    sys.stderr.write("Worker running in loop ignoring SIGTERM\n")
    sys.stderr.flush()

    # Loop indefinitely until forcefully killed (SIGKILL)
    while True:
        try:
            time.sleep(0.1)
        except Exception:
            pass


if __name__ == "__main__":
    main()
