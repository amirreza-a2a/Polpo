# ============================================================
#  tests/unit/test_windows_job.py
#  Unit tests for Windows Job Object lifecycle management
#  Phase 13 — Ticket #99
# ============================================================

import sys
import threading
import types
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from infrastructure.pdf.windows_job import (
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JobAssignmentError,
    JobClosureError,
    JobConfigurationError,
    JobResumeError,
    SimulatedWin32Api,
    WindowsJobObject,
)


def test_job_object_creates_and_configures_kill_on_close():
    """Verifies Job Object creation enables JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE."""
    api = SimulatedWin32Api()
    job = WindowsJobObject(win32_api=api)

    assert job.handle is not None
    assert "create_job_object" in api.operation_log
    assert f"set_kill_on_close:{job.handle}" in api.operation_log
    assert api.jobs[job.handle]["kill_on_close"] is True

    handle = job.handle
    job.close()
    assert api.jobs[handle]["closed"] is True


def test_job_object_creation_failure_raises():
    """Verifies failure in CreateJobObject raises JobConfigurationError."""
    api = SimulatedWin32Api()
    api.fail_create_job = True

    with pytest.raises(JobConfigurationError, match="CreateJobObjectW failed"):
        WindowsJobObject(win32_api=api)


def test_job_object_configuration_failure_closes_handle_and_raises():
    """Verifies failure in SetInformationJobObject closes acquired handle and raises."""
    api = SimulatedWin32Api()
    api.fail_set_kill_on_close = True

    with pytest.raises(JobConfigurationError, match="JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE"):
        WindowsJobObject(win32_api=api)

    assert any(entry.startswith("close_handle:") for entry in api.operation_log)


def test_job_object_spawn_process_ordering_suspended_assign_resume():
    """Verifies process creation orders: suspended create -> assign to job -> resume thread."""
    api = SimulatedWin32Api()
    job = WindowsJobObject(win32_api=api)

    mock_proc = MagicMock()
    mock_popen = MagicMock(return_value=mock_proc)

    proc = job.spawn_process(["test.exe"], popen_cls=mock_popen)

    assert proc.pid == 8888
    # Check strict operation sequence in log
    create_idx = next(i for i, op in enumerate(api.operation_log) if op.startswith("create_process:"))
    assign_idx = next(i for i, op in enumerate(api.operation_log) if op.startswith("assign_process_to_job:"))
    resume_idx = next(i for i, op in enumerate(api.operation_log) if op.startswith("resume_thread:"))

    assert create_idx < assign_idx < resume_idx, "Process must be assigned before thread is resumed"
    job.close()


def test_job_object_assignment_failure_terminates_suspended_process_and_raises():
    """Verifies failure during process assignment terminates suspended process and does not resume."""
    api = SimulatedWin32Api()
    job = WindowsJobObject(win32_api=api)
    api.fail_assign_process = True

    mock_popen = MagicMock()

    with pytest.raises(JobAssignmentError, match="AssignProcessToJobObject failed"):
        job.spawn_process(["test.exe"], popen_cls=mock_popen)

    assert any(op.startswith("terminate_process:") for op in api.operation_log)
    assert not any(op.startswith("resume_thread:") for op in api.operation_log), "Must not resume thread on assignment failure"
    assert any(op.startswith("close_handle:") for op in api.operation_log)
    job.close()


def test_job_object_resume_failure_terminates_process_and_raises():
    """Verifies failure during ResumeThread terminates process and cleans up handles."""
    api = SimulatedWin32Api()
    job = WindowsJobObject(win32_api=api)
    api.fail_resume_thread = True

    mock_popen = MagicMock()

    with pytest.raises(JobResumeError, match="ResumeThread failed"):
        job.spawn_process(["test.exe"], popen_cls=mock_popen)

    assert any(op.startswith("terminate_process:") for op in api.operation_log)
    assert any(op.startswith("close_handle:") for op in api.operation_log)
    job.close()


def test_job_object_terminate_calls_terminate_job_object():
    """Verifies terminate() delegates to TerminateJobObject with requested exit code."""
    api = SimulatedWin32Api()
    job = WindowsJobObject(win32_api=api)

    success = job.terminate(exit_code=42)

    assert success is True
    assert f"terminate_job_object:{job.handle}:42" in api.operation_log
    job.close()


