"""Test fixture worker that hangs on startup without responding to the ping handshake.

Used to test supervisor cold-start handshake timeout and two-phase termination escalation.
"""

from __future__ import annotations

import sys
import time


def main() -> None:
    # Emit diagnostic message to stderr so test can assert on stderr capture
    sys.stderr.write("Hung startup worker started, ignoring ping handshake\n")
    sys.stderr.flush()

    # Sleep indefinitely without writing to stdout
    while True:
        try:
            time.sleep(1.0)
        except (OSError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    main()
