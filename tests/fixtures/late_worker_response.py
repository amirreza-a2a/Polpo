"""Test fixture worker that responds late to an RPC request after sleeping.

Used to test supervisor generation token isolation (discarding late responses from killed generations).
"""

from __future__ import annotations

import json
import sys
import time


def main() -> None:
    sys.stderr.write("Late response worker started\n")
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

    # 2. Wait for request, sleep before responding
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        # Sleep to exceed a fast request timeout (e.g., 0.5s)
        time.sleep(0.5)
        resp = json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": {"svg": "<svg>late</svg>"}}) + "\n"
        try:
            sys.stdout.write(resp)
            sys.stdout.flush()
        except OSError:
            # If supervisor already killed the worker, stdout write may fail
            pass
        break


if __name__ == "__main__":
    main()
