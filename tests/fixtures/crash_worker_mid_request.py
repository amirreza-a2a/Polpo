"""Test fixture worker that answers ping, then abruptly crashes mid-request.

Used to test supervisor fast crash wake-up via _EofSentinel raising MathWorkerCrashedError.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> None:
    sys.stderr.write("Crash mid-request worker started\n")
    sys.stderr.flush()

    # 1. Answer ping handshake
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        if req.get("method") == "ping":
            resp = json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": "pong"}) + "\n"
            sys.stdout.write(resp)
            sys.stdout.flush()
            break

    # 2. On next request line, log and exit immediately via os._exit to bypass cleanup
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        sys.stderr.write("Received request, crashing immediately via os._exit(1)\n")
        sys.stderr.flush()
        os._exit(1)


if __name__ == "__main__":
    main()
