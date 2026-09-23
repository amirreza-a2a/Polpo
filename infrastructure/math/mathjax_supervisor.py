"""Persistent supervisor for headless MathJax worker process (TICK-009B).

Manages the background Node.js MathJax worker daemon over line-delimited JSON-RPC 2.0,
enforcing buffer bounds, correlation IDs, auto-restart with exponential backoff,
and graceful shutdown.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from application.ports.math_renderer import MathRenderError
from infrastructure.paths import get_runtime_resource_path

MAX_TEX_LENGTH: int = 16 * 1024  # 16 KB
MAX_SVG_LENGTH: int = 512 * 1024  # 512 KB
MAX_RESTARTS_PER_MINUTE: int = 3
RESTART_WINDOW_SECONDS: float = 60.0


def resolve_node_executable() -> Path:
    """Resolve the Node.js executable according to the standard resolution hierarchy.

    Hierarchy:
    1. Environment variable `POLPO_NODE_PATH`
    2. Bundled runtime under `resources/mathjax/node` (`node.exe` on Windows)
    3. System `PATH`
    """
    env_node = os.environ.get("POLPO_NODE_PATH")
    if env_node:
        candidate = Path(env_node).expanduser().resolve()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate

    ext = ".exe" if os.name == "nt" else ""
    bundled = get_runtime_resource_path(f"resources/mathjax/node{ext}")
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return bundled.resolve()

    system_node = shutil.which(f"node{ext}") or shutil.which("node")
    if system_node:
        system_path = Path(system_node).resolve()
        if system_path.is_file() and os.access(system_path, os.X_OK):
            return system_path

    raise FileNotFoundError(
        "Node.js executable could not be resolved from POLPO_NODE_PATH, "
        "bundled resources, or system PATH."
    )


class MathJaxProcessSupervisor:
    """Supervises the persistent Node.js MathJax worker process."""

    def __init__(
        self,
        node_path: Optional[Path | str] = None,
        worker_script_path: Optional[Path | str] = None,
        auto_start: bool = False,
    ) -> None:
        """Initialize the supervisor with optional explicit runtime paths.

        Args:
            node_path: Explicit path to the Node.js binary (defaults to resolution hierarchy).
            worker_script_path: Explicit path to `mathjax_worker.js`.
            auto_start: Whether to spawn the child process immediately upon construction.
        """
        self._node_path_override: Optional[Path] = (
            Path(node_path).expanduser().resolve() if node_path else None
        )
        self._worker_script_path: Path = (
            Path(worker_script_path).expanduser().resolve()
            if worker_script_path
            else get_runtime_resource_path("resources/mathjax/mathjax_worker.js")
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
    def is_alive(self) -> bool:
        """Return True if the child process is currently running."""
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def _resolve_node(self) -> Path:
        """Return the active Node executable path."""
        if self._node_path_override is not None:
            if not self._node_path_override.is_file() or not os.access(
                self._node_path_override, os.X_OK
            ):
                raise FileNotFoundError(
                    f"Configured Node executable not found or not executable: {self._node_path_override}"
                )
            return self._node_path_override
        return resolve_node_executable()

    def _ensure_process_locked(self) -> subprocess.Popen:
        """Ensure the child process is running, restarting with backoff if needed."""
        if self._is_shutdown:
            raise RuntimeError("MathJaxProcessSupervisor has been shut down.")

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
            raise MathRenderError(
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
            raise FileNotFoundError(
                f"MathJax worker script not found at {self._worker_script_path}"
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
            raise MathRenderError(
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
            raise MathRenderError(
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
            raise MathRenderError(
                code=err.get("code", -32603),
                message=err.get("message", "Unknown MathJax worker error"),
            )

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
            raise MathRenderError(
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
                raise MathRenderError(
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
