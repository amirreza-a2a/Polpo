"""Test fixture worker that answers ping handshake, then hangs indefinitely on requests.

Used to test supervisor in-flight RPC request timeout and two-phase termination escalation.
"""

from __future__ import annotations

import json
import sys
import time


def main() -> None:
    sys.stderr.write("Hung request worker started\n")
    sys.stderr.flush()

    # Handle the startup ping handshake
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

    # Emit diagnostic message to stderr after handshake
    sys.stderr.write("Ping answered, hanging on subsequent requests\n")
    sys.stderr.flush()

    # Hang indefinitely on subsequent requests without writing response
    while True:
        try:
            time.sleep(1.0)
        except (OSError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    main()
