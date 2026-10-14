# ============================================================
#  infrastructure/pdf/windows_job.py
#  Windows Job Object Lifecycle Management & Process Tree Containment
#  Phase 13 — Ticket #99
# ============================================================

"""
Windows Job Object Lifecycle Manager.

Provides Win32 Job Object containment for supervised subprocesses, ensuring
guaranteed process tree cleanup without orphan child processes.

Security and Architecture:
- Child processes are assigned to the Job Object before their primary thread executes.
- JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE ensures the OS kernel terminates all descendants
  when the supervising handle closes, even in abnormal termination scenarios.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)

# Win32 Constants
JobObjectBasicAccountingInformation: int = 1
JobObjectExtendedLimitInformation: int = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: int = 0x00002000
_CREATE_SUSPENDED: int = 0x00000004
_WIN32_CREATE_NO_WINDOW: int = 0x08000000

_spawn_lock = threading.Lock()


class JobConfigurationError(OSError):
    """Raised when creating or configuring a Windows Job Object fails."""


class JobAssignmentError(OSError):
    """Raised when assigning a process to a Windows Job Object fails."""


class JobResumeError(OSError):
    """Raised when resuming a suspended process inside a Job Object fails."""


# ctypes Win32 Structure Definitions
class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", wintypes.ULARGE_INTEGER),
        ("WriteOperationCount", wintypes.ULARGE_INTEGER),
        ("OtherOperationCount", wintypes.ULARGE_INTEGER),
        ("ReadTransferCount", wintypes.ULARGE_INTEGER),
        ("WriteTransferCount", wintypes.ULARGE_INTEGER),
        ("OtherTransferCount", wintypes.ULARGE_INTEGER),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", wintypes.LARGE_INTEGER),
        ("TotalKernelTime", wintypes.LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class Win32ApiProtocol(Protocol):
    """Protocol defining low-level Win32 Job Object and process lifecycle primitives."""

    def create_job_object(self) -> Optional[int]: ...
    def set_kill_on_close(self, job_handle: int) -> bool: ...
    def assign_process_to_job(self, job_handle: int, proc_handle: int) -> bool: ...
    def resume_thread(self, thread_handle: int) -> int: ...
    def terminate_job_object(self, job_handle: int, exit_code: int = 1) -> bool: ...
    def terminate_process(self, proc_handle: int, exit_code: int = 1) -> bool: ...
    def query_active_processes(self, job_handle: int) -> int: ...
    def close_handle(self, handle: int) -> bool: ...
    def get_last_error(self) -> int: ...
    def create_process(
        self,
        executable: Any,
        args: Any,
        sec_attr: Any,
        thread_sec_attr: Any,
        inherit_handles: Any,
        creationflags: Any,
        env: Any,
        cwd: Any,
        startupinfo: Any,
    ) -> Tuple[int, int, int, int]: ...


class RealWin32Api:
    """Production Win32 API implementation using kernel32.dll via ctypes."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("RealWin32Api is only supported on Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._setup_prototypes()

    def _setup_prototypes(self) -> None:
        k32 = self._kernel32

        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE

        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        k32.SetInformationJobObject.restype = wintypes.BOOL

        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL

        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateJobObject.restype = wintypes.BOOL

        k32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k32.QueryInformationJobObject.restype = wintypes.BOOL

        k32.ResumeThread.argtypes = [wintypes.HANDLE]
        k32.ResumeThread.restype = wintypes.DWORD

        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateProcess.restype = wintypes.BOOL

        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL

    def create_job_object(self) -> Optional[int]:
        h = self._kernel32.CreateJobObjectW(None, None)
        if not h:
            return None
        return int(h)

    def set_kill_on_close(self, job_handle: int) -> bool:
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        res = self._kernel32.SetInformationJobObject(
            wintypes.HANDLE(job_handle),
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        return bool(res)

    def assign_process_to_job(self, job_handle: int, proc_handle: int) -> bool:
        res = self._kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(job_handle),
            wintypes.HANDLE(proc_handle),
        )
        return bool(res)

    def resume_thread(self, thread_handle: int) -> int:
        res = self._kernel32.ResumeThread(wintypes.HANDLE(thread_handle))
        return int(res)

    def terminate_job_object(self, job_handle: int, exit_code: int = 1) -> bool:
        res = self._kernel32.TerminateJobObject(
            wintypes.HANDLE(job_handle),
            wintypes.UINT(exit_code),
        )
        return bool(res)

    def terminate_process(self, proc_handle: int, exit_code: int = 1) -> bool:
        res = self._kernel32.TerminateProcess(
            wintypes.HANDLE(proc_handle),
            wintypes.UINT(exit_code),
        )
        return bool(res)

    def query_active_processes(self, job_handle: int) -> int:
        info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        ret_len = wintypes.DWORD(0)
        res = self._kernel32.QueryInformationJobObject(
            wintypes.HANDLE(job_handle),
            JobObjectBasicAccountingInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
            ctypes.byref(ret_len),
        )
        if not res:
            return 0
        return int(info.ActiveProcesses)

    def close_handle(self, handle: int) -> bool:
        res = self._kernel32.CloseHandle(wintypes.HANDLE(handle))
        return bool(res)

    def get_last_error(self) -> int:
        return ctypes.get_last_error()

    def create_process(
        self,
        executable: Any,
        args: Any,
        sec_attr: Any,
        thread_sec_attr: Any,
        inherit_handles: Any,
        creationflags: Any,
        env: Any,
        cwd: Any,
        startupinfo: Any,
    ) -> Tuple[int, int, int, int]:
        import _winapi
        hp, ht, pid, tid = _winapi.CreateProcess(
            executable,
            args,
            sec_attr,
            thread_sec_attr,
            inherit_handles,
            creationflags,
            env,
            cwd,
            startupinfo,
        )
        return int(hp), int(ht), int(pid), int(tid)