def test_job_object_query_active_processes():
    """Verifies get_active_processes queries accounting information."""
    api = SimulatedWin32Api()
    api.active_processes_count = 3
    job = WindowsJobObject(win32_api=api)

    active = job.get_active_processes()

    assert active == 3
    assert f"query_active_processes:{job.handle}" in api.operation_log
    job.close()


def test_job_object_close_is_idempotent():
    """Verifies closing a job object multiple times does not raise."""
    api = SimulatedWin32Api()
    job = WindowsJobObject(win32_api=api)

    handle = job.handle
    job.close()
    assert job.handle is None

    # Second close should be safe no-op
    job.close()
    assert job.handle is None


def test_job_object_context_manager():
    """Verifies WindowsJobObject can be used as a context manager and closes handle upon exit."""
    api = SimulatedWin32Api()
    with WindowsJobObject(win32_api=api) as job:
        handle = job.handle
        assert handle is not None

    assert job.handle is None
    assert api.jobs[handle]["closed"] is True


def test_job_object_query_active_processes_failure_raises():
    """Verifies that failure in query_active_processes raises OSError."""
    api = SimulatedWin32Api()
    api.fail_query_active_processes = True
    job = WindowsJobObject(win32_api=api)

    with pytest.raises(OSError, match="QueryInformationJobObject failed"):
        job.get_active_processes()

    job.close()


def test_job_object_spawn_process_with_custom_win32_api():
    """Verifies any custom Win32ApiProtocol implementation routes properly through intercepted creation."""
    class CustomWin32Api:
        def __init__(self) -> None:
            self.ops: List[str] = []

        def create_job_object(self) -> Optional[int]:
            self.ops.append("create_job")
            return 4321

        def set_kill_on_close(self, job_handle: int) -> bool:
            self.ops.append(f"set_kill:{job_handle}")
            return True

        def assign_process_to_job(self, job_handle: int, proc_handle: int) -> bool:
            self.ops.append(f"assign:{job_handle}:{proc_handle}")
            return True

        def resume_thread(self, thread_handle: int) -> int:
            self.ops.append(f"resume:{thread_handle}")
            return 1

        def terminate_job_object(self, job_handle: int, exit_code: int = 1) -> bool:
            return True

        def terminate_process(self, proc_handle: int, exit_code: int = 1) -> bool:
            return True

        def query_active_processes(self, job_handle: int) -> int:
            return 0

        def close_handle(self, handle: int) -> bool:
            self.ops.append(f"close:{handle}")
            return True

        def get_last_error(self) -> int:
            return 0

        def create_process(self, *args: Any, **kwargs: Any) -> Tuple[int, int, int, int]:
            self.ops.append("custom_create")
            return 111, 222, 333, 444

    custom_api = CustomWin32Api()
    job = WindowsJobObject(win32_api=custom_api)

    mock_proc = MagicMock()
    mock_popen = MagicMock(return_value=mock_proc)

    proc = job.spawn_process(["custom.exe"], popen_cls=mock_popen)

    assert proc.pid == 333
    assert "custom_create" in custom_api.ops
    assert "assign:4321:111" in custom_api.ops
    assert "resume:222" in custom_api.ops

    create_idx = custom_api.ops.index("custom_create")
    assign_idx = custom_api.ops.index("assign:4321:111")
    resume_idx = custom_api.ops.index("resume:222")
    assert create_idx < assign_idx < resume_idx

    job.close()
    assert "close:4321" in custom_api.ops


def test_job_object_spawn_process_default_popen_cls_returns_simulated_process():
    """Verifies that calling spawn_process without popen_cls on non-native platform returns simulated process."""
    api = SimulatedWin32Api()
    job = WindowsJobObject(win32_api=api)

    proc = job.spawn_process(["any_cmd.exe"])

    assert proc.pid == 8888
    assert proc.poll() == 0
    assert proc.returncode == 0
    assert proc.stdout.read() == b""
    assert proc.stderr.read() == b""

    proc.kill()
    assert proc.poll() == -9

    job.close()


