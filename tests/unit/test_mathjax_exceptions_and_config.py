"""Unit tests for MathJax structured exception hierarchy, timeout env validation,
and Windows binary executable guard (TICK-P07A).
"""

from __future__ import annotations

import ast
import inspect
import logging
import math
import os
import queue
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import application.ports.math_renderer as math_renderer_module
from application.ports.math_renderer import (
    MathBufferLimitExceededError,
    MathCircuitBreakerOpenError,
    MathRenderError,
    MathRenderTimeoutError,
    MathSupervisorShutdownError,
    MathSyntaxError,
    MathWorkerCrashedError,
    MathWorkerStartupError,
)
from infrastructure.math.mathjax_supervisor import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_STARTUP_TIMEOUT_SECONDS,
    MathJaxProcessSupervisor,
    _EofSentinel,
    _map_rpc_error_to_exception,
    _parse_timeout_env_var,
    resolve_node_executable,
)


# ==============================================================================
# 1. Exception Hierarchy & Port Isolation Tests
# ==============================================================================

ALL_SUBCLASSES = [
    MathWorkerStartupError,
    MathRenderTimeoutError,
    MathWorkerCrashedError,
    MathCircuitBreakerOpenError,
    MathSupervisorShutdownError,
    MathBufferLimitExceededError,
    MathSyntaxError,
]


def test_exception_hierarchy_all_subclasses_inherit_from_math_render_error():
    """All 7 structured exception subclasses must inherit from MathRenderError."""
    for cls in ALL_SUBCLASSES:
        assert issubclass(cls, MathRenderError), f"{cls.__name__} must inherit from MathRenderError"
        assert issubclass(cls, Exception), f"{cls.__name__} must inherit from Exception"


def test_math_worker_startup_error_inherits_strictly_from_math_render_error():
    """MathWorkerStartupError must inherit ONLY from MathRenderError, not FileNotFoundError/OSError."""
    assert issubclass(MathWorkerStartupError, MathRenderError)
    assert not issubclass(MathWorkerStartupError, FileNotFoundError)
    assert not issubclass(MathWorkerStartupError, OSError)


def test_subclasses_preserve_code_and_message_and_are_catchable():
    """All subclasses preserve code and message attributes and are catchable by MathRenderError."""
    for cls in ALL_SUBCLASSES:
        err = cls(code=-32600, message=f"Test error message for {cls.__name__}")
        assert err.code == -32600
        assert err.message == f"Test error message for {cls.__name__}"
        assert err.args == (f"Test error message for {cls.__name__}",)
        assert cls.__name__ in str(err)
        assert "-32600" in str(err)
        assert f"Test error message for {cls.__name__}" in str(err)

        # Verify catchable by base class
        caught = False
        try:
            raise err
        except MathRenderError as caught_err:
            caught = True
            assert caught_err.code == -32600
            assert caught_err.message == f"Test error message for {cls.__name__}"
            assert isinstance(caught_err, cls)
        assert caught is True


def test_application_ports_math_renderer_has_no_qt_or_infrastructure_imports():
    """Architecture invariant: application/ports/math_renderer.py has zero Qt/infra imports."""
    source_path = Path(inspect.getfile(math_renderer_module))
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    disallowed_prefixes = ("PySide6", "PyQt5", "PyQt6", "infrastructure", "interfaces")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in disallowed_prefixes:
                    assert not alias.name.startswith(prefix), (
                        f"Forbidden import '{alias.name}' in {source_path}"
                    )
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            for prefix in disallowed_prefixes:
                assert not module_name.startswith(prefix), (
                    f"Forbidden from-import '{module_name}' in {source_path}"
                )


# ==============================================================================
# 2. Timeout Environment Variable Parsing Tests
# ==============================================================================

@pytest.mark.parametrize(
    "env_val,expected",
    [
        ("5.0", 5.0),
        ("15", 15.0),
        ("0.5", 0.5),
        ("120.75", 120.75),
        ("  8.2  ", 8.2),
    ],
)
def test_parse_timeout_env_var_valid_values(env_val: str, expected: float, caplog: pytest.LogCaptureFixture):
    """Valid positive float strings parse cleanly with no warnings."""
    with caplog.at_level(logging.WARNING):
        with patch.dict(os.environ, {"TEST_TIMEOUT_VAR": env_val}):
            val = _parse_timeout_env_var("TEST_TIMEOUT_VAR", default=5.0)
            assert val == expected
            assert not caplog.records


def test_parse_timeout_env_var_unset_returns_default_without_warning(caplog: pytest.LogCaptureFixture):
    """Unset environment variable returns default cleanly without warnings."""
    with caplog.at_level(logging.WARNING):
        with patch.dict(os.environ, {}, clear=True):
            val = _parse_timeout_env_var("NON_EXISTENT_VAR_12345", default=5.0)
            assert val == 5.0
            assert not caplog.records


