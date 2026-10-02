"""Test fixture worker that continuously floods stderr with log lines.

Used to verify that the supervisor's stderr drain thread prevents pipe backpressure
deadlocks and enforces the 16 KB diagnostic buffer bound.
"""

from __future__ import annotations

import json
import signal
import sys


def _handle_sigterm(signum: int, frame: object) -> None:
    """Exit cleanly on SIGTERM to model cooperative child termination."""
    sys.exit(0)


def main() -> None:
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sigterm)

    # Emit many lines of stderr totaling several megabytes
    chunk = "LOG: " + ("x" * 120) + "\n"  # ~126 bytes
    # Write 1000 lines (~126 KB) immediately, then flush
    for i in range(1000):
        sys.stderr.write(f"[{i:04d}] {chunk}")
    sys.stderr.flush()

    # Listen on stdin for ping handshake or commands
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
                    continue
            except Exception:
                pass

            if line_str == "flood":
                for i in range(1000, 2000):
                    sys.stderr.write(f"[{i:04d}] {chunk}")
                sys.stderr.flush()
                sys.stdout.write("FLOODED\n")
                sys.stdout.flush()
            elif line_str == "exit":
                break
    except (OSError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main()
