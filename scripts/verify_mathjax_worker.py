#!/usr/bin/env python3
"""Standalone verification script for headless MathJax worker subsystem (TICK-008).

Verifies the pinned Node.js 22.23.2 runtime contract, mathjax-full@3.2.2 dependency,
stdio line-delimited JSON-RPC communication, self-contained SVG generation,
metrics extraction, and resilient error handling for invalid TeX.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Add project root to sys.path to enable infrastructure imports if needed
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXPECTED_NODE_VERSION = "22.23.2"
EXPECTED_MATHJAX_VERSION = "3.2.2"


def find_node_executable() -> Path:
    """Locate the Node.js executable according to resolution hierarchy."""
    # 1. Environment variable override
    env_node = os.environ.get("POLPO_NODE_PATH")
    if env_node:
        p = Path(env_node).expanduser().resolve()
        if p.is_file() and os.access(p, os.X_OK):
            return p

    # 2. Bundled resource in resources/mathjax/
    ext = ".exe" if os.name == "nt" else ""
    bundled = PROJECT_ROOT / "resources" / "mathjax" / f"node{ext}"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return bundled.resolve()

    # 3. System PATH
    node_on_path = shutil.which(f"node{ext}") or shutil.which("node")
    if node_on_path:
        p = Path(node_on_path).resolve()
        if p.is_file() and os.access(p, os.X_OK):
            return p

    raise RuntimeError(
        "Node.js executable not found. Checked POLPO_NODE_PATH, resources/mathjax/node, and system PATH."
    )


def verify_node_version(node_path: Path) -> str:
    """Verify that the selected Node executable reports exactly the pinned version."""
    proc = subprocess.run(
        [str(node_path), "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    raw_version = proc.stdout.strip()
    clean_version = raw_version.lstrip("v")
    if clean_version != EXPECTED_NODE_VERSION:
        raise AssertionError(
            f"Node version mismatch: expected exactly {EXPECTED_NODE_VERSION}, got {clean_version} ({raw_version})"
        )
    return clean_version


def verify_mathjax_worker() -> None:
    """Execute end-to-end verification of the MathJax worker process."""
    print("=" * 70)
    print("PolpoT TICK-008: Headless MathJax Worker Verification")
    print("=" * 70)

    # 1. Resolve and verify Node runtime
    node_path = find_node_executable()
    print(f"[1/8] Located Node executable: {node_path}")

    node_version = verify_node_version(node_path)
    print(f"[2/8] Verified Node runtime version: v{node_version} (matches pinned {EXPECTED_NODE_VERSION})")

    # 2. Verify worker script exists
    worker_script = PROJECT_ROOT / "resources" / "mathjax" / "mathjax_worker.js"
    if not worker_script.is_file():
        raise FileNotFoundError(f"MathJax worker script not found at {worker_script}")
    print(f"[3/8] Located MathJax worker script: {worker_script}")

    # 3. Spawn worker process
    proc = subprocess.Popen(
        [str(node_path), str(worker_script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )

    try:
        def send_request(req_obj: dict) -> dict:
            line = json.dumps(req_obj) + "\n"
            proc.stdin.write(line)
            proc.stdin.flush()
            resp_line = proc.stdout.readline()
            if not resp_line:
                stderr_output = proc.stderr.read()
                raise RuntimeError(
                    f"Worker terminated unexpectedly. stderr: {stderr_output}"
                )
            return json.loads(resp_line.strip())

        # 4. Probe version
        ver_resp = send_request({"jsonrpc": "2.0", "id": 1, "method": "version"})
        assert ver_resp.get("jsonrpc") == "2.0", "Expected jsonrpc 2.0 response"
        assert ver_resp.get("id") == 1, "Expected matching request id 1"
        assert ver_resp.get("result", {}).get("mathjax") == EXPECTED_MATHJAX_VERSION, (
            f"Expected MathJax version {EXPECTED_MATHJAX_VERSION}, got {ver_resp.get('result')}"
        )
        print(f"[4/8] Verified MathJax package version in worker: {EXPECTED_MATHJAX_VERSION}")

        # 5. Render standard TeX ($E=mc^2$)
        req_id = 101
        render_resp = send_request({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "render",
            "params": {"tex": "E=mc^2", "display": False, "em": 16, "ex": 8},
        })
        assert render_resp.get("jsonrpc") == "2.0"
        assert render_resp.get("id") == req_id
        assert "result" in render_resp, f"Render failed: {render_resp}"
        result = render_resp["result"]

        svg_xml = result.get("svg") or result.get("svg_xml")
        assert svg_xml is not None, "Missing SVG output in response"
        assert svg_xml.startswith("<svg"), "SVG output must begin with <svg"
        assert svg_xml.endswith("</svg>"), "SVG output must end with </svg>"
        assert 'xmlns="http://www.w3.org/2000/svg"' in svg_xml, (
            "SVG must declare xmlns='http://www.w3.org/2000/svg'"
        )
        assert "<defs>" in svg_xml and "<path" in svg_xml, (
            "SVG must contain embedded vector <path> definitions inside <defs>"
        )

        # Disallow external font loading or external URLs
        urls = re.findall(r"https?://[^\s\"'>]+", svg_xml)
        allowed_ns = {"http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink"}
        unexpected_urls = [u for u in urls if u not in allowed_ns]
        assert not unexpected_urls, f"SVG contains unexpected external URL references: {unexpected_urls}"

        # Verify well-formed XML
        root = ET.fromstring(svg_xml)
        assert root.tag.endswith("svg"), f"Root tag must be svg, got {root.tag}"

        # Verify metrics
        width = result.get("width")
        height = result.get("height")
        vertical_align = result.get("vertical_align")
        assert width and isinstance(width, str) and ("ex" in width or "px" in width), (
            f"Invalid width metric: {width}"
        )
        assert height and isinstance(height, str) and ("ex" in height or "px" in height), (
            f"Invalid height metric: {height}"
        )
        assert vertical_align and isinstance(vertical_align, str), (
            f"Invalid vertical_align metric: {vertical_align}"
        )
        print(f"[5/8] Rendered $E=mc^2$ successfully (width={width}, height={height}, vertical-align={vertical_align})")
        print("      Output is self-contained standalone XML with embedded glyph paths and zero external fonts.")

        # 6. Verify error handling on invalid TeX (syntax error: unclosed brace)
        err_id = 102
        err_resp = send_request({
            "jsonrpc": "2.0",
            "id": err_id,
            "method": "render",
            "params": {"tex": r"\frac{1}{", "display": False},
        })
        assert err_resp.get("id") == err_id
        assert "error" in err_resp, f"Expected structured error on invalid TeX, got {err_resp}"
        assert isinstance(err_resp["error"].get("code"), int), "Error code must be an integer"
        assert err_resp["error"].get("message"), "Error message must be non-empty"
        print(f"[6/8] Invalid TeX trapped as structured error: code={err_resp['error']['code']}, message={err_resp['error']['message']!r}")

        # 7. Verify worker is STILL alive and processes subsequent requests
        assert proc.poll() is None, "Worker process must not terminate on invalid TeX"
        subsequent_resp = send_request({
            "jsonrpc": "2.0",
            "id": 103,
            "method": "render",
            "params": {"tex": r"\sum_{i=1}^n i = \frac{n(n+1)}{2}", "display": True},
        })
        assert subsequent_resp.get("id") == 103
        assert "result" in subsequent_resp, f"Worker failed to render subsequent valid formula: {subsequent_resp}"
        print("[7/8] Verified worker remained alive and processed subsequent display formula after error.")

        # 8. Verify malformed JSON input resilience
        proc.stdin.write("INVALID JSON SYNTAX\n")
        proc.stdin.flush()
        malformed_resp_line = proc.stdout.readline()
        assert malformed_resp_line, "Worker must respond to malformed JSON with an error message"
        malformed_resp = json.loads(malformed_resp_line.strip())
        assert malformed_resp.get("error", {}).get("code") == -32700, (
            f"Expected code -32700 on parse error, got {malformed_resp}"
        )
        assert proc.poll() is None, "Worker process must remain alive after malformed input"
        print("[8/8] Verified worker survived malformed JSON input and returned code -32700.")

    finally:
        # Graceful shutdown
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
        proc.wait(timeout=5)
        if sys.exc_info()[0] is None:
            assert proc.returncode == 0, f"Expected clean exit code 0, got {proc.returncode}"

    print("-" * 70)
    print("ALL MATHJAX WORKER CONTRACT CHECKS PASSED.")
    print("=" * 70)


if __name__ == "__main__":
    try:
        verify_mathjax_worker()
    except Exception as exc:
        print(f"\n[FAIL] Verification error: {exc}", file=sys.stderr)
        sys.exit(1)