@pytest.mark.parametrize(
    "invalid_val,reason",
    [
        ("", "empty string"),
        ("   ", "whitespace only"),
        ("0", "zero int"),
        ("0.0", "zero float"),
        ("-1.0", "negative float"),
        ("-10", "negative int"),
        ("not_a_number", "alphanumeric string"),
        ("5s", "string with unit suffix"),
        ("NaN", "not a number float"),
        ("nan", "not a number lowercase"),
        ("Inf", "positive infinity"),
        ("-inf", "negative infinity"),
    ],
)
def test_parse_timeout_env_var_invalid_values_log_warning_and_fallback(
    invalid_val: str,
    reason: str,
    caplog: pytest.LogCaptureFixture,
):
    """Invalid, zero, negative, NaN, Inf, or empty values log a warning and fall back to default."""
    with caplog.at_level(logging.WARNING):
        with patch.dict(os.environ, {"TEST_TIMEOUT_VAR": invalid_val}):
            val = _parse_timeout_env_var("TEST_TIMEOUT_VAR", default=5.0)
            assert val == 5.0
            assert any(rec.levelno == logging.WARNING for rec in caplog.records), (
                f"Expected warning for invalid value '{invalid_val}' ({reason})"
            )


def test_supervisor_constructor_parses_request_and_startup_timeouts(caplog: pytest.LogCaptureFixture):
    """Supervisor initializes request and startup timeouts from environment variables with fallback."""
    env = {
        "POLPO_MATHJAX_REQUEST_TIMEOUT": "8.5",
        "POLPO_MATHJAX_STARTUP_TIMEOUT": "22.0",
    }
    with patch.dict(os.environ, env):
        supervisor = MathJaxProcessSupervisor()
        assert supervisor.request_timeout_seconds == 8.5
        assert supervisor.startup_timeout_seconds == 22.0

    # Malformed env vars should fall back to 5.0 and 15.0 without crashing
    with caplog.at_level(logging.WARNING):
        bad_env = {
            "POLPO_MATHJAX_REQUEST_TIMEOUT": "invalid_timeout",
            "POLPO_MATHJAX_STARTUP_TIMEOUT": "-5.0",
        }
        with patch.dict(os.environ, bad_env):
            supervisor = MathJaxProcessSupervisor()
            assert supervisor.request_timeout_seconds == DEFAULT_REQUEST_TIMEOUT_SECONDS
            assert supervisor.startup_timeout_seconds == DEFAULT_STARTUP_TIMEOUT_SECONDS
            warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
            assert len(warnings) >= 2


def test_supervisor_constructor_explicit_timeout_parameters_override_env():
    """Explicit constructor parameters take precedence over environment variables."""
    env = {
        "POLPO_MATHJAX_REQUEST_TIMEOUT": "8.5",
        "POLPO_MATHJAX_STARTUP_TIMEOUT": "22.0",
    }
    with patch.dict(os.environ, env):
        supervisor = MathJaxProcessSupervisor(
            request_timeout_seconds=3.0,
            startup_timeout_seconds=10.0,
        )
        assert supervisor.request_timeout_seconds == 3.0
        assert supervisor.startup_timeout_seconds == 10.0


# ==============================================================================
# 3. Executable Resolution & Windows Shim Skipping Tests
# ==============================================================================

def test_resolve_node_executable_valid_polpo_node_path(tmp_path: Path):
    """Explicit valid POLPO_NODE_PATH is returned directly."""
    fake_node = tmp_path / "node"
    fake_node.write_text("#!/bin/sh\nexit 0\n")
    fake_node.chmod(0o755)

    with patch.dict(os.environ, {"POLPO_NODE_PATH": str(fake_node)}):
        resolved = resolve_node_executable()
        assert resolved == fake_node.resolve()


def test_resolve_node_executable_skips_windows_shims(tmp_path: Path):
    """On Windows, candidates ending with .cmd, .bat, or .ps1 are skipped."""
    shim_cmd = tmp_path / "node.cmd"
    shim_cmd.write_text("@echo off\n")
    shim_cmd.chmod(0o755)

    shim_bat = tmp_path / "node.bat"
    shim_bat.write_text("@echo off\n")
    shim_bat.chmod(0o755)

    shim_ps1 = tmp_path / "node.ps1"
    shim_ps1.write_text("Write-Host 'hello'\n")
    shim_ps1.chmod(0o755)

    valid_exe = tmp_path / "node.exe"
    valid_exe.write_text("binary")
    valid_exe.chmod(0o755)

    with patch("infrastructure.math.mathjax_supervisor._is_windows", return_value=True):
        # 1. POLPO_NODE_PATH points to a .cmd shim -> skipped, falls back to PATH/bundled
        with patch.dict(os.environ, {"POLPO_NODE_PATH": str(shim_cmd)}):
            with patch("shutil.which", side_effect=lambda cmd: str(valid_exe) if "exe" in cmd or cmd == "node" else None):
                with patch("infrastructure.math.mathjax_supervisor.get_runtime_resource_path", return_value=tmp_path / "nonexistent"):
                    resolved = resolve_node_executable()
                    assert resolved == valid_exe.resolve()

        # 2. POLPO_NODE_PATH points to a .bat shim -> skipped
        with patch.dict(os.environ, {"POLPO_NODE_PATH": str(shim_bat)}):
            with patch("shutil.which", side_effect=lambda cmd: str(valid_exe) if "exe" in cmd or cmd == "node" else None):
                with patch("infrastructure.math.mathjax_supervisor.get_runtime_resource_path", return_value=tmp_path / "nonexistent"):
                    resolved = resolve_node_executable()
                    assert resolved == valid_exe.resolve()

        # 3. POLPO_NODE_PATH points to a .ps1 shim -> skipped
        with patch.dict(os.environ, {"POLPO_NODE_PATH": str(shim_ps1)}):
            with patch("shutil.which", side_effect=lambda cmd: str(valid_exe) if "exe" in cmd or cmd == "node" else None):
                with patch("infrastructure.math.mathjax_supervisor.get_runtime_resource_path", return_value=tmp_path / "nonexistent"):
                    resolved = resolve_node_executable()
                    assert resolved == valid_exe.resolve()

        # 4. Only shims exist on system -> raises MathWorkerStartupError
        with patch.dict(os.environ, {}, clear=True):
            with patch("shutil.which", side_effect=lambda cmd: str(shim_cmd) if cmd == "node" else None):
                with patch("infrastructure.math.mathjax_supervisor.get_runtime_resource_path", return_value=tmp_path / "nonexistent"):
                    with pytest.raises(MathWorkerStartupError) as exc_info:
                        resolve_node_executable()
                    assert exc_info.value.code == -32603
                    assert "Node.js executable could not be resolved" in exc_info.value.message


