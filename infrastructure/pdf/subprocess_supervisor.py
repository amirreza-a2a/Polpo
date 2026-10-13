# ============================================================
#  infrastructure/pdf/subprocess_supervisor.py
#  Cross-Platform Subprocess Supervisor & Lifecycle Manager
#  Phase 13 — Ticket #99
# ============================================================

"""
Subprocess Supervisor for Quarto CLI and XeLaTeX Compilers.

Security Trust Boundary Disclosure (Mandatory F-03):
---------------------------------------------------
Child processes execute under ordinary desktop user privileges.
The compilation flag -no-shell-escape strictly blocks arbitrary shell command
execution and subprocess invocation via \\write18. However, -no-shell-escape does NOT
provide operating-system filesystem read sandboxing for native TeX primitives
(such as \\input and \\openin).
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import signal
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

_DEFAULT_TIMEOUT_SECONDS: float = 60.0
_MAX_STREAM_BYTES: int = 5 * 1024 * 1024  # 5 MB per stream hard cap
_WIN32_CREATE_NO_WINDOW: int = 0x08000000


@dataclass(frozen=True)
class SubprocessResult:
    """
    Structured outcome of a supervised subprocess execution.

    Attributes:
        returncode: Process exit code, or -1 if terminated abnormally.
        stdout: Captured standard output, truncated at maximum size cap.
        stderr: Captured standard error, truncated at maximum size cap.
        timed_out: Whether execution was terminated due to timeout expiry.
        cancelled: Whether execution was terminated due to cancellation signal.
    """
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False


class SubprocessSupervisor:
    """
    Cross-platform process execution manager for Quarto and XeLaTeX.

    Enforces environment sanitization, execution timeouts, bounded output collection,
    and process-tree cancellation.

    Security Trust Boundary (Mandatory F-03):
    Child processes execute under ordinary desktop user privileges.
    -no-shell-escape blocks shell command execution (\\write18) but does not provide
    OS filesystem read sandboxing for TeX primitives.
    """

    def __init__(self, max_stream_bytes: int = _MAX_STREAM_BYTES) -> None:
        self._max_stream_bytes = max_stream_bytes

    def sanitize_environment(
        self,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """
        Builds a sanitized environment dictionary based on os.environ.

        Strips any QUARTO_* variables and TEXINPUTS overrides to prevent
        unintended profile or path hijacking, retaining standard system essentials.
        """
        env: Dict[str, str] = {}
        for key, value in os.environ.items():
            upper_key = key.upper()
            if upper_key.startswith("QUARTO_"):
                continue
            if upper_key == "TEXINPUTS" or upper_key.startswith("TEXINPUTS"):
                continue
            env[key] = value

        if extra_env:
            for k, v in extra_env.items():
                upper_k = k.upper()
                if upper_k.startswith("QUARTO_"):
                    continue
                if upper_k == "TEXINPUTS" or upper_k.startswith("TEXINPUTS"):
                    continue
                env[k] = v

        return env

    def execute(
        self,
        cmd: List[str],
        cwd: Path,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        is_cancelled: Optional[Callable[[], bool]] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> SubprocessResult:
        """
        Executes a subprocess command with sanitization, timeouts, and cancellation.

        Args:
            cmd: Command argument list.
            cwd: Working directory.
            timeout_seconds: Maximum allowed wall-clock seconds before termination.
            is_cancelled: Optional polling callable signaling cooperative cancellation.
            extra_env: Optional supplemental environment variables.

        Returns:
            SubprocessResult with exit status, captured streams, and timeout/cancellation flags.
        """
        is_windows = platform.system() == "Windows"
        env = self.sanitize_environment(extra_env)

        popen_kwargs: Dict[str, object] = {
            "cwd": str(cwd),
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
        }

        if is_windows:
            popen_kwargs["creationflags"] = _WIN32_CREATE_NO_WINDOW
        else:
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **popen_kwargs)  # type: ignore[call-overload]

        stdout_chunks: List[bytes] = []
        stderr_chunks: List[bytes] = []

        def _reader(stream, chunks: List[bytes]) -> None:
            bytes_collected = 0
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    if bytes_collected < self._max_stream_bytes:
                        allowed = min(len(chunk), self._max_stream_bytes - bytes_collected)
                        chunks.append(chunk[:allowed])
                        bytes_collected += allowed
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        t_out = threading.Thread(target=_reader, args=(proc.stdout, stdout_chunks), daemon=True)
        t_err = threading.Thread(target=_reader, args=(proc.stderr, stderr_chunks), daemon=True)
        t_out.start()
        t_err.start()

        start_time = time.monotonic()
        timed_out = False
        cancelled = False

        while True:
            ret = proc.poll()
            if ret is not None:
                break

            if is_cancelled and is_cancelled():
                cancelled = True
                self._kill_process_tree(proc, is_windows)
                break

            if time.monotonic() - start_time > timeout_seconds:
                timed_out = True
                self._kill_process_tree(proc, is_windows)
                break

            time.sleep(0.05)

        t_out.join(timeout=2.0)
        t_err.join(timeout=2.0)

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

        stdout_text = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")

        final_returncode = proc.returncode if proc.returncode is not None else -1

        return SubprocessResult(
            returncode=final_returncode,
            stdout=stdout_text,
            stderr=stderr_text,
            timed_out=timed_out,
            cancelled=cancelled,
        )

    def _kill_process_tree(self, proc: subprocess.Popen, is_windows: bool) -> None:
        """
        Forcefully terminates child process and all its descendants.

        On Windows, invokes 'taskkill /F /T /PID <pid>' to eliminate child compilers.
        On POSIX, signals the child process group via SIGKILL.
        """
        pid = proc.pid
        if is_windows:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    creationflags=_WIN32_CREATE_NO_WINDOW,
                    capture_output=True,
                    timeout=5.0,
                )
            except Exception:
                pass
        else:
            if hasattr(os, "getpgid") and hasattr(os, "killpg"):
                try:
                    pgid = os.getpgid(pid)
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    # If session leader has already exited, getpgid(pid) raises ProcessLookupError,
                    # but descendant processes in process group (PGID == pid) may still be running.
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                except (PermissionError, OSError):
                    pass

        try:
            proc.kill()
        except Exception:
            pass

        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass
