"""Test fixture worker that ignores SIGTERM on POSIX platforms.

Used to test two-phase termination escalation (SIGTERM -> 0.5s wait -> SIGKILL)
in MathJaxProcessSupervisor lifecycle tests.
"""

from __future__ import annotations

import json
import signal
import sys
import time


def main() -> None:
    # Ignore SIGTERM on POSIX
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    # Emit diagnostic message to stderr
    sys.stderr.write("Worker running in loop ignoring SIGTERM\n")
    sys.stderr.flush()

    # Answer ping handshake
    try:
        for line in sys.stdin:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                req = json.loads(line_str)
                if req.get("method") == "ping":
                    resp = json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": "pong"}) + "\n"
                    sys.stdout.write(resp)
                    sys.stdout.flush()
                    break
            except Exception:
                pass
    except (OSError, KeyboardInterrupt):
        pass

    # Loop indefinitely until forcefully killed (SIGKILL)
    while True:
        try:
            time.sleep(0.1)
        except Exception:
            pass


if __name__ == "__main__":
    main()
