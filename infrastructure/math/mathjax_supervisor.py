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
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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
from infrastructure.math.circuit_breaker import (
    CircuitBreakerState,
    MathCircuitBreaker,
)
from infrastructure.paths import get_runtime_resource_path

logger = logging.getLogger(__name__)

MAX_TEX_LENGTH: int = 16 * 1024  # 16 KB
MAX_SVG_LENGTH: int = 512 * 1024  # 512 KB
MAX_STDERR_BUFFER_BYTES: int = 16 * 1024  # 16 KB
DEFAULT_REQUEST_TIMEOUT_SECONDS: float = 5.0
DEFAULT_STARTUP_TIMEOUT_SECONDS: float = 15.0
WINDOWS_SHELL_SHIM_EXTENSIONS = {".cmd", ".bat", ".ps1"}


class _Sentinel:
    """Base sentinel type for internal supervisor queue signaling."""
    pass


class _EofSentinel(_Sentinel):
    """Pushed by stdout reader thread when worker stdout closes (crash, exit, EOF)."""
    pass


class _AbortSentinel(_Sentinel):
    """Pushed when supervisor is shutting down to wake up pending waiters immediately."""
    pass


class _WriteWatchdog:
    """Watchdog timer that terminates/kills a process if writing to stdin blocks.

    Guarantees deadlock-free timeout escalation (ADR-002 section D04):
    1. Runs on a companion background timer thread.
    2. Does NOT acquire supervisor._lock.
    3. Calls proc.terminate() / proc.kill() directly on the subprocess from outside the lock.
    4. Terminating the process closes the pipe endpoint at OS level, immediately
       unblocking the blocked stdin.write()/flush() call with BrokenPipeError or OSError.
    """

    def __init__(self, proc: subprocess.Popen, timeout: float) -> None:
        self._proc = proc
        self._timeout = timeout
        self._timer: Optional[threading.Timer] = None
        self._triggered: bool = False

    def arm(self) -> None:
        """Start the watchdog countdown timer."""
        self._triggered = False
        self._timer = threading.Timer(self._timeout, self._on_timeout)
        self._timer.daemon = True
        self._timer.name = f"MathJaxWriteWatchdog-pid{getattr(self._proc, 'pid', 'unknown')}"
        self._timer.start()

    def disarm(self) -> None:
        """Cancel the watchdog countdown timer if active."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_timeout(self) -> None:
        """Execute forceful process termination outside supervisor lock on write hang."""
        self._triggered = True
        pid = getattr(self._proc, "pid", None)
        logger.warning(
            "Write watchdog timed out after %.2fs; killing worker process (PID: %s)",
            self._timeout,
            pid,
        )
        try:
            if self._proc.poll() is None:
                self._proc.kill()
        except (ProcessLookupError, OSError):
            pass


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


def _set_exc_process_identity(
    exc: Exception,
    proc: Optional[subprocess.Popen],
    gen: Optional[int],
) -> None:
    """Safely attach process and generation identity to an exception, bypassing frozen dataclass restrictions."""
    try:
        object.__setattr__(exc, "_failed_proc", proc)
        object.__setattr__(exc, "_failed_generation", gen)
    except (AttributeError, TypeError):
        pass


class MathJaxProcessSupervisor:
    """Supervises the persistent Node.js MathJax worker process."""

    def __init__(
        self,
        node_path: Optional[Path | str] = None,
        worker_script_path: Optional[Path | str] = None,
        auto_start: bool = False,
        request_timeout_seconds: Optional[float] = None,
        startup_timeout_seconds: Optional[float] = None,
        circuit_breaker: Optional[MathCircuitBreaker] = None,
        write_timeout_seconds: Optional[float] = None,
    ) -> None:
        """Initialize the supervisor with optional explicit runtime paths and timeouts.

        Args:
            node_path: Explicit path to the Node.js binary (defaults to resolution hierarchy).
            worker_script_path: Explicit path to `mathjax_worker.js`.
            auto_start: Whether to spawn the child process immediately upon construction.
            request_timeout_seconds: Optional timeout for rendering requests (seconds).
            startup_timeout_seconds: Optional timeout for worker startup and handshake (seconds).
            circuit_breaker: Optional injected MathCircuitBreaker instance.
            write_timeout_seconds: Optional timeout for stdin writes/flushes before watchdog kills process.
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

        self._circuit_breaker: MathCircuitBreaker = (
            circuit_breaker if circuit_breaker is not None else MathCircuitBreaker()
        )
        self._write_watchdog_timeout: float = (
            write_timeout_seconds
            if write_timeout_seconds is not None
            else self._request_timeout
        )

        self._process: Optional[subprocess.Popen] = None
        self._lock: threading.Lock = threading.Lock()
        self._state_cv: threading.Condition = threading.Condition(self._lock)
        self._respawning: bool = False
        self._respawn_owner_thread_id: Optional[int] = None
        self._next_id: int = 1
        self._is_shutdown: bool = False
        self._stderr_buffer: BoundedStderrBuffer = BoundedStderrBuffer()
        self._stderr_drain_thread: Optional[threading.Thread] = None
        self._process_generation: int = 0
        self._response_queue: Optional[queue.Queue[Tuple[int, Any]]] = None
        self._stdout_reader_thread: Optional[threading.Thread] = None

        atexit.register(self.shutdown)

        if auto_start:
            failed_proc = None
            failed_gen = None
            try:
                with self._lock:
                    try:
                        self._ensure_process_locked()
                    except (MathWorkerStartupError, MathRenderTimeoutError, MathWorkerCrashedError) as start_exc:
                        failed_proc = getattr(start_exc, "_failed_proc", self._process)
                        failed_gen = getattr(start_exc, "_failed_generation", self._process_generation)
                        raise
            except (MathWorkerStartupError, MathRenderTimeoutError, MathWorkerCrashedError) as exc:
                self._handle_rpc_exception(exc, proc=failed_proc, generation=failed_gen)
                raise

    @property
    def circuit_breaker(self) -> MathCircuitBreaker:
        """Return the active circuit breaker governing worker restarts."""
        return self._circuit_breaker

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

    def _drain_stdout(
        self,
        proc: subprocess.Popen,
        q: queue.Queue[Tuple[int, Any]],
        generation: int,
    ) -> None:
        """Continuously read stdout lines and push them to the response queue until EOF."""
        try:
            if proc.stdout is None:
                return
            for line in iter(proc.stdout.readline, ""):
                q.put((generation, line))
        except (OSError, ValueError):
            pass
        finally:
            q.put((generation, _EofSentinel()))

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

    @property
    def is_shutdown(self) -> bool:
        """Return True if the supervisor has been shut down."""
        return self._is_shutdown

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
        while True:
            if self._is_shutdown:
                raise MathSupervisorShutdownError(
                    code=-32603,
                    message="MathJaxProcessSupervisor has been shut down.",
                )

            # If another thread is currently respawning, wait for it to complete
            if self._respawning:
                self._state_cv.wait()
                continue

            # Check if existing process is healthy
            if self._process is not None:
                is_healthy = False
                try:
                    is_healthy = (self._process.poll() is None)
                except (ProcessLookupError, OSError):
                    is_healthy = False

                if is_healthy:
                    return self._process

            break

        # Claim the respawn lifecycle transition
        self._respawning = True
        self._respawn_owner_thread_id = threading.get_ident()
        stale_proc = self._process
        stale_gen = self._process_generation

        try:
            if stale_proc is not None:
                # Terminate and reap stale process outside supervisor lock (D03/D06)
                lock_was_held = False
                try:
                    self._lock.release()
                    lock_was_held = True
                except RuntimeError:
                    lock_was_held = False

                try:
                    self._terminate_process_outside_lock(stale_proc, reason="RESPAWN")
                finally:
                    if lock_was_held:
                        self._lock.acquire()

                # Re-check shutdown status in case shutdown was called while lock was released
                if self._is_shutdown:
                    raise MathSupervisorShutdownError(
                        code=-32603,
                        message="MathJaxProcessSupervisor has been shut down.",
                    )

                # Clean up stale process handles and confirm reaping under lock
                self._cleanup_process_handles_locked(
                    reason="RESPAWN",
                    process=stale_proc,
                    generation=stale_gen,
                )

                # If another lifecycle operation installed a replacement process while lock was released:
                if self._process is not None and self._process is not stale_proc:
                    is_healthy = False
                    try:
                        is_healthy = (self._process.poll() is None)
                    except (ProcessLookupError, OSError):
                        is_healthy = False
                    if is_healthy:
                        return self._process

                # If the process could not be reaped (e.g. poll() keeps raising OSError or reap timed out),
                # generic OSError does NOT prove reaping (D03). Do not discard reference or spawn replacement!
                if self._process is not None and self._process is stale_proc:
                    reap_exc = MathWorkerStartupError(
                        code=-32603,
                        message=(
                            f"Cannot respawn worker: previous process (PID: {getattr(stale_proc, 'pid', None)}) "
                            f"could not be reaped."
                        ),
                    )
                    _set_exc_process_identity(reap_exc, stale_proc, stale_gen)
                    raise reap_exc

            # Re-check circuit breaker state under supervisor lock (fail fast in O(1))
            if self._circuit_breaker.state == CircuitBreakerState.OPEN:
                raise MathCircuitBreakerOpenError(
                    code=-32603,
                    message="MathJax circuit breaker is OPEN",
                )

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

            # Re-check shutdown status immediately before process launch
            if self._is_shutdown:
                raise MathSupervisorShutdownError(
                    code=-32603,
                    message="MathJaxProcessSupervisor has been shut down.",
                )

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

            # Re-check shutdown status to prevent spawn/shutdown race (ADR-002 D06)
            if self._is_shutdown:
                try:
                    self._process.kill()
                except (ProcessLookupError, OSError):
                    pass
                try:
                    self._process.wait(timeout=0.5)
                except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                    pass
                self._cleanup_process_handles_locked(reason="SHUTDOWN_RACE")
                raise MathSupervisorShutdownError(
                    code=-32603,
                    message="MathJaxProcessSupervisor was shut down during spawn.",
                )

            # Increment generation token and allocate dedicated response queue (ADR-002 D04)
            self._process_generation += 1
            current_gen = self._process_generation
            current_queue: queue.Queue[Tuple[int, Any]] = queue.Queue()
            self._response_queue = current_queue

            self._stderr_drain_thread = threading.Thread(
                target=self._drain_stderr,
                args=(self._process, self._stderr_buffer),
                name="MathJaxStderrDrain",
                daemon=True,
            )
            self._stderr_drain_thread.start()

            self._stdout_reader_thread = threading.Thread(
                target=self._drain_stdout,
                args=(self._process, current_queue, current_gen),
                name=f"MathJaxStdoutReader-gen{current_gen}",
                daemon=True,
            )
            self._stdout_reader_thread.start()

            # Perform synchronous cold-start health handshake (ADR-002 D02)
            try:
                self._perform_startup_handshake_locked(current_gen, current_queue)
            except MathWorkerStartupError as handshake_exc:
                _set_exc_process_identity(handshake_exc, self._process, current_gen)
                raise

            return self._process
        finally:
            self._respawning = False
            self._respawn_owner_thread_id = None
            self._state_cv.notify_all()

    def _perform_startup_handshake_locked(
        self,
        expected_gen: int,
        q: queue.Queue[Tuple[int, Any]],
    ) -> None:
        """Perform synchronous startup health handshake by sending 'ping' and awaiting 'pong'."""
        assert self._process is not None
        req_id = self._next_id
        self._next_id += 1

        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "ping",
        }

        watchdog = None
        if self._write_watchdog_timeout is not None and self._write_watchdog_timeout > 0:
            watchdog = _WriteWatchdog(self._process, self._write_watchdog_timeout)
            watchdog.arm()

        try:
            assert self._process.stdin is not None
            self._process.stdin.write(json.dumps(payload) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as write_err:
            raise MathWorkerStartupError(
                code=-32603,
                message=f"Failed to send startup handshake ping to MathJax worker: {write_err}",
            )
        finally:
            if watchdog is not None:
                watchdog.disarm()

        deadline = time.monotonic() + self._startup_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise MathWorkerStartupError(
                    code=-32603,
                    message=f"MathJax worker startup handshake timed out after {self._startup_timeout:.1f}s.",
                )

            try:
                item = q.get(timeout=max(0.0, remaining))
            except queue.Empty:
                raise MathWorkerStartupError(
                    code=-32603,
                    message=f"MathJax worker startup handshake timed out after {self._startup_timeout:.1f}s.",
                )

            gen, payload_item = item
            if gen != expected_gen:
                logger.warning(
                    "Discarding response from stale generation %s during startup (expected %s)",
                    gen,
                    expected_gen,
                )
                continue

            if isinstance(payload_item, _AbortSentinel):
                raise MathSupervisorShutdownError(
                    code=-32603,
                    message="MathJaxProcessSupervisor has been shut down.",
                )

            if isinstance(payload_item, _EofSentinel):
                stderr_diag = self._stderr_buffer.get_content().strip()
                diag_suffix = f" Stderr: {stderr_diag}" if stderr_diag else ""
                self._cleanup_process_handles_locked(reason="STARTUP_CRASH")
                raise MathWorkerStartupError(
                    code=-32603,
                    message=f"MathJax worker process exited unexpectedly during startup handshake.{diag_suffix}",
                )

            assert isinstance(payload_item, str)
            try:
                response = json.loads(payload_item.strip())
            except json.JSONDecodeError as decode_err:
                raise MathWorkerStartupError(
                    code=-32700,
                    message=f"Invalid JSON received from MathJax worker during startup: {decode_err}",
                )

            if not isinstance(response, dict):
                raise MathWorkerStartupError(
                    code=-32603,
                    message="MathJax worker returned unexpected response type during startup handshake.",
                )

            if response.get("id") != req_id:
                logger.warning(
                    "Dropped mismatched JSON-RPC response ID during startup (expected %s, got %s)",
                    req_id,
                    response.get("id"),
                )
                continue

            if "error" in response:
                err = response["error"]
                raise MathWorkerStartupError(
                    code=err.get("code", -32603),
                    message=f"MathJax worker returned error during startup handshake: {err.get('message', '')}",
                )

            if response.get("result") != "pong":
                raise MathWorkerStartupError(
                    code=-32603,
                    message=f"MathJax worker startup handshake returned unexpected result: {response.get('result')!r}",
                )

            # Handshake successful
            return

    def _terminate_process_outside_lock(self, proc: subprocess.Popen, reason: str = "CLEANUP") -> None:
        """Execute two-phase termination escalation outside supervisor lock (ADR-002 D03/D06).

        Phase 1: proc.terminate() with 0.5s wait.
        Phase 2: proc.kill() with 0.5s reap if still alive.
        """
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

    def _cleanup_process_handles_locked(
        self,
        reason: str = "CLEANUP",
        process: Optional[subprocess.Popen] = None,
        generation: Optional[int] = None,
    ) -> None:
        """Close pipes, drain threads, and clear reaped process handles under supervisor lock.

        If process/generation identity is specified, cleans state ONLY if the current
        supervisor process and generation still match that exact identity.
        If supervisor has already advanced to a newer generation, newer state and handles
        are strictly preserved (ADR-002 D04/D06).
        """
        target_proc = process if process is not None else self._process
        if target_proc is None:
            return

        # Close open stream pipes for the target process safely with narrow exception handling
        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(target_proc, stream_name, None)
            if stream and not getattr(stream, "closed", True):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

        # Check if the supervisor's active process/generation still matches the target
        matches_current = (
            self._process is target_proc
            and (generation is None or self._process_generation == generation)
        )

        if not matches_current:
            logger.info(
                "Skipping handle and thread cleanup for PID %s (gen %s) because supervisor has "
                "already advanced to PID %s (gen %s)",
                getattr(target_proc, "pid", None),
                generation,
                getattr(self._process, "pid", None),
                self._process_generation,
            )
            return

        # Confirm process has been reaped before clearing process reference.
        # ProcessLookupError confirms the process is gone from the OS.
        # Generic OSError does not prove reaping; retain self._process so subsequent
        # cleanup calls can retry process termination/reaping.
        is_reaped = False
        pid = getattr(target_proc, "pid", None)
        try:
            is_reaped = target_proc.poll() is not None
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
            self._stdout_reader_thread = None
            self._response_queue = None
            self._process = None

    def _call_rpc_locked(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Send a JSON-RPC 2.0 request and wait for the correlated response line."""
        if self._circuit_breaker.state == CircuitBreakerState.OPEN:
            raise MathCircuitBreakerOpenError(
                code=-32603,
                message="MathJax circuit breaker is OPEN",
            )

        proc = self._ensure_process_locked()
        expected_gen = self._process_generation
        q = self._response_queue
        assert q is not None

        req_id = self._next_id
        self._next_id += 1

        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        watchdog = None
        if self._write_watchdog_timeout is not None and self._write_watchdog_timeout > 0:
            watchdog = _WriteWatchdog(proc, self._write_watchdog_timeout)
            watchdog.arm()

        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as write_err:
            crashed_exc = MathWorkerCrashedError(
                code=-32603,
                message=f"Failed to communicate with MathJax worker: {write_err}",
            )
            _set_exc_process_identity(crashed_exc, proc, expected_gen)
            raise crashed_exc
        finally:
            if watchdog is not None:
                watchdog.disarm()

        deadline = time.monotonic() + self._request_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                timeout_exc = MathRenderTimeoutError(
                    code=-32603,
                    message=f"MathJax RPC request '{method}' timed out after {self._request_timeout:.1f}s.",
                )
                _set_exc_process_identity(timeout_exc, proc, expected_gen)
                raise timeout_exc

            try:
                item = q.get(timeout=max(0.0, remaining))
            except queue.Empty:
                timeout_exc = MathRenderTimeoutError(
                    code=-32603,
                    message=f"MathJax RPC request '{method}' timed out after {self._request_timeout:.1f}s.",
                )
                _set_exc_process_identity(timeout_exc, proc, expected_gen)
                raise timeout_exc

            gen, payload_item = item
            if gen != expected_gen:
                logger.warning(
                    "Discarding response from stale generation %s (expected %s)",
                    gen,
                    expected_gen,
                )
                continue

            if isinstance(payload_item, _AbortSentinel):
                raise MathSupervisorShutdownError(
                    code=-32603,
                    message="MathJaxProcessSupervisor has been shut down.",
                )

            if isinstance(payload_item, _EofSentinel):
                stderr_diag = self._stderr_buffer.get_content().strip()
                diag_suffix = f" Stderr: {stderr_diag}" if stderr_diag else ""
                crashed_exc = MathWorkerCrashedError(
                    code=-32603,
                    message=f"MathJax worker process exited unexpectedly.{diag_suffix}",
                )
                _set_exc_process_identity(crashed_exc, proc, expected_gen)
                raise crashed_exc

            assert isinstance(payload_item, str)
            try:
                response = json.loads(payload_item.strip())
            except json.JSONDecodeError as decode_err:
                raise MathRenderError(
                    code=-32700,
                    message=f"Invalid JSON received from MathJax worker: {decode_err}",
                )

            if not isinstance(response, dict):
                raise MathRenderError(
                    code=-32603,
                    message="MathJax worker returned unexpected response type.",
                )

            if response.get("id") != req_id:
                logger.warning(
                    "Dropped mismatched JSON-RPC response ID (expected %s, got %s)",
                    req_id,
                    response.get("id"),
                )
                continue

            if "error" in response:
                err = response["error"]
                code = err.get("code", -32603)
                message = err.get("message", "Unknown MathJax worker error")
                raise _map_rpc_error_to_exception(code, message)

            return response.get("result")

    def _handle_rpc_exception(
        self,
        exc: Exception,
        proc: Optional[subprocess.Popen] = None,
        generation: Optional[int] = None,
    ) -> None:
        """Handle lifecycle termination and cleanup outside lock when RPC or startup fails.

        Only terminates and reaps the specific process instance that failed.
        Never terminates or clears handles of a newer process generation (ADR-002 D04/D06).
        """
        if isinstance(exc, (MathSupervisorShutdownError, MathSyntaxError, MathBufferLimitExceededError)):
            return

        reason = "RPC_ERROR"
        if isinstance(exc, MathWorkerStartupError):
            reason = "STARTUP_TIMEOUT"
        elif isinstance(exc, MathRenderTimeoutError):
            reason = "REQUEST_TIMEOUT"
        elif isinstance(exc, MathWorkerCrashedError):
            reason = "CRASHED"
        else:
            return

        target_proc = proc if proc is not None else getattr(exc, "_failed_proc", None)
        target_gen = generation if generation is not None else getattr(exc, "_failed_generation", None)

        claimed_respawn = False
        with self._lock:
            if target_proc is None and target_gen is None:
                target_proc = self._process
                target_gen = self._process_generation
            if target_proc is not None and self._process is target_proc:
                if not self._respawning:
                    self._respawning = True
                self._respawn_owner_thread_id = threading.get_ident()
                claimed_respawn = True
            elif self._respawning and self._respawn_owner_thread_id == threading.get_ident():
                claimed_respawn = True

        try:
            if target_proc is not None:
                # Terminate and reap ONLY the failed process outside lock
                self._terminate_process_outside_lock(target_proc, reason=reason)

                # Acquire supervisor lock to clear handles ONLY if current process/generation matches
                with self._lock:
                    self._cleanup_process_handles_locked(
                        reason=reason,
                        process=target_proc,
                        generation=target_gen,
                    )
        finally:
            if claimed_respawn:
                with self._lock:
                    self._respawning = False
                    self._respawn_owner_thread_id = None
                    self._state_cv.notify_all()

    def _dispatch_rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Dispatch an RPC request under supervisor lock, capturing process identity on failure."""
        failed_proc: Optional[subprocess.Popen] = None
        failed_gen: Optional[int] = None
        try:
            with self._lock:
                try:
                    return self._call_rpc_locked(method, params)
                except (MathWorkerStartupError, MathRenderTimeoutError, MathWorkerCrashedError) as rpc_exc:
                    failed_proc = getattr(rpc_exc, "_failed_proc", self._process)
                    failed_gen = getattr(rpc_exc, "_failed_generation", self._process_generation)
                    if failed_proc is not None and self._process is failed_proc:
                        self._respawning = True
                        self._respawn_owner_thread_id = threading.get_ident()
                    raise
        except (MathWorkerStartupError, MathRenderTimeoutError, MathWorkerCrashedError) as exc:
            self._handle_rpc_exception(exc, proc=failed_proc, generation=failed_gen)
            raise

    def ping(self) -> bool:
        """Send a diagnostic ping request to verify worker connectivity."""
        with self._circuit_breaker.probe_permit() as permit:
            try:
                result = self._dispatch_rpc("ping")
                if result == "pong":
                    permit.record_success()
                    return True
                permit.record_failure()
                return False
            except (MathRenderTimeoutError, MathWorkerCrashedError, MathWorkerStartupError):
                permit.record_failure()
                raise
            except MathSupervisorShutdownError:
                raise

    def version(self) -> Dict[str, str]:
        """Query runtime versions from the worker process."""
        with self._circuit_breaker.probe_permit() as permit:
            try:
                res = self._dispatch_rpc("version")
                if not isinstance(res, dict):
                    permit.record_failure()
                    raise MathRenderError(
                        code=-32603,
                        message="MathJax worker returned unexpected response type for version.",
                    )
                permit.record_success()
                return dict(res)
            except (MathRenderTimeoutError, MathWorkerCrashedError, MathWorkerStartupError):
                permit.record_failure()
                raise
            except MathSupervisorShutdownError:
                raise

    def render(
        self,
        tex: str,
        display: bool = False,
        em: int = 16,
        ex: int = 8,
    ) -> Dict[str, str]:
        """Render a single TeX math expression to SVG XML with layout metrics.

        Canonical evaluation order (ADR-002 section D05):
        1. Pre-flight input validation: MAX_TEX_LENGTH check before acquiring breaker permit.
        2. Circuit breaker probe permit acquisition (fails fast in O(1) if OPEN).
        3. RPC dispatch under supervisor lock.
        4. Post-response output validation: MAX_SVG_LENGTH check.
        5. Probe outcome classification (success vs failure).

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

        with self._circuit_breaker.probe_permit() as permit:
            try:
                result = self._dispatch_rpc(
                    "render",
                    {"tex": tex, "display": display, "em": em, "ex": ex},
                )

                if not isinstance(result, dict):
                    permit.record_failure()
                    raise MathRenderError(
                        code=-32603,
                        message="MathJax worker returned unexpected response type.",
                    )

                svg_xml = result.get("svg") or result.get("svg_xml") or ""
                svg_bytes = svg_xml.encode("utf-8")

                if len(svg_bytes) > MAX_SVG_LENGTH:
                    # Oversized SVG proves worker answered -> SUCCESS
                    permit.record_success()
                    raise MathBufferLimitExceededError(
                        code=-32600,
                        message=(
                            f"Buffer limit exceeded: SVG output ({len(svg_bytes)} bytes) "
                            f"exceeds {MAX_SVG_LENGTH} bytes"
                        ),
                    )

                # Valid worker response -> SUCCESS
                permit.record_success()
                return {
                    "svg_xml": svg_xml,
                    "width": str(result.get("width", "0ex")),
                    "height": str(result.get("height", "0ex")),
                    "vertical_align": str(result.get("vertical_align", "0ex")),
                }

            except MathSyntaxError:
                # TeX syntax error proves worker is responsive -> SUCCESS
                permit.record_success()
                raise

            except MathBufferLimitExceededError:
                # Worker-side -32600 buffer error or SVG length -> SUCCESS
                permit.record_success()
                raise

            except (MathRenderTimeoutError, MathWorkerCrashedError, MathWorkerStartupError):
                # Unrecoverable worker failure -> FAILED probe
                permit.record_failure()
                raise

            except MathSupervisorShutdownError:
                # Neutral lifecycle teardown event; neither success nor failure
                raise

    def shutdown(self) -> None:
        """Terminate the child worker process and close open pipes idempotently.

        Ordered teardown sequence (ADR-002 section D06):
        1. Set self._is_shutdown = True early to reject any new incoming operations.
        2. Push _AbortSentinel to active response queue outside lock to wake RPC waiters immediately.
        3. Release any active circuit breaker probe reservation cleanly.
        4. Terminate worker process outside supervisor lock via two-phase escalation.
        5. Cleanup process handles under the lifecycle lock.
        6. Join reader and stderr drain threads with bounded timeout (<= 0.2s) outside the lock.
        7. Leave supervisor in safe terminal state (repeated calls are no-ops).
        """
        self._is_shutdown = True

        proc = self._process
        q = self._response_queue
        drain_thread = self._stderr_drain_thread
        reader_thread = self._stdout_reader_thread

        # If already cleanly shut down (process and queue are None and not respawning), fast no-op
        if proc is None and q is None and not self._respawning:
            if hasattr(self._circuit_breaker, "release_probe"):
                self._circuit_breaker.release_probe()
            return

        # Wake blocked RPC waiters immediately without waiting out request timeouts
        if q is not None:
            q.put((self._process_generation, _AbortSentinel()))

        # Release active probe reservation so circuit breaker is not left in probe lockout
        if hasattr(self._circuit_breaker, "release_probe"):
            self._circuit_breaker.release_probe()

        # Terminate worker process outside supervisor lock to prevent lock contention
        if proc is not None:
            self._terminate_process_outside_lock(proc, reason="SHUTDOWN")

        # Clean up stream handles and wake condition variable waiters under supervisor lock
        is_reentrant = (self._respawn_owner_thread_id == threading.get_ident())
        if not is_reentrant:
            with self._lock:
                self._cleanup_process_handles_locked(reason="SHUTDOWN")
                self._state_cv.notify_all()
        else:
            self._cleanup_process_handles_locked(reason="SHUTDOWN")
            self._state_cv.notify_all()

        # Join daemon drain and reader threads with bounded timeout outside supervisor lock
        if drain_thread is not None and drain_thread.is_alive():
            if threading.current_thread() != drain_thread:
                drain_thread.join(timeout=0.2)

        if reader_thread is not None and reader_thread.is_alive():
            if threading.current_thread() != reader_thread:
                reader_thread.join(timeout=0.2)
