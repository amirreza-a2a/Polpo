"""Test fixture worker that responds late to an RPC request after sleeping.

Used to test supervisor generation token isolation (discarding late responses from killed generations).
Supports an optional one-shot marker file via POLPO_LATE_WORKER_MARKER_FILE environment variable:
when configured, only the first generation sleeps late; subsequent generations respond promptly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
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

        delay = float(os.environ.get("POLPO_TEST_LATE_WORKER_DELAY", "0.5"))
        marker_file = os.environ.get("POLPO_LATE_WORKER_MARKER_FILE")
        is_gen1 = False
        if marker_file:
            marker_path = Path(marker_file)
            if not marker_path.exists():
                is_gen1 = True
                marker_path.touch()
                # Generation 1 ignores SIGTERM so it survives the supervisor's termination
                # grace period to emit its late response to stdout before exiting.
                if hasattr(signal, "SIGTERM"):
                    try:
                        signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    except (ValueError, OSError):
                        pass
                time.sleep(delay)
            else:
                # Generation 2+: respond immediately without sleeping
                pass
        else:
            time.sleep(delay)

        resp = json.dumps({
            "jsonrpc": "2.0",
            "id": req.get("id"),
            "result": {
                "svg": f"<svg>response-id-{req.get('id')}</svg>",
                "svg_xml": f"<svg>response-id-{req.get('id')}</svg>",
                "width": "1ex",
                "height": "1ex",
                "vertical_align": "0ex",
            },
        }) + "\n"
        try:
            sys.stdout.write(resp)
            sys.stdout.flush()
        except OSError:
            # If supervisor already killed the worker, stdout write may fail
            pass

        if is_gen1:
            sys.exit(0)


if __name__ == "__main__":
    main()
