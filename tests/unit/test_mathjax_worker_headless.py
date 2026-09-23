"""Unit tests for headless MathJax daemon worker subsystem (TICK-008).

Verifies the pinned runtime contract (Node.js 22.23.2 LTS, mathjax-full@3.2.2),
stdio line-delimited JSON-RPC protocol, standalone SVG generation,
embedded vector path glyphs, layout metrics, and resilient error recovery.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Generator, Optional, Tuple

import pytest

from infrastructure.paths import get_runtime_resource_path

EXPECTED_NODE_VERSION = "22.23.2"
EXPECTED_MATHJAX_VERSION = "3.2.2"
ALLOWED_SVG_NAMESPACES = {
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1999/xlink",
}


def _resolve_test_node_binary() -> Tuple[Optional[Path], Optional[str]]:
    """Locate the Node.js executable and determine its version string."""
    env_node = os.environ.get("POLPO_NODE_PATH")
    candidate: Optional[Path] = None
    if env_node:
        p = Path(env_node).expanduser().resolve()
        if p.is_file() and os.access(p, os.X_OK):
            candidate = p

    if candidate is None:
        ext = ".exe" if os.name == "nt" else ""
        bundled = get_runtime_resource_path(f"resources/mathjax/node{ext}")
        if bundled.is_file() and os.access(bundled, os.X_OK):
            candidate = bundled.resolve()

    if candidate is None:
        ext = ".exe" if os.name == "nt" else ""
        system_node = shutil.which(f"node{ext}") or shutil.which("node")
        if system_node:
            candidate = Path(system_node).resolve()

    if candidate is None:
        return None, None

    try:
        proc = subprocess.run(
            [str(candidate), "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        version = proc.stdout.strip().lstrip("v")
        return candidate, version
    except Exception:
        return candidate, None


def _can_node_resolve_mathjax(node_path: Path) -> bool:
    """Check if the Node environment has mathjax-full available."""
    worker_dir = get_runtime_resource_path("resources/mathjax")
    try:
        proc = subprocess.run(
            [str(node_path), "-e", "require.resolve('mathjax-full/js/mathjax.js')"],
            cwd=str(worker_dir),
            capture_output=True,
            timeout=5,
        )
        return proc.returncode == 0
    except Exception:
        return False


@pytest.fixture(scope="module")
def node_executable() -> Path:
    """Fixture providing the verified Node executable path, or skipping if unavailable."""
    node_path, version = _resolve_test_node_binary()
    if node_path is None:
        pytest.skip(
            "Node.js executable not available on host. "
            "(Worker package contract validated by static tests; bundled runtime supplied in TICK-013)"
        )
    if version != EXPECTED_NODE_VERSION:
        pytest.skip(
            f"Node version {version} does not match pinned {EXPECTED_NODE_VERSION} LTS requirement."
        )
    return node_path


@pytest.fixture
def worker_proc(node_executable: Path) -> Generator[subprocess.Popen, None, None]:
    """Spawn a headless MathJax worker process for interactive testing."""
    worker_script = get_runtime_resource_path("resources/mathjax/mathjax_worker.js")
    assert worker_script.is_file(), f"Worker script not found at {worker_script}"

    if not _can_node_resolve_mathjax(node_executable):
        pytest.skip(
            "MathJax worker runtime dependencies (mathjax-full) not installed in resources/mathjax/. "
            "(Worker package contract validated by static tests; bundled runtime and dependencies supplied in TICK-013)"
        )

    proc = subprocess.Popen(
        [str(node_executable), str(worker_script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    # Warm up and verify readiness
    _rpc_call(proc, {"jsonrpc": "2.0", "id": 0, "method": "ping"})
    try:
        yield proc
    finally:
        if proc.poll() is None:
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except Exception:
                pass
        if proc.stdout and not proc.stdout.closed:
            try:
                proc.stdout.close()
            except Exception:
                pass
        if proc.stderr and not proc.stderr.closed:
            try:
                proc.stderr.close()
            except Exception:
                pass


def _rpc_call(proc: subprocess.Popen, payload: dict) -> dict:
    """Send a JSON payload to worker stdin and read one JSON response line from stdout."""
    assert proc.stdin is not None
    assert proc.stdout is not None
    line = json.dumps(payload) + "\n"
    proc.stdin.write(line)
    proc.stdin.flush()
    resp_line = proc.stdout.readline()
    assert resp_line, f"Worker terminated unexpectedly. stderr: {proc.stderr.read() if proc.stderr else ''}"
    return json.loads(resp_line.strip())


# ==============================================================================
# 1. Static Contract & Dependency Pinning Tests
# ==============================================================================

def test_package_json_pins_node_22_and_mathjax_3_2_2():
    """Assert resources/mathjax/package.json strictly pins Node 22.23.2 and mathjax-full 3.2.2."""
    package_json_path = get_runtime_resource_path("resources/mathjax/package.json")
    assert package_json_path.is_file()

    data = json.loads(package_json_path.read_text(encoding="utf-8"))
    engines = data.get("engines", {})
    dependencies = data.get("dependencies", {})

    assert engines.get("node") == EXPECTED_NODE_VERSION, (
        f"package.json must pin engines.node to {EXPECTED_NODE_VERSION}"
    )
    assert dependencies.get("mathjax-full") == EXPECTED_MATHJAX_VERSION, (
        f"package.json must pin dependencies.mathjax-full to {EXPECTED_MATHJAX_VERSION}"
    )


def test_runtimes_manifest_specifies_node_22_23_2_and_platforms():
    """Assert resources/runtimes.json specifies Node 22.23.2 with SHA-256 for all 4 platforms."""
    manifest_path = get_runtime_resource_path("resources/runtimes.json")
    assert manifest_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    node_config = manifest.get("runtimes", {}).get("node", {})
    assert node_config.get("version") == EXPECTED_NODE_VERSION

    platforms = node_config.get("platforms", {})
    expected_platforms = ["linux-x86_64", "windows-x64", "darwin-x86_64", "darwin-arm64"]
    for p in expected_platforms:
        assert p in platforms, f"Missing platform definition: {p}"
        entry = platforms[p]
        assert entry.get("url"), f"Missing URL for {p}"
        assert len(entry.get("sha256", "")) == 64, f"Invalid SHA-256 for {p}"


def test_worker_script_declares_jsonrpc_protocol_and_bounded_buffers():
    """Assert resources/mathjax/mathjax_worker.js exists and declares required protocol constants."""
    worker_script = get_runtime_resource_path("resources/mathjax/mathjax_worker.js")
    assert worker_script.is_file()
    content = worker_script.read_text(encoding="utf-8")

    assert "mathjax-full" in content
    assert "MAX_TEX_LENGTH" in content
    assert "MAX_SVG_LENGTH" in content
    assert "-32700" in content  # PARSE_ERROR
    assert "-32600" in content  # INVALID_REQUEST
    assert "-32601" in content  # METHOD_NOT_FOUND
    assert "-32602" in content  # INVALID_PARAMS


# ==============================================================================
# 2. Interactive Subprocess Tests (Live Worker Execution)
# ==============================================================================

def test_node_runtime_pinned_version(node_executable: Path):
    """Assert host Node runtime reports exactly the pinned 22.23.2 release."""
    proc = subprocess.run(
        [str(node_executable), "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip().lstrip("v") == EXPECTED_NODE_VERSION


def test_worker_ping_and_version(worker_proc: subprocess.Popen):
    """Verify worker diagnostic ping and version methods."""
    ping_resp = _rpc_call(worker_proc, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert ping_resp.get("jsonrpc") == "2.0"
    assert ping_resp.get("id") == 1
    assert ping_resp.get("result") == "pong"

    ver_resp = _rpc_call(worker_proc, {"jsonrpc": "2.0", "id": 2, "method": "version"})
    assert ver_resp.get("id") == 2
    res = ver_resp.get("result", {})
    assert res.get("node") == EXPECTED_NODE_VERSION
    assert res.get("mathjax") == EXPECTED_MATHJAX_VERSION


def test_render_inline_formula_emc2(worker_proc: subprocess.Popen):
    """Assert successful conversion of $E=mc^2$ to standalone SVG with self-contained glyphs."""
    req_id = 101
    resp = _rpc_call(worker_proc, {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "render",
        "params": {"tex": "E=mc^2", "display": False, "em": 16, "ex": 8},
    })
    assert resp.get("jsonrpc") == "2.0"
    assert resp.get("id") == req_id
    assert "result" in resp, f"Expected result, got {resp}"
    result = resp["result"]

    svg_xml = result.get("svg")
    assert svg_xml is not None
    assert svg_xml.startswith("<svg")
    assert svg_xml.endswith("</svg>")
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg_xml
    assert "<defs>" in svg_xml and "<path" in svg_xml

    # Verify no external URLs or web-font requests
    urls = re.findall(r"https?://[^\s\"'>]+", svg_xml)
    unexpected = [u for u in urls if u not in ALLOWED_SVG_NAMESPACES]
    assert not unexpected, f"Found unexpected external URLs in SVG: {unexpected}"

    # Verify well-formed XML
    root = ET.fromstring(svg_xml)
    assert root.tag.endswith("svg")

    # Verify metrics presence and format
    assert result.get("width", "").endswith("ex")
    assert result.get("height", "").endswith("ex")
    assert "ex" in result.get("vertical_align", "") or result.get("vertical_align") == "0"


def test_render_display_formula_fraction(worker_proc: subprocess.Popen):
    """Assert display-mode math rendering produces valid SVG with appropriate display metrics."""
    resp = _rpc_call(worker_proc, {
        "jsonrpc": "2.0",
        "id": 201,
        "method": "render",
        "params": {"tex": r"\frac{a + b}{c - d}", "display": True},
    })
    assert resp.get("id") == 201
    result = resp["result"]
    assert result["svg"].startswith("<svg")
    assert "<defs>" in result["svg"]

    # Display math height should be larger than inline baseline
    height = float(result["height"].rstrip("ex"))
    assert height > 2.0


def test_render_complex_matrix_and_greek(worker_proc: subprocess.Popen):
    """Assert AMS matrices and Greek symbols render cleanly."""
    tex = r"\begin{pmatrix} \alpha & \beta \\ \gamma & \delta \end{pmatrix}"
    resp = _rpc_call(worker_proc, {
        "jsonrpc": "2.0",
        "id": 202,
        "method": "render",
        "params": {"tex": tex, "display": True},
    })
    assert resp.get("id") == 202
    assert "result" in resp
    assert "<defs><path" in resp["result"]["svg"]


def test_invalid_tex_syntax_returns_structured_error(worker_proc: subprocess.Popen):
    """Assert invalid TeX produces structured error without killing worker process."""
    resp = _rpc_call(worker_proc, {
        "jsonrpc": "2.0",
        "id": 301,
        "method": "render",
        "params": {"tex": r"\frac{1}{", "display": False},
    })
    assert resp.get("id") == 301
    assert "error" in resp, f"Expected error payload, got {resp}"
    error = resp["error"]
    assert error.get("code") == -32602
    assert "Missing close brace" in error.get("message", "")
    assert worker_proc.poll() is None, "Worker must remain alive after syntax error"


def test_undefined_macro_returns_structured_error(worker_proc: subprocess.Popen):
    """Assert undefined control sequences produce structured error without killing worker process."""
    resp = _rpc_call(worker_proc, {
        "jsonrpc": "2.0",
        "id": 302,
        "method": "render",
        "params": {"tex": r"\nonExistentMacro{x}", "display": False},
    })
    assert resp.get("id") == 302
    assert "error" in resp
    assert resp["error"].get("code") == -32602
    assert "Undefined control sequence" in resp["error"].get("message", "")
    assert worker_proc.poll() is None


def test_worker_remains_usable_after_multiple_errors(worker_proc: subprocess.Popen):
    """Assert interleaved invalid and valid formulas maintain full worker fidelity."""
    # 1. Invalid
    err1 = _rpc_call(worker_proc, {
        "jsonrpc": "2.0",
        "id": 401,
        "method": "render",
        "params": {"tex": r"\begin{matrix} 1 & 2", "display": True},
    })
    assert "error" in err1

    # 2. Valid
    ok1 = _rpc_call(worker_proc, {
        "jsonrpc": "2.0",
        "id": 402,
        "method": "render",
        "params": {"tex": "x + y = z", "display": False},
    })
    assert "result" in ok1
    assert ok1["result"]["svg"].startswith("<svg")

    # 3. Invalid
    err2 = _rpc_call(worker_proc, {
        "jsonrpc": "2.0",
        "id": 403,
        "method": "render",
        "params": {"tex": r"\sqrt{", "display": False},
    })
    assert "error" in err2

    # 4. Valid
    ok2 = _rpc_call(worker_proc, {
        "jsonrpc": "2.0",
        "id": 404,
        "method": "render",
        "params": {"tex": r"\int_0^1 f(x) dx", "display": True},
    })
    assert "result" in ok2
    assert ok2["result"]["svg"].startswith("<svg")


def test_malformed_json_returns_parse_error(worker_proc: subprocess.Popen):
    """Assert unparseable JSON lines return JSON-RPC parse error (-32700) and preserve worker."""
    assert worker_proc.stdin is not None
    assert worker_proc.stdout is not None

    worker_proc.stdin.write("MALFORMED JSON LINE\n")
    worker_proc.stdin.flush()
    resp_line = worker_proc.stdout.readline()
    resp = json.loads(resp_line.strip())

    assert resp.get("error", {}).get("code") == -32700
    assert worker_proc.poll() is None


def test_invalid_request_envelope(worker_proc: subprocess.Popen):
    """Assert invalid request shapes return appropriate JSON-RPC error codes."""
    # Unknown method
    resp1 = _rpc_call(worker_proc, {"jsonrpc": "2.0", "id": 501, "method": "nonExistentMethod"})
    assert resp1.get("error", {}).get("code") == -32601

    # Missing tex parameter
    resp2 = _rpc_call(worker_proc, {"jsonrpc": "2.0", "id": 502, "method": "render", "params": {}})
    assert resp2.get("error", {}).get("code") == -32602


def test_buffer_limits_enforced(worker_proc: subprocess.Popen):
    """Assert TeX input exceeding bounded limit (16 KB) is rejected with error code -32600."""
    huge_tex = "x + " * 6000  # > 16 KB
    resp = _rpc_call(worker_proc, {
        "jsonrpc": "2.0",
        "id": 601,
        "method": "render",
        "params": {"tex": huge_tex, "display": False},
    })
    assert resp.get("id") == 601
    assert "error" in resp
    assert resp["error"].get("code") == -32600
    assert "Buffer limit exceeded" in resp["error"].get("message", "")
    assert worker_proc.poll() is None


def test_subprocess_clean_exit_on_stdin_close(worker_proc: subprocess.Popen):
    """Assert worker exits with code 0 when stdin is closed."""
    assert worker_proc.stdin is not None
    worker_proc.stdin.close()
    exit_code = worker_proc.wait(timeout=5)
    assert exit_code == 0


def test_subprocess_clean_exit_on_sigterm(worker_proc: subprocess.Popen):
    """Assert worker handles SIGTERM cleanly on supported platforms."""
    if os.name == "nt":
        pytest.skip("SIGTERM is POSIX-specific")

    worker_proc.send_signal(signal.SIGTERM)
    exit_code = worker_proc.wait(timeout=5)
    assert exit_code in (0, -signal.SIGTERM)