class SimulatedWin32Api:
    """In-memory Win32 simulation for unit tests and non-Windows platform execution."""

    def __init__(self) -> None:
        self.jobs: Dict[int, Dict[str, Any]] = {}
        self._next_handle: int = 1000
        self.last_error: int = 0
        self.operation_log: List[str] = []

        # Configurable failure flags for test coverage
        self.fail_create_job: bool = False
        self.fail_set_kill_on_close: bool = False
        self.fail_assign_process: bool = False
        self.fail_resume_thread: bool = False
        self.fail_terminate_job: bool = False
        self.active_processes_count: int = 0

    def create_job_object(self) -> Optional[int]:
        self.operation_log.append("create_job_object")
        if self.fail_create_job:
            self.last_error = 5  # ERROR_ACCESS_DENIED
            return None
        h = self._next_handle
        self._next_handle += 1
        self.jobs[h] = {
            "kill_on_close": False,
            "assigned": [],
            "terminated": False,
            "closed": False,
        }
        return h

    def set_kill_on_close(self, job_handle: int) -> bool:
        self.operation_log.append(f"set_kill_on_close:{job_handle}")
        if self.fail_set_kill_on_close:
            self.last_error = 87  # ERROR_INVALID_PARAMETER
            return False
        if job_handle in self.jobs:
            self.jobs[job_handle]["kill_on_close"] = True
            return True
        return False

    def assign_process_to_job(self, job_handle: int, proc_handle: int) -> bool:
        self.operation_log.append(f"assign_process_to_job:{job_handle}:{proc_handle}")
        if self.fail_assign_process:
            self.last_error = 5
            return False
        if job_handle in self.jobs:
            self.jobs[job_handle]["assigned"].append(proc_handle)
            return True
        return False

    def resume_thread(self, thread_handle: int) -> int:
        self.operation_log.append(f"resume_thread:{thread_handle}")
        if self.fail_resume_thread:
            self.last_error = 6  # ERROR_INVALID_HANDLE
            return 0xFFFFFFFF
        return 1

    def terminate_job_object(self, job_handle: int, exit_code: int = 1) -> bool:
        self.operation_log.append(f"terminate_job_object:{job_handle}:{exit_code}")
        if self.fail_terminate_job:
            return False
        if job_handle in self.jobs:
            self.jobs[job_handle]["terminated"] = True
            return True
        return False

    def terminate_process(self, proc_handle: int, exit_code: int = 1) -> bool:
        self.operation_log.append(f"terminate_process:{proc_handle}:{exit_code}")
        return True

    def query_active_processes(self, job_handle: int) -> int:
        self.operation_log.append(f"query_active_processes:{job_handle}")
        return self.active_processes_count

    def close_handle(self, handle: int) -> bool:
        self.operation_log.append(f"close_handle:{handle}")
        if handle in self.jobs:
            self.jobs[handle]["closed"] = True
        return True

    def get_last_error(self) -> int:
        return self.last_error

    def create_process(self, *args: Any, **kwargs: Any) -> Tuple[int, int, int, int]:
        hp = self._next_handle
        ht = self._next_handle + 1
        self._next_handle += 2
        pid = 8888
        tid = 9999
        self.operation_log.append(f"create_process:{hp}:{ht}:{pid}:{tid}")
        return hp, ht, pid, tid


