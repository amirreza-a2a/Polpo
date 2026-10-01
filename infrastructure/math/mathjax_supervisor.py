"""Persistent supervisor for headless MathJax worker process (TICK-009B).

Manages the background Node.js MathJax worker daemon over line-delimited JSON-RPC 2.0,
enforcing buffer bounds, correlation IDs, auto-restart with exponential backoff,
and graceful shutdown.
"""

from __future__ import annotations

import atexit
import collections
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
MAX_STDERR_BUFFER_BYTES: int = 16 * 1024  # 16 KB
MAX_RESTARTS_PER_MINUTE: int = 3
RESTART_WINDOW_SECONDS: float = 60.0
DEFAULT_REQUEST_TIMEOUT_SECONDS: float = 5.0
DEFAULT_STARTUP_TIMEOUT_SECONDS: float = 15.0
WINDOWS_SHELL_SHIM_EXTENSIONS = {".cmd", ".bat", ".ps1"}


class BoundedStderrBuffer:
    """Thread-safe circular buffer for process stderr diagnostic data.

    Enforces a strict upper bound on retained bytes (default 16 KB),
    discarding oldest entries via FIFO eviction when capacity is exceeded,
    and safely truncating oversized single chunks.
    """

    def __init__(self, max_bytes: int = MAX_STDERR_BUFFER_BYTES) -> None:
        self._max_bytes: int = max_bytes
        self._buffer: collections.deque[str] = collections.deque()
        self._current_bytes: int = 0
        self._lock: threading.Lock = threading.Lock()

    def append(self, text: str) -> None:
        """Append text to the buffer, evicting oldest data if capacity is exceeded."""
        if not text:
            return

        with self._lock:
            encoded_len = len(text.encode("utf-8", errors="replace"))

            if encoded_len >= self._max_bytes:
                # Retain only the tail of the chunk up to max_bytes
                tail = text[-self._max_bytes:]
                while len(tail.encode("utf-8", errors="replace")) > self._max_bytes:
                    tail = tail[1:]
                self._buffer.clear()
                self._buffer.append(tail)
                self._current_bytes = len(tail.encode("utf-8", errors="replace"))
                return

            self._buffer.append(text)
            self._current_bytes += encoded_len

            while self._current_bytes > self._max_bytes and self._buffer:
                excess = self._current_bytes - self._max_bytes
                first_len = len(self._buffer[0].encode("utf-8", errors="replace"))
                if first_len <= excess:
                    self._buffer.popleft()
                    self._current_bytes -= first_len
                else:
                    oldest = self._buffer.popleft()
                    trimmed = oldest[excess:]
                    trimmed_len = len(trimmed.encode("utf-8", errors="replace"))
                    while trimmed_len > (first_len - excess):
                        trimmed = trimmed[1:]
                        trimmed_len = len(trimmed.encode("utf-8", errors="replace"))
                    self._buffer.appendleft(trimmed)
                    self._current_bytes -= (first_len - trimmed_len)
                    break

    def get_content(self) -> str:
        """Return the complete retained stderr content as a single string."""
        with self._lock:
            return "".join(self._buffer)

    def clear(self) -> None:
        """Reset the buffer to empty."""
        with self._lock:
            self._buffer.clear()
            self._current_bytes = 0


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
        self._stderr_buffer: BoundedStderrBuffer = BoundedStderrBuffer()
        self._stderr_drain_thread: Optional[threading.Thread] = None

        atexit.register(self.shutdown)

        if auto_start:
            with self._lock:
                self._ensure_process_locked()

    def get_stderr_diagnostics(self) -> str:
        """Return the bounded recent stderr diagnostic buffer."""
        return self._stderr_buffer.get_content()

    def _drain_stderr(self, proc: subprocess.Popen, buffer: BoundedStderrBuffer) -> None:
        """Continuously read stderr lines into the bounded buffer until EOF or stream closure."""
        try:
            if proc.stderr is None:
                return
            for line in iter(proc.stderr.readline, ""):
                buffer.append(line)
        except (OSError, ValueError):
            pass

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
        self._cleanup_process_handles_locked(reason="RESPAWN")

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

        self._stderr_buffer = BoundedStderrBuffer()

        # Launch worker daemon directly with pipe I/O without shell
        self._process = subprocess.Popen(
            [str(node_path), str(self._worker_script_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            shell=False,
            **popen_kwargs,
        )

        self._stderr_drain_thread = threading.Thread(
            target=self._drain_stderr,
            args=(self._process, self._stderr_buffer),
            name="MathJaxStderrDrain",
            daemon=True,
        )
        self._stderr_drain_thread.start()

        return self._process

    def _cleanup_process_handles_locked(self, reason: str = "CLEANUP") -> None:
        """Close pipes, drain threads, and terminate abandoned process instances.

        Implements the two-phase cross-platform termination escalation protocol (D03):
        Phase 1: proc.terminate() with 0.5s wait.
        Phase 2: proc.kill() with 0.5s reap if still alive.
        """
        if self._process is None:
            return
        proc = self._process
        pid = getattr(proc, "pid", None)

        try:
            is_alive = False
            try:
                is_alive = proc.poll() is None
            except ProcessLookupError:
                is_alive = False
            except OSError:
                is_alive = True

            if is_alive:
                logger.warning(
                    "Terminating MathJax worker process (PID: %s, reason: %s)",
                    pid,
                    reason,
                )

                # Phase 1: Graceful termination request
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    logger.warning(
                        "proc.terminate() failed with OSError (PID: %s): %s",
                        pid,
                        exc,
                    )

                # Bounded grace period for graceful termination
                try:
                    proc.wait(timeout=0.5)
                except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                    pass

                # Check if process remains alive after Phase 1 attempt
                still_alive = False
                try:
                    still_alive = proc.poll() is None
                except ProcessLookupError:
                    still_alive = False
                except OSError:
                    still_alive = True

                # Phase 2: Forceful kill escalation if still alive
                if still_alive:
                    stderr_diag = self._stderr_buffer.get_content().strip()
                    diag_suffix = f" | stderr: {stderr_diag}" if stderr_diag else ""
                    logger.warning(
                        "MathJax worker did not exit after SIGTERM grace period; "
                        "escalating to SIGKILL (PID: %s, reason: %s)%s",
                        pid,
                        reason,
                        diag_suffix,
                    )
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    except OSError as exc:
                        logger.warning(
                            "proc.kill() failed with OSError (PID: %s): %s",
                            pid,
                            exc,
                        )

                    try:
                        proc.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        stderr_diag = self._stderr_buffer.get_content().strip()
                        diag_suffix = f" | stderr: {stderr_diag}" if stderr_diag else ""
                        logger.warning(
                            "MathJax worker failed to exit after SIGKILL reap timeout; "
                            "retaining process reference (PID: %s, reason: %s)%s",
                            pid,
                            reason,
                            diag_suffix,
                        )
                    except ProcessLookupError:
                        pass
                    except OSError:
                        pass
        except (ProcessLookupError, OSError):
            pass

        # Close open stream pipes safely with narrow exception handling
        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(proc, stream_name, None)
            if stream and not getattr(stream, "closed", True):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

        # Join the daemon stderr drain thread with a short bounded timeout
        if self._stderr_drain_thread and self._stderr_drain_thread.is_alive():
            if threading.current_thread() != self._stderr_drain_thread:
                self._stderr_drain_thread.join(timeout=0.2)

        # Confirm process has been reaped before clearing process reference.
        # ProcessLookupError confirms the process is gone from the OS.
        # Generic OSError does not prove reaping; retain self._process so subsequent
        # cleanup calls can retry process termination/reaping.
        is_reaped = False
        try:
            is_reaped = proc.poll() is not None
        except ProcessLookupError:
            is_reaped = True
        except OSError as exc:
            logger.warning(
                "MathJax worker process poll failed with OSError (PID: %s): %s; "
                "retaining process reference",
                pid,
                exc,
            )
            is_reaped = False

        if is_reaped:
            self._stderr_drain_thread = None
            self._process = None

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
            self._cleanup_process_handles_locked(reason="BROKEN_PIPE")
            raise MathWorkerCrashedError(
                code=-32603,
                message=f"Failed to communicate with MathJax worker: {write_err}",
            )

        assert proc.stdout is not None
        resp_line = proc.stdout.readline()
        if not resp_line:
            stderr_output = self._stderr_buffer.get_content()
            self._cleanup_process_handles_locked(reason="CRASHED")
            raise MathWorkerCrashedError(
                code=-32603,
                message=f"MathJax worker process exited unexpectedly. Stderr: {stderr_output.strip()}",
            )

        try:
            response = json.loads(resp_line.strip())
        except json.JSONDecodeError as decode_err:
            self._cleanup_process_handles_locked(reason="INVALID_JSON")
            raise MathRenderError(
                code=-32700,
                message=f"Invalid JSON received from MathJax worker: {decode_err}",
            )

        if response.get("id") != req_id:
            self._cleanup_process_handles_locked(reason="ID_MISMATCH")
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
            self._cleanup_process_handles_locked(reason="SHUTDOWN")
