# ============================================================
#  tests/integration/test_windows_process_tree.py
#  Real Windows Job Object Process Tree Integration Tests
#  Phase 13 — Ticket #99
# ============================================================

"""
Real Windows Job Object Process Tree Integration Tests.

Validates that:
1. Root, child, and grandchild processes are terminated on timeout.
2. Cooperative cancellation terminates the entire process tree.
3. Lingering descendants are terminated even if the root process exits cleanly (exit code 0).
4. Failure during Job Object setup/assignment never allows the target to run unsupervised.
5. Tree cleanup does not rely solely on the root process exit code.

Gated to execute exclusively in Windows environments (e.g. Windows CI runner).
"""

from pathlib import Path
import sys
import textwrap
import time
from typing import Generator, List

import pytest

from infrastructure.pdf.subprocess_supervisor import SubprocessSupervisor
from infrastructure.pdf.windows_job import (
    JobAssignmentError,
    RealWin32Api,
    WindowsJobObject,
)

# Skip entire test module on non-Windows platforms
pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows Job Object integration tests require Windows platform",
)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_TERMINATE = 0x0001
    STILL_ACTIVE = 259

    def is_pid_alive(pid: int) -> bool:
        """Checks if a Windows process is currently running."""
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(h)

    def terminate_pid(pid: int) -> None:
        """Forcefully terminates a PID for test fixture teardown safety."""
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if h:
            try:
                kernel32.TerminateProcess(h, 1)
            finally:
                kernel32.CloseHandle(h)
else:
    def is_pid_alive(pid: int) -> bool:
        return False

    def terminate_pid(pid: int) -> None:
        pass


def wait_for_pid_file(path: Path, timeout: float = 10.0) -> int:
    """Polls until a PID file exists and contains a valid integer PID."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return int(text)
            except (ValueError, OSError):
                pass
        time.sleep(0.05)
    raise TimeoutError(f"PID file {path} was not created within {timeout}s")


def wait_for_pid_exit(pid: int, timeout: float = 10.0) -> bool:
    """Polls until a process PID is confirmed dead."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def pid_cleanup() -> Generator[List[int], None, None]:
    """Test teardown fixture ensuring no test helper process is leaked on assertion failure."""
    pids: List[int] = []
    try:
        yield pids
    finally:
        for pid in pids:
            try:
                if is_pid_alive(pid):
                    terminate_pid(pid)
            except Exception:
                pass


def test_windows_tree_termination_on_timeout(tmp_path: Path, pid_cleanup: List[int]):
    """
    Scenario 1: Supervised root process spawns child and grandchild.
    Timeout expiry terminates root, child, and grandchild.
    """
    root_pid_file = tmp_path / "root.pid"
    child_pid_file = tmp_path / "child.pid"
    grandchild_pid_file = tmp_path / "grandchild.pid"

    # Create grandchild script: writes PID and sleeps
    grandchild_script = tmp_path / "grandchild.py"
    grandchild_script.write_text(
        textwrap.dedent(f"""
            import os, time
            with open(r"{grandchild_pid_file}", "w") as f:
                f.write(str(os.getpid()))
            time.sleep(60)
        """),
        encoding="utf-8",
    )

    # Create child script: writes PID, spawns grandchild, and sleeps
    child_script = tmp_path / "child.py"
    child_script.write_text(
        textwrap.dedent(f"""
            import os, subprocess, sys, time
            with open(r"{child_pid_file}", "w") as f:
                f.write(str(os.getpid()))
            subprocess.Popen([sys.executable, r"{grandchild_script}"])
            time.sleep(60)
        """),
        encoding="utf-8",
    )

    # Create root script: writes PID, spawns child, and sleeps
    root_script = tmp_path / "root.py"
    root_script.write_text(
        textwrap.dedent(f"""
            import os, subprocess, sys, time
            with open(r"{root_pid_file}", "w") as f:
                f.write(str(os.getpid()))
            subprocess.Popen([sys.executable, r"{child_script}"])
            time.sleep(60)
        """),
        encoding="utf-8",
    )

    supervisor = SubprocessSupervisor()
    cmd = [sys.executable, str(root_script)]

    # Run with timeout=3.0s, giving ample time for tree creation before timeout expiry
    res = supervisor.execute(cmd=cmd, cwd=tmp_path, timeout_seconds=3.0)

    assert res.timed_out is True
    assert res.cancelled is False

    root_pid = wait_for_pid_file(root_pid_file)
    child_pid = wait_for_pid_file(child_pid_file)
    grandchild_pid = wait_for_pid_file(grandchild_pid_file)

    pid_cleanup.extend([root_pid, child_pid, grandchild_pid])

    # Assert that root, child, and grandchild are all dead
    assert wait_for_pid_exit(root_pid, timeout=5.0), f"Root process PID {root_pid} survived timeout"
    assert wait_for_pid_exit(child_pid, timeout=5.0), f"Child process PID {child_pid} survived timeout"
    assert wait_for_pid_exit(grandchild_pid, timeout=5.0), f"Grandchild process PID {grandchild_pid} survived timeout"