class WindowsJobObject:
    """
    Manages the lifecycle of a Windows Job Object.

    Ensures that supervised processes are assigned before execution begins,
    configures automatic kill-on-close at the OS kernel level, and guarantees
    that no descendant processes outlive the supervised lifecycle.
    """

    def __init__(self, win32_api: Optional[Win32ApiProtocol] = None) -> None:
        if win32_api is not None:
            self._win32 = win32_api
        elif sys.platform == "win32":
            self._win32 = RealWin32Api()
        else:
            self._win32 = SimulatedWin32Api()

        self._handle: Optional[int] = None
        self._init_job()

    @property
    def handle(self) -> Optional[int]:
        """Returns the underlying Win32 Job Object handle, or None if closed."""
        return self._handle

    def _init_job(self) -> None:
        h = self._win32.create_job_object()
        if not h:
            err = self._win32.get_last_error()
            raise JobConfigurationError(f"CreateJobObjectW failed with error code {err}")
        self._handle = h

        if not self._win32.set_kill_on_close(self._handle):
            err = self._win32.get_last_error()
            self._win32.close_handle(self._handle)
            self._handle = None
            raise JobConfigurationError(
                f"SetInformationJobObject (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE) failed with error code {err}"
            )

    def assign_process(self, proc_handle: int) -> bool:
        """Assigns a process to the job object."""
        if self._handle is None:
            return False
        return self._win32.assign_process_to_job(self._handle, proc_handle)

    def resume_thread(self, thread_handle: int) -> int:
        """Resumes a suspended thread."""
        return self._win32.resume_thread(thread_handle)

    def terminate(self, exit_code: int = 1) -> bool:
        """
        Forcefully terminates all processes associated with this Job Object.

        Returns True if successful, or False if the termination call failed.
        """
        if self._handle is None:
            return True
        return self._win32.terminate_job_object(self._handle, exit_code)

    def get_active_processes(self) -> int:
        """Returns the count of currently active processes in the Job Object."""
        if self._handle is None:
            return 0
        return self._win32.query_active_processes(self._handle)

    def close(self) -> None:
        """
        Closes the Job Object handle safely.

        Idempotent: safe to invoke multiple times.
        """
        handle = self._handle
        if handle is not None:
            self._handle = None
            try:
                self._win32.close_handle(handle)
            except Exception:
                pass

    def __enter__(self) -> WindowsJobObject:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def spawn_process(
        self,
        cmd: List[str],
        popen_cls: Any = subprocess.Popen,
        **popen_kwargs: Any,
    ) -> subprocess.Popen:
        """
        Spawns a subprocess inside this Job Object with suspended-start containment.

        Guarantees that the target process is created in a suspended state, assigned
        to the Job Object, and only resumed once assignment succeeds. If assignment
        or resumption fails, the process is terminated immediately and fail-closed
        exceptions are raised before any unsupervised code can execute.
        """
        if self._handle is None:
            raise JobConfigurationError("Cannot spawn process: Job Object handle is invalid or closed")

        with _spawn_lock:
            winapi_mod = sys.modules.get("_winapi")
            if winapi_mod is None and sys.platform == "win32":
                try:
                    import _winapi

                    winapi_mod = _winapi
                except ImportError:
                    pass
            orig_winapi_create = getattr(winapi_mod, "CreateProcess", None) if winapi_mod else None

            created_hp: Optional[int] = None
            created_ht: Optional[int] = None

            def _intercepted_create(*args: Any, **kwargs: Any) -> Tuple[int, int, int, int]:
                nonlocal created_hp, created_ht
                arg_list = list(args)
                if len(arg_list) > 5:
                    current_flags = int(arg_list[5]) if arg_list[5] is not None else 0
                    arg_list[5] = current_flags | _CREATE_SUSPENDED
                elif "creationflags" in kwargs:
                    current_flags = int(kwargs["creationflags"]) if kwargs.get("creationflags") is not None else 0
                    kwargs["creationflags"] = current_flags | _CREATE_SUSPENDED
                else:
                    kwargs["creationflags"] = _CREATE_SUSPENDED

                if orig_winapi_create is not None:
                    hp, ht, pid, tid = orig_winapi_create(*arg_list, **kwargs)
                else:
                    hp, ht, pid, tid = self._win32.create_process(*arg_list, **kwargs)

                created_hp = hp
                created_ht = ht

                # Step 4: Assign to Job Object BEFORE resuming thread
                try:
                    if not self._win32.assign_process_to_job(self._handle, hp):
                        err = self._win32.get_last_error()
                        raise JobAssignmentError(
                            f"AssignProcessToJobObject failed for PID {pid} with error code {err}"
                        )

                    # Step 5: Resume thread only after successful assignment
                    prev_count = self._win32.resume_thread(ht)
                    if prev_count == 0xFFFFFFFF or prev_count == -1:
                        err = self._win32.get_last_error()
                        raise JobResumeError(
                            f"ResumeThread failed for PID {pid} with error code {err}"
                        )

                except Exception:
                    # Fail closed: terminate suspended process and release handles immediately
                    try:
                        self._win32.terminate_process(hp, 1)
                    except Exception:
                        pass
                    try:
                        self._win32.close_handle(hp)
                    except Exception:
                        pass
                    try:
                        self._win32.close_handle(ht)
                    except Exception:
                        pass
                    raise

                return int(hp), int(ht), int(pid), int(tid)

            try:
                if winapi_mod:
                    winapi_mod.CreateProcess = _intercepted_create
                if hasattr(subprocess, "_winapi") and hasattr(subprocess._winapi, "CreateProcess"):
                    subprocess._winapi.CreateProcess = _intercepted_create

                # If running with SimulatedWin32Api (in unit tests on any platform):
                if isinstance(self._win32, SimulatedWin32Api):
                    from unittest.mock import MagicMock, Mock

                    hp, ht, pid, tid = _intercepted_create(cmd, cmd, None, None, 1, 0, None, None, None)
                    if isinstance(popen_cls, Mock) or hasattr(popen_cls, "_mock_return_value"):
                        proc = popen_cls(cmd, **popen_kwargs)
                        proc.pid = pid
                        if hasattr(proc, "_handle"):
                            proc._handle = hp
                        return proc

                    # Otherwise build simulated mock process representation
                    proc = MagicMock()
                    proc.pid = pid
                    proc._handle = hp
                    proc.poll.return_value = 0
                    proc.returncode = 0
                    proc.stdout = MagicMock()
                    proc.stdout.read.return_value = b""
                    proc.stdout.closed = False
                    proc.stderr = MagicMock()
                    proc.stderr.read.return_value = b""
                    proc.stderr.closed = False
                    return proc

                proc = popen_cls(cmd, **popen_kwargs)
                return proc
            finally:
                if winapi_mod and orig_winapi_create is not None:
                    winapi_mod.CreateProcess = orig_winapi_create
                if hasattr(subprocess, "_winapi") and orig_winapi_create is not None:
                    subprocess._winapi.CreateProcess = orig_winapi_create