def test_job_object_close_failure_returns_false_and_preserves_handle():
    """Verifies that when CloseHandle returns False, close() returns False and handle is not cleared."""
    api = SimulatedWin32Api()
    job = WindowsJobObject(win32_api=api)
    handle = job.handle
    assert handle is not None

    api.fail_close_handle = True
    success = job.close()

    assert success is False
    assert job.handle == handle, "Handle must NOT be cleared when CloseHandle fails"


def test_job_object_close_exception_returns_false_and_preserves_handle():
    """Verifies that when CloseHandle raises an exception, close() returns False and handle is not cleared."""
    api = SimulatedWin32Api()
    job = WindowsJobObject(win32_api=api)
    handle = job.handle
    assert handle is not None

    api.raise_on_close_handle = True
    success = job.close()

    assert success is False
    assert job.handle == handle, "Handle must NOT be cleared when CloseHandle raises"


def test_job_object_close_raise_on_error_raises_job_closure_error():
    """Verifies that close(raise_on_error=True) raises JobClosureError on failure."""
    api = SimulatedWin32Api()
    job = WindowsJobObject(win32_api=api)
    api.fail_close_handle = True

    with pytest.raises(JobClosureError, match="Failed to close Job Object handle"):
        job.close(raise_on_error=True)


def test_job_object_context_manager_raises_on_close_failure_on_clean_exit():
    """Verifies context manager exit raises JobClosureError if handle closure fails during clean exit."""
    api = SimulatedWin32Api()
    api.fail_close_handle = True

    with pytest.raises(JobClosureError, match="Failed to close Job Object handle"):
        with WindowsJobObject(win32_api=api):
            pass


def test_job_object_context_manager_preserves_primary_exception_on_close_failure():
    """Verifies context manager preserves primary in-flight exception even if handle close fails."""
    api = SimulatedWin32Api()
    api.fail_close_handle = True

    with pytest.raises(ValueError, match="primary failure"):
        with WindowsJobObject(win32_api=api):
            raise ValueError("primary failure")


def test_supervised_launch_does_not_mutate_global_winapi_createprocess(monkeypatch):
    """Verifies that supervised launch never modifies _winapi.CreateProcess or subprocess._winapi."""
    sentinel_create = object()
    dummy_winapi = types.ModuleType("_winapi")
    dummy_winapi.CreateProcess = sentinel_create
    monkeypatch.setitem(sys.modules, "_winapi", dummy_winapi)

    api = SimulatedWin32Api()
    job = WindowsJobObject(win32_api=api)

    mock_proc = MagicMock()
    mock_popen = MagicMock(return_value=mock_proc)

    job.spawn_process(["cmd.exe"], popen_cls=mock_popen)

    assert sys.modules["_winapi"].CreateProcess is sentinel_create
    job.close()


def test_concurrent_subprocess_launch_isolation():
    """
    Verifies that an unrelated concurrent process launch is not assigned to the Job Object.
    Uses threading synchronization primitives to make the test deterministic.
    """
    launch_in_progress = threading.Event()
    unrelated_launched = threading.Event()

    class HookedSimulatedApi(SimulatedWin32Api):
        def create_process(self, *args: Any, **kwargs: Any) -> Tuple[int, int, int, int]:
            res = super().create_process(*args, **kwargs)
            launch_in_progress.set()
            unrelated_launched.wait(timeout=2.0)
            return res

    api = HookedSimulatedApi()
    job = WindowsJobObject(win32_api=api)

    mock_supervised_proc = MagicMock()
    mock_supervised_popen = MagicMock(return_value=mock_supervised_proc)

    unrelated_proc_handle = 7777
    unrelated_assigned = False

    def unrelated_launcher():
        nonlocal unrelated_assigned
        assert launch_in_progress.wait(timeout=2.0)
        unrelated_assigned = (
            job.handle in api.jobs and unrelated_proc_handle in api.jobs[job.handle]["assigned"]
        )
        unrelated_launched.set()

    t = threading.Thread(target=unrelated_launcher)
    t.start()

    proc = job.spawn_process(["supervised.exe"], popen_cls=mock_supervised_popen)
    t.join(timeout=2.0)

    assert not t.is_alive()
    assert unrelated_assigned is False, "Unrelated process must not be assigned to the Job Object"
    assert len(api.jobs[job.handle]["assigned"]) == 1
    assert api.jobs[job.handle]["assigned"][0] == proc._handle

    job.close()