def test_windows_tree_termination_on_cancellation(tmp_path: Path, pid_cleanup: List[int]):
    """
    Scenario 2: Cooperative cancellation terminates root and descendants.
    """
    root_pid_file = tmp_path / "root.pid"
    child_pid_file = tmp_path / "child.pid"

    child_script = tmp_path / "child.py"
    child_script.write_text(
        textwrap.dedent(f"""
            import os, time
            with open(r"{child_pid_file}", "w") as f:
                f.write(str(os.getpid()))
            time.sleep(60)
        """),
        encoding="utf-8",
    )

    root_script = tmp_path / "root.py"
    root_script.write_text(
        textwrap.dedent(f"""
            import os, subprocess, sys, time
            with open(r"{root_pid_file}", "w") as f:
                f.write(str(os.getpid()))
            subprocess.Popen([sys.executable, r"{child_script}"])
            time.sleep(60)
        """),
        encoding="utf-8",
    )

    supervisor = SubprocessSupervisor()
    cmd = [sys.executable, str(root_script)]

    # Trigger cancellation as soon as child process is active
    def is_cancelled() -> bool:
        return child_pid_file.exists()

    res = supervisor.execute(cmd=cmd, cwd=tmp_path, timeout_seconds=30.0, is_cancelled=is_cancelled)

    assert res.cancelled is True
    assert res.timed_out is False

    root_pid = wait_for_pid_file(root_pid_file)
    child_pid = wait_for_pid_file(child_pid_file)

    pid_cleanup.extend([root_pid, child_pid])

    assert wait_for_pid_exit(root_pid, timeout=5.0), f"Root process PID {root_pid} survived cancellation"
    assert wait_for_pid_exit(child_pid, timeout=5.0), f"Child process PID {child_pid} survived cancellation"


def test_windows_root_exits_before_descendants_lingering_tree_terminated(
    tmp_path: Path, pid_cleanup: List[int]
):
    """
    Scenario 3: The root exits with code 0 while child remains alive.
    The supervisor guarantees that the child is terminated and cannot outlive the lifecycle.
    """
    root_pid_file = tmp_path / "root.pid"
    child_pid_file = tmp_path / "child.pid"

    child_script = tmp_path / "child.py"
    child_script.write_text(
        textwrap.dedent(f"""
            import os, time
            with open(r"{child_pid_file}", "w") as f:
                f.write(str(os.getpid()))
            time.sleep(60)
        """),
        encoding="utf-8",
    )

    root_script = tmp_path / "root.py"
    root_script.write_text(
        textwrap.dedent(f"""
            import os, subprocess, sys, time
            with open(r"{root_pid_file}", "w") as f:
                f.write(str(os.getpid()))
            subprocess.Popen([sys.executable, r"{child_script}"])
            # Wait until child has written its PID file, then exit immediately
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if os.path.exists(r"{child_pid_file}"):
                    break
                time.sleep(0.05)
            sys.exit(0)
        """),
        encoding="utf-8",
    )

    supervisor = SubprocessSupervisor()
    cmd = [sys.executable, str(root_script)]

    res = supervisor.execute(cmd=cmd, cwd=tmp_path, timeout_seconds=30.0)

    assert res.returncode == 0
    assert res.timed_out is False
    assert res.cancelled is False

    root_pid = wait_for_pid_file(root_pid_file)
    child_pid = wait_for_pid_file(child_pid_file)

    pid_cleanup.extend([root_pid, child_pid])

    # Root already exited, but child MUST be terminated by supervisor Job Object cleanup
    assert wait_for_pid_exit(child_pid, timeout=5.0), f"Lingering child PID {child_pid} survived root exit"


def test_windows_job_assignment_failure_does_not_execute_unsupervised(tmp_path: Path):
    """
    Scenario 4: If Job Object assignment fails, the target process is immediately
    terminated before execution and never runs unsupervised.
    """
    marker_file = tmp_path / "unsupervised_ran.txt"

    target_script = tmp_path / "target.py"
    target_script.write_text(
        textwrap.dedent(f"""
            import os
            with open(r"{marker_file}", "w") as f:
                f.write("UNSUPERVISED")
        """),
        encoding="utf-8",
    )

    # Inject a RealWin32Api derivative that intentionally fails process assignment
    class FailingAssignWin32Api(RealWin32Api):
        def assign_process_to_job(self, job_handle: int, proc_handle: int) -> bool:
            return False

        def get_last_error(self) -> int:
            return 5  # ERROR_ACCESS_DENIED

    supervisor = SubprocessSupervisor(
        job_factory=lambda: WindowsJobObject(win32_api=FailingAssignWin32Api())
    )
    cmd = [sys.executable, str(target_script)]

    with pytest.raises(JobAssignmentError):
        supervisor.execute(cmd=cmd, cwd=tmp_path)

    # Assert that target script was never allowed to execute
    assert not marker_file.exists(), "Target script was executed despite Job Object assignment failure!"


def test_windows_cleanup_does_not_rely_only_on_root_exit_code(
    tmp_path: Path, pid_cleanup: List[int]
):
    """
    Scenario 5: Cleanup verifies process tree elimination rather than relying on
    the root process exit code.
    """
    child_pid_file = tmp_path / "child.pid"

    child_script = tmp_path / "child.py"
    child_script.write_text(
        textwrap.dedent(f"""
            import os, time
            with open(r"{child_pid_file}", "w") as f:
                f.write(str(os.getpid()))
            time.sleep(60)
        """),
        encoding="utf-8",
    )

    root_script = tmp_path / "root.py"
    root_script.write_text(
        textwrap.dedent(f"""
            import os, subprocess, sys, time
            subprocess.Popen([sys.executable, r"{child_script}"])
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if os.path.exists(r"{child_pid_file}"):
                    break
                time.sleep(0.05)
            # Deceptive exit code 0 while descendant is actively running
            sys.exit(0)
        """),
        encoding="utf-8",
    )

    supervisor = SubprocessSupervisor()
    cmd = [sys.executable, str(root_script)]

    res = supervisor.execute(cmd=cmd, cwd=tmp_path, timeout_seconds=30.0)

    assert res.returncode == 0
    child_pid = wait_for_pid_file(child_pid_file)
    pid_cleanup.append(child_pid)

    assert wait_for_pid_exit(child_pid, timeout=5.0), (
        f"Child PID {child_pid} was not terminated despite supervisor completing"
    )