def test_resolve_node_executable_raises_math_worker_startup_error_when_none_found():
    """When no executable can be resolved, MathWorkerStartupError is raised (not FileNotFoundError)."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", return_value=None):
            with patch("infrastructure.math.mathjax_supervisor.get_runtime_resource_path", return_value=Path("/nonexistent/node")):
                with pytest.raises(MathWorkerStartupError) as exc_info:
                    resolve_node_executable()
                assert exc_info.value.code == -32603
                assert not isinstance(exc_info.value, FileNotFoundError)


def test_supervisor_constructor_with_missing_node_override_raises_math_worker_startup_error():
    """Supervisor initialized with a non-existent explicit node_path raises MathWorkerStartupError."""
    supervisor = MathJaxProcessSupervisor(node_path="/nonexistent/path/to/node")
    with pytest.raises(MathWorkerStartupError) as exc_info:
        supervisor._resolve_node()
    assert exc_info.value.code == -32603
    assert not isinstance(exc_info.value, FileNotFoundError)


def test_supervisor_raises_math_worker_startup_error_on_missing_worker_script(tmp_path: Path):
    """Supervisor raises MathWorkerStartupError when mathjax_worker.js is missing."""
    fake_node = tmp_path / "node"
    fake_node.write_text("#!/bin/sh\nexit 0\n")
    fake_node.chmod(0o755)

    supervisor = MathJaxProcessSupervisor(
        node_path=fake_node,
        worker_script_path=tmp_path / "nonexistent_worker.js",
    )
    with pytest.raises(MathWorkerStartupError) as exc_info:
        supervisor.render("x^2")
    assert exc_info.value.code == -32603
    assert "MathJax worker script not found" in exc_info.value.message
    assert not isinstance(exc_info.value, FileNotFoundError)


# ==============================================================================
# 4. JSON-RPC Error to Exception Mapping Tests
# ==============================================================================

@pytest.mark.parametrize(
    "code,message,expected_cls",
    [
        (-32600, "Buffer limit exceeded: TeX length (20000 bytes) exceeds 16384 bytes", MathBufferLimitExceededError),
        (-32600, "Buffer limit exceeded: SVG output (600000 bytes) exceeds 524288 bytes", MathBufferLimitExceededError),
        (-32602, "Missing close brace", MathSyntaxError),
        (-32602, "Undefined control sequence \\foo", MathSyntaxError),
        (-32602, "Invalid params: 'tex' string parameter is required", MathRenderError),
        (-32602, "Invalid params: expected an object with 'tex'", MathRenderError),
        (-32601, "Method not found", MathRenderError),
        (-32700, "Parse error: invalid JSON", MathRenderError),
        (-32603, "Internal MathJax error", MathRenderError),
    ],
)
def test_rpc_error_to_exception_mapping(code: int, message: str, expected_cls: type):
    """Worker JSON-RPC errors are mapped to specific exception subclasses per the contract."""
    exc = _map_rpc_error_to_exception(code=code, message=message)
    assert type(exc) is expected_cls
    assert exc.code == code
    assert exc.message == message


def test_supervisor_call_rpc_raises_mapped_exceptions():
    """Supervisor._call_rpc_locked transforms JSON-RPC error responses into mapped exception subclasses."""
    supervisor = MathJaxProcessSupervisor()
    q: queue.Queue = queue.Queue()
    supervisor._response_queue = q
    supervisor._process_generation = 1

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()

    # 1. Test syntax error mapping
    q.put((
        1,
        '{"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "Missing close brace"}}\n',
    ))
    with patch.object(supervisor, "_ensure_process_locked", return_value=mock_proc):
        with pytest.raises(MathSyntaxError) as exc_info:
            supervisor._call_rpc_locked("render", {"tex": r"\frac{1}{"})
        assert exc_info.value.code == -32602
        assert "Missing close brace" in exc_info.value.message

    # 2. Test buffer limit error mapping
    supervisor._next_id = 2
    q.put((
        1,
        '{"jsonrpc": "2.0", "id": 2, "error": {"code": -32600, "message": "Buffer limit exceeded: TeX length..."}}\n',
    ))
    with patch.object(supervisor, "_ensure_process_locked", return_value=mock_proc):
        with pytest.raises(MathBufferLimitExceededError) as exc_info:
            supervisor._call_rpc_locked("render", {"tex": "huge"})
        assert exc_info.value.code == -32600

    # 3. Test params validation error mapping (base MathRenderError)
    supervisor._next_id = 3
    q.put((
        1,
        '{"jsonrpc": "2.0", "id": 3, "error": {"code": -32602, "message": "Invalid params: '
        'expected an object with \'tex\'"}}\n',
    ))
    with patch.object(supervisor, "_ensure_process_locked", return_value=mock_proc):
        with pytest.raises(MathRenderError) as exc_info:
            supervisor._call_rpc_locked("render", {})
        assert type(exc_info.value) is MathRenderError
        assert exc_info.value.code == -32602


def test_supervisor_pre_flight_buffer_limit_raises_math_buffer_limit_exceeded_error():
    """Client-side TeX input buffer check (> 16 KB) raises MathBufferLimitExceededError."""
    supervisor = MathJaxProcessSupervisor()
    huge_tex = "x + " * 6000  # > 16 KB
    with pytest.raises(MathBufferLimitExceededError) as exc_info:
        supervisor.render(huge_tex)
    assert exc_info.value.code == -32600
    assert "Buffer limit exceeded" in exc_info.value.message


def test_supervisor_post_response_svg_buffer_limit_raises_math_buffer_limit_exceeded_error():
    """Client-side SVG output buffer check (> 512 KB) raises MathBufferLimitExceededError."""
    supervisor = MathJaxProcessSupervisor()
    huge_svg = "<svg>" + ("a" * (520 * 1024)) + "</svg>"
    with patch.object(supervisor, "_call_rpc_locked", return_value={"svg": huge_svg}):
        with pytest.raises(MathBufferLimitExceededError) as exc_info:
            supervisor.render("x = 1")
        assert exc_info.value.code == -32600
        assert "Buffer limit exceeded: SVG output" in exc_info.value.message


def test_supervisor_shutdown_error_when_already_shut_down():
    """Submitting render requests to a shut down supervisor raises MathSupervisorShutdownError."""
    supervisor = MathJaxProcessSupervisor()
    supervisor.shutdown()
    with pytest.raises(MathSupervisorShutdownError) as exc_info:
        supervisor.render("x^2")
    assert exc_info.value.code == -32603
    assert "shut down" in exc_info.value.message


def test_supervisor_crash_raises_math_worker_crashed_error():
    """Worker EOF / unexpected exit raises MathWorkerCrashedError."""
    supervisor = MathJaxProcessSupervisor()
    q: queue.Queue = queue.Queue()
    supervisor._response_queue = q
    supervisor._process_generation = 1
    q.put((1, _EofSentinel()))

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()

    with patch.object(supervisor, "_ensure_process_locked", return_value=mock_proc):
        with pytest.raises(MathWorkerCrashedError) as exc_info:
            supervisor._call_rpc_locked("ping")
        assert exc_info.value.code == -32603
        assert "exited unexpectedly" in exc_info.value.message
