"""Persistent supervisor for headless MathJax worker process (TICK-009B).

Manages the background Node.js MathJax worker daemon over line-delimited JSON-RPC 2.0,
enforcing buffer bounds, correlation IDs, auto-restart with exponential backoff,
and graceful shutdown.
"""

from __future__ import annotations

import atexit
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from infrastructure.paths import get_runtime_resource_path

logger = logging.getLogger(__name__)

MAX_TEX_LENGTH: int = 16 * 1024  # 16 KB
MAX_SVG_LENGTH: int = 512 * 1024  # 512 KB
MAX_RESTARTS_PER_MINUTE: int = 3
RESTART_WINDOW_SECONDS: float = 60.0
DEFAULT_REQUEST_TIMEOUT_SECONDS: float = 5.0
DEFAULT_STARTUP_TIMEOUT_SECONDS: float = 15.0
WINDOWS_SHELL_SHIM_EXTENSIONS = {".cmd", ".bat", ".ps1"}


def _parse_timeout_env_var(var_name: str, default: float) -> float:
    """Parse and validate a timeout value in seconds from an environment variable.

    If the environment variable is unset, returns default.
    If set to an empty string, non-numeric value, NaN, infinity, or <= 0.0,
    logs a warning and safely falls back to default. Invalid environment variables
    never raise or crash supervisor initialization.
    """
    raw_val = os.environ.get(var_name)
    if raw_val is None:
        return default

    val_str = raw_val.strip()
    if not val_str:
        logger.warning(
            "Environment variable %s is empty; falling back to default %.1fs",
            var_name,
            default,
        )
        return default

    try:
        parsed = float(val_str)
    except ValueError:
        logger.warning(
            "Environment variable %s has non-numeric value %r; falling back to default %.1fs",
            var_name,
            raw_val,
            default,
        )
        return default

    if math.isnan(parsed) or math.isinf(parsed):
        logger.warning(
            "Environment variable %s is not a finite float %r; falling back to default %.1fs",
            var_name,
            raw_val,
            default,
        )
        return default

    if parsed <= 0.0:
        logger.warning(
            "Environment variable %s must be positive (> 0), got %r; falling back to default %.1fs",
            var_name,
            raw_val,
            default,
        )
        return default

    return parsed


def _is_valid_executable_binary(candidate: Path, is_windows: bool) -> bool:
    """Check if candidate path is an executable binary file, rejecting Windows shell shims."""
    if not candidate.is_file():
        return False
    if is_windows and candidate.suffix.lower() in WINDOWS_SHELL_SHIM_EXTENSIONS:
        return False
    return os.access(candidate, os.X_OK)


def _is_windows() -> bool:
    """Return True if running on a Windows platform."""
    return os.name == "nt" or sys.platform == "win32"


def resolve_node_executable() -> Path:
    """Resolve the direct Node.js binary executable according to the standard hierarchy.

    Hierarchy:
    1. Environment variable `POLPO_NODE_PATH`
    2. Bundled runtime under `resources/mathjax/node` (`node.exe` on Windows)
    3. System `PATH` (`node.exe` on Windows)

    On Windows, candidates ending with `.cmd`, `.bat`, or `.ps1` are explicitly skipped
    to prevent intermediate shell wrapper processes from orphaning child Node workers.

    Raises:
        MathWorkerStartupError: If no valid binary executable can be resolved.
    """
    is_windows = _is_windows()

    env_node = os.environ.get("POLPO_NODE_PATH")
    if env_node:
        candidate = Path(env_node).expanduser().resolve()
        if _is_valid_executable_binary(candidate, is_windows):
            return candidate

    ext = ".exe" if is_windows else ""
    bundled = get_runtime_resource_path(f"resources/mathjax/node{ext}")
    if _is_valid_executable_binary(bundled, is_windows):
        return bundled.resolve()

    candidates: List[Optional[str]] = []
    if is_windows:
        candidates.append(shutil.which(f"node{ext}"))
    candidates.append(shutil.which("node"))

    for system_node in candidates:
        if system_node:
            system_path = Path(system_node).resolve()
            if _is_valid_executable_binary(system_path, is_windows):
                return system_path

    raise MathWorkerStartupError(
        code=-32603,
        message=(
            "Node.js executable could not be resolved from POLPO_NODE_PATH, "
            "bundled resources, or system PATH."
        ),
    )


def _map_rpc_error_to_exception(code: int, message: str) -> MathRenderError:
    """Map a JSON-RPC error response from mathjax_worker.js to a structured exception."""
    if code == -32600 and "Buffer limit exceeded" in message:
        return MathBufferLimitExceededError(code=code, message=message)
    if code == -32602:
        if not message.startswith("Invalid params:"):
            return MathSyntaxError(code=code, message=message)
        return MathRenderError(code=code, message=message)
    return MathRenderError(code=code, message=message)


class MathJaxProcessSupervisor:
    """Supervises the persistent Node.js MathJax worker process."""

    def __init__(
        self,
        node_path: Optional[Path | str] = None,
        worker_script_path: Optional[Path | str] = None,
        auto_start: bool = False,
        request_timeout_seconds: Optional[float] = None,
        startup_timeout_seconds: Optional[float] = None,
    ) -> None:
        """Initialize the supervisor with optional explicit runtime paths and timeouts.

        Args:
            node_path: Explicit path to the Node.js binary (defaults to resolution hierarchy).
            worker_script_path: Explicit path to `mathjax_worker.js`.
            auto_start: Whether to spawn the child process immediately upon construction.
            request_timeout_seconds: Optional timeout for rendering requests (seconds).
            startup_timeout_seconds: Optional timeout for worker startup and handshake (seconds).
        """
        self._node_path_override: Optional[Path] = (
            Path(node_path).expanduser().resolve() if node_path else None
        )
        self._worker_script_path: Path = (
            Path(worker_script_path).expanduser().resolve()
            if worker_script_path
            else get_runtime_resource_path("resources/mathjax/mathjax_worker.js")
        )

        if request_timeout_seconds is not None:
            if (
                request_timeout_seconds <= 0.0
                or math.isnan(request_timeout_seconds)
                or math.isinf(request_timeout_seconds)
            ):
                logger.warning(
                    "Explicit request_timeout_seconds invalid (%r); falling back to default %.1fs",
                    request_timeout_seconds,
                    DEFAULT_REQUEST_TIMEOUT_SECONDS,
                )
                self._request_timeout = DEFAULT_REQUEST_TIMEOUT_SECONDS
            else:
                self._request_timeout = float(request_timeout_seconds)
        else:
            self._request_timeout = _parse_timeout_env_var(
                "POLPO_MATHJAX_REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT_SECONDS
            )

        if startup_timeout_seconds is not None:
            if (
                startup_timeout_seconds <= 0.0
                or math.isnan(startup_timeout_seconds)
                or math.isinf(startup_timeout_seconds)
            ):
                logger.warning(
                    "Explicit startup_timeout_seconds invalid (%r); falling back to default %.1fs",
                    startup_timeout_seconds,
                    DEFAULT_STARTUP_TIMEOUT_SECONDS,
                )
                self._startup_timeout = DEFAULT_STARTUP_TIMEOUT_SECONDS
            else:
                self._startup_timeout = float(startup_timeout_seconds)
        else:
            self._startup_timeout = _parse_timeout_env_var(
                "POLPO_MATHJAX_STARTUP_TIMEOUT", DEFAULT_STARTUP_TIMEOUT_SECONDS
            )

        self._process: Optional[subprocess.Popen] = None
        self._lock: threading.Lock = threading.Lock()
        self._next_id: int = 1
        self._restart_timestamps: List[float] = []
        self._is_shutdown: bool = False

        atexit.register(self.shutdown)

        if auto_start:
            with self._lock:
                self._ensure_process_locked()

    @property
    def request_timeout_seconds(self) -> float:
        """Configured timeout deadline for in-flight render RPC requests."""
        return self._request_timeout

    @property
    def startup_timeout_seconds(self) -> float:
        """Configured timeout deadline for worker startup and handshake."""
        return self._startup_timeout

    @property
    def is_alive(self) -> bool:
        """Return True if the child process is currently running."""
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def _resolve_node(self) -> Path:
        """Return the active Node executable path."""
        if self._node_path_override is not None:
            is_windows = _is_windows()
            if not _is_valid_executable_binary(self._node_path_override, is_windows):
                raise MathWorkerStartupError(
                    code=-32603,
                    message=f"Configured Node executable not found or not executable: {self._node_path_override}",
                )
            return self._node_path_override
        return resolve_node_executable()

    def _ensure_process_locked(self) -> subprocess.Popen:
        """Ensure the child process is running, restarting with backoff if needed."""
        if self._is_shutdown:
            raise MathSupervisorShutdownError(
                code=-32603,
                message="MathJaxProcessSupervisor has been shut down.",
            )

        if self._process is not None and self._process.poll() is None:
            return self._process

        # Clean up stale process handles
        self._cleanup_process_handles_locked()

        # Check restart frequency
        now = time.monotonic()
        self._restart_timestamps = [
            t for t in self._restart_timestamps if now - t < RESTART_WINDOW_SECONDS
        ]
        if len(self._restart_timestamps) >= MAX_RESTARTS_PER_MINUTE:
            raise MathCircuitBreakerOpenError(
                code=-32603,
                message=(
                    f"MathJax worker crashed repeatedly ({len(self._restart_timestamps)} "
                    f"times in {RESTART_WINDOW_SECONDS}s). Restart rate limit exceeded."
                ),
            )

        # Apply exponential backoff when restarting after a crash
        if self._restart_timestamps:
            attempt = len(self._restart_timestamps)
            backoff_duration = min(0.05 * (2 ** (attempt - 1)), 1.0)
            time.sleep(backoff_duration)

        self._restart_timestamps.append(time.monotonic())

        node_path = self._resolve_node()
        if not self._worker_script_path.is_file():
            raise MathWorkerStartupError(
                code=-32603,
                message=f"MathJax worker script not found at {self._worker_script_path}",
            )

        popen_kwargs: Dict[str, Any] = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        # Launch worker daemon with pipe I/O
        self._process = subprocess.Popen(
            [str(node_path), str(self._worker_script_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            **popen_kwargs,
        )
        return self._process

    def _cleanup_process_handles_locked(self) -> None:
        """Close pipes and terminate abandoned process instances."""
        if self._process is None:
            return
        proc = self._process
        self._process = None

        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=0.5)
                except (subprocess.TimeoutExpired, Exception):
                    proc.kill()
        except Exception:
            pass

        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass
        try:
            if proc.stdout and not proc.stdout.closed:
                proc.stdout.close()
        except Exception:
            pass
        try:
            if proc.stderr and not proc.stderr.closed:
                proc.stderr.close()
        except Exception:
            pass

    def _call_rpc_locked(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Send a JSON-RPC 2.0 request and wait for the correlated response line."""
        proc = self._ensure_process_locked()

        req_id = self._next_id
        self._next_id += 1

        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as write_err:
            self._cleanup_process_handles_locked()
            raise MathWorkerCrashedError(
                code=-32603,
                message=f"Failed to communicate with MathJax worker: {write_err}",
            )

        assert proc.stdout is not None
        resp_line = proc.stdout.readline()
        if not resp_line:
            stderr_output = ""
            if proc.stderr:
                try:
                    stderr_output = proc.stderr.read()
                except Exception:
                    pass
            self._cleanup_process_handles_locked()
            raise MathWorkerCrashedError(
                code=-32603,
                message=f"MathJax worker process exited unexpectedly. Stderr: {stderr_output.strip()}",
            )

        try:
            response = json.loads(resp_line.strip())
        except json.JSONDecodeError as decode_err:
            self._cleanup_process_handles_locked()
            raise MathRenderError(
                code=-32700,
                message=f"Invalid JSON received from MathJax worker: {decode_err}",
            )

        if response.get("id") != req_id:
            self._cleanup_process_handles_locked()
            raise MathRenderError(
                code=-32603,
                message=f"Request ID mismatch: expected {req_id}, got {response.get('id')}",
            )

        if "error" in response:
            err = response["error"]
            code = err.get("code", -32603)
            message = err.get("message", "Unknown MathJax worker error")
            raise _map_rpc_error_to_exception(code, message)

        return response.get("result")

    def ping(self) -> bool:
        """Send a diagnostic ping request to verify worker connectivity."""
        with self._lock:
            result = self._call_rpc_locked("ping")
            return result == "pong"

    def version(self) -> Dict[str, str]:
        """Query runtime versions from the worker process."""
        with self._lock:
            res = self._call_rpc_locked("version")
            return dict(res) if isinstance(res, dict) else {}

    def render(
        self,
        tex: str,
        display: bool = False,
        em: int = 16,
        ex: int = 8,
    ) -> Dict[str, str]:
        """Render a single TeX math expression to SVG XML with layout metrics.

        Args:
            tex: TeX math expression.
            display: True for display math ($$...$$), False for inline ($...$).
            em: Font em size in pixels.
            ex: Font ex size in pixels.

        Returns:
            Dictionary containing 'svg_xml', 'width', 'height', and 'vertical_align'.

        Raises:
            MathRenderError: If TeX exceeds buffer limits, syntax is invalid,
                             or worker encounters an error.
        """
        tex_bytes = tex.encode("utf-8")
        if len(tex_bytes) > MAX_TEX_LENGTH:
            raise MathBufferLimitExceededError(
                code=-32600,
                message=(
                    f"Buffer limit exceeded: TeX length ({len(tex_bytes)} bytes) "
                    f"exceeds {MAX_TEX_LENGTH} bytes"
                ),
            )

        with self._lock:
            result = self._call_rpc_locked(
                "render",
                {"tex": tex, "display": display, "em": em, "ex": ex},
            )

            if not isinstance(result, dict):
                raise MathRenderError(
                    code=-32603,
                    message="MathJax worker returned unexpected response type.",
                )

            svg_xml = result.get("svg") or result.get("svg_xml") or ""
            svg_bytes = svg_xml.encode("utf-8")
            if len(svg_bytes) > MAX_SVG_LENGTH:
                raise MathBufferLimitExceededError(
                    code=-32600,
                    message=(
                        f"Buffer limit exceeded: SVG output ({len(svg_bytes)} bytes) "
                        f"exceeds {MAX_SVG_LENGTH} bytes"
                    ),
                )

            return {
                "svg_xml": svg_xml,
                "width": str(result.get("width", "0ex")),
                "height": str(result.get("height", "0ex")),
                "vertical_align": str(result.get("vertical_align", "0ex")),
            }

    def shutdown(self) -> None:
        """Terminate the child worker process and close open pipes."""
        with self._lock:
            self._is_shutdown = True
            proc = self._process
            if proc is None:
                return

            if proc.poll() is None:
                if proc.stdin and not proc.stdin.closed:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        proc.terminate()
                        proc.wait(timeout=1)
                    except (subprocess.TimeoutExpired, OSError):
                        try:
                            proc.kill()
                            proc.wait(timeout=1)
                        except OSError:
                            pass

            self._cleanup_process_handles_locked()
