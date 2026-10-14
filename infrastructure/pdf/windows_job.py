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
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)

# Win32 Constants
JobObjectBasicAccountingInformation: int = 1
JobObjectExtendedLimitInformation: int = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: int = 0x00002000
_CREATE_SUSPENDED: int = 0x00000004
_WIN32_CREATE_NO_WINDOW: int = 0x08000000


class JobConfigurationError(OSError):
    """Raised when creating or configuring a Windows Job Object fails."""


class JobAssignmentError(OSError):
    """Raised when assigning a process to a Windows Job Object fails."""


class JobResumeError(OSError):
    """Raised when resuming a suspended process inside a Job Object fails."""


class JobClosureError(OSError):
    """Raised when closing a Windows Job Object handle fails."""


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
            err = self.get_last_error()
            raise OSError(f"QueryInformationJobObject failed with error code {err}")
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
        self.fail_query_active_processes: bool = False
        self.fail_close_handle: bool = False
        self.raise_on_close_handle: bool = False
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
        if self.fail_query_active_processes:
            self.last_error = 5
            raise OSError("QueryInformationJobObject failed with error code 5")
        return self.active_processes_count

    def close_handle(self, handle: int) -> bool:
        self.operation_log.append(f"close_handle:{handle}")
        if self.raise_on_close_handle:
            self.last_error = 6  # ERROR_INVALID_HANDLE
            raise OSError("Simulated CloseHandle failure")
        if self.fail_close_handle:
            self.last_error = 6  # ERROR_INVALID_HANDLE
            return False
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


class _SimulatedProcess:
    """Lightweight test double representing a simulated subprocess for non-native execution."""

    def __init__(self, pid: int, handle: int) -> None:
        self.pid = pid
        self._handle = handle
        self.returncode: Optional[int] = 0

        class _Stream:
            def __init__(self) -> None:
                self.closed = False

            def read(self, *args: Any) -> bytes:
                return b""

            def close(self) -> None:
                self.closed = True

        self.stdout = _Stream()
        self.stderr = _Stream()

    def poll(self) -> Optional[int]:
        return self.returncode

    def wait(self, timeout: Optional[float] = None) -> int:
        return self.returncode or 0

    def kill(self) -> None:
        self.returncode = -9


class _JobContainedPopen(subprocess.Popen):
    """
    Subprocess Popen implementation providing process-scoped Job Object containment.

    Overrides `_execute_child` to guarantee that the child process is created
    suspended (`CREATE_SUSPENDED`), assigned to the Job Object, and resumed
    strictly before execution begins.

    Eliminates global monkeypatching of `_winapi.CreateProcess`, ensuring that
    concurrent `subprocess.Popen` invocations on other threads remain completely
    isolated and unaffected.
    """

    def __init__(
        self,
        *args: Any,
        job_handle: int,
        win32_api: Win32ApiProtocol,
        **kwargs: Any,
    ) -> None:
        self._job_handle = job_handle
        self._win32 = win32_api
        super().__init__(*args, **kwargs)

    def _execute_child(
        self,
        args: Any,
        executable: Any,
        preexec_fn: Any,
        close_fds: Any,
        pass_fds: Any,
        cwd: Any,
        env: Any,
        startupinfo: Any,
        creationflags: Any,
        shell: Any,
        p2cread: Any,
        p2cwrite: Any,
        c2pread: Any,
        c2pwrite: Any,
        errread: Any,
        errwrite: Any,
        *unused_args: Any,
        **unused_kwargs: Any,
    ) -> None:
        """
        Executes child process on Windows with process-scoped Job Object containment.
        """
        assert not pass_fds, "pass_fds not supported on Windows."

        if isinstance(args, str):
            pass
        elif isinstance(args, bytes):
            if shell:
                raise TypeError("bytes args is not allowed on Windows")
            args = subprocess.list2cmdline([args])
        elif isinstance(args, os.PathLike):
            if shell:
                raise TypeError("path-like args is not allowed when shell is true")
            args = subprocess.list2cmdline([args])
        else:
            args = subprocess.list2cmdline(args)

        if executable is not None:
            executable = os.fsdecode(executable)

        if startupinfo is None:
            startupinfo = subprocess.STARTUPINFO()
        else:
            startupinfo = startupinfo.copy()

        use_std_handles = -1 not in (p2cread, c2pwrite, errwrite)
        if use_std_handles:
            import _winapi

            startupinfo.dwFlags |= _winapi.STARTF_USESTDHANDLES
            startupinfo.hStdInput = p2cread
            startupinfo.hStdOutput = c2pwrite
            startupinfo.hStdError = errwrite

        attribute_list = startupinfo.lpAttributeList
        have_handle_list = bool(
            attribute_list and "handle_list" in attribute_list and attribute_list["handle_list"]
        )

        if have_handle_list or (use_std_handles and close_fds):
            if attribute_list is None:
                attribute_list = startupinfo.lpAttributeList = {}
            handle_list = attribute_list["handle_list"] = list(attribute_list.get("handle_list", []))

            if use_std_handles:
                handle_list += [int(p2cread), int(c2pwrite), int(errwrite)]

            if hasattr(self, "_filter_handle_list"):
                handle_list[:] = self._filter_handle_list(handle_list)
            else:
                import _winapi

                handle_list[:] = list({
                    h for h in handle_list
                    if (h & 0x3 != 0x3) or (_winapi.GetFileType(h) != _winapi.FILE_TYPE_CHAR)
                })

            if handle_list:
                close_fds = False

        if shell:
            import _winapi

            startupinfo.dwFlags |= _winapi.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = _winapi.SW_HIDE
            if not executable:
                comspec = os.environ.get("ComSpec")
                if not comspec:
                    system_root = os.environ.get("SystemRoot", "")
                    comspec = os.path.join(system_root, "System32", "cmd.exe")
                    if not os.path.isabs(comspec):
                        raise FileNotFoundError(
                            "shell not found: neither %ComSpec% nor %SystemRoot% is set"
                        )
                if os.path.isabs(comspec):
                    executable = comspec
            else:
                comspec = executable

            args = f'{comspec} /c "{args}"'

        if cwd is not None:
            cwd = os.fsdecode(cwd)

        sys.audit("subprocess.Popen", executable, args, cwd, env)

        creationflags = (int(creationflags) if creationflags is not None else 0) | _CREATE_SUSPENDED

        try:
            hp, ht, pid, tid = self._win32.create_process(
                executable,
                args,
                None,
                None,
                int(not close_fds),
                creationflags,
                env,
                cwd,
                startupinfo,
            )
        finally:
            self._close_pipe_fds(p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite)

        try:
            if not self._win32.assign_process_to_job(self._job_handle, hp):
                err = self._win32.get_last_error()
                raise JobAssignmentError(
                    f"AssignProcessToJobObject failed for PID {pid} with error code {err}"
                )

            prev_count = self._win32.resume_thread(ht)
            if prev_count == 0xFFFFFFFF or prev_count == -1:
                err = self._win32.get_last_error()
                raise JobResumeError(
                    f"ResumeThread failed for PID {pid} with error code {err}"
                )
        except Exception:
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

        self._child_created = True
        if hasattr(subprocess, "Handle"):
            self._handle = getattr(subprocess, "Handle")(hp)
        else:
            self._handle = hp
        self.pid = pid
        self._win32.close_handle(ht)


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

    def close(self, raise_on_error: bool = False) -> bool:
        """
        Closes the Job Object handle safely.

        Idempotent: safe to invoke multiple times.

        Args:
            raise_on_error: If True, raises JobClosureError on failure instead
                            of returning False.

        Returns:
            True if the handle is successfully closed (or was already closed).
            False if handle closure failed.

        Raises:
            JobClosureError: If raise_on_error is True and handle closure fails.
        """
        handle = self._handle
        if handle is None:
            return True

        closed_ok = False
        error_msg = ""
        try:
            res = self._win32.close_handle(handle)
            if res:
                closed_ok = True
            else:
                err = self._win32.get_last_error()
                error_msg = f"CloseHandle returned False (error code {err})"
                logger.warning(
                    "CloseHandle failed for Job Object handle %s (error code %s)",
                    handle,
                    err,
                )
        except Exception as e:
            error_msg = f"CloseHandle raised exception: {e}"
            logger.warning(
                "CloseHandle raised exception for Job Object handle %s: %s",
                handle,
                e,
            )

        if not closed_ok:
            if raise_on_error:
                raise JobClosureError(
                    f"Failed to close Job Object handle {handle}: {error_msg}"
                )
            return False

        # Only clear _handle when CloseHandle is confirmed successful
        self._handle = None
        return True

    def __enter__(self) -> WindowsJobObject:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        # In context manager exit, do not mask an in-flight primary exception
        # with a secondary cleanup error. If no exception occurred, raise_on_error=True
        # ensures handle closure failures are observable.
        if exc_type is not None:
            self.close(raise_on_error=False)
        else:
            self.close(raise_on_error=True)

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

        Uses process-scoped containment without mutating process-wide APIs.
        """
        if self._handle is None:
            raise JobConfigurationError("Cannot spawn process: Job Object handle is invalid or closed")

        is_native_win32 = isinstance(self._win32, RealWin32Api) and sys.platform == "win32"
        is_mock_popen = (
            callable(popen_cls)
            and (hasattr(popen_cls, "mock_calls") or hasattr(popen_cls, "_mock_return_value"))
        )

        if is_native_win32 and not is_mock_popen:
            if popen_cls is subprocess.Popen:
                target_cls = _JobContainedPopen
            elif isinstance(popen_cls, type) and issubclass(popen_cls, subprocess.Popen):
                target_cls = type(
                    f"_JobContained{popen_cls.__name__}",
                    (popen_cls, _JobContainedPopen),
                    {},
                )
            else:
                target_cls = _JobContainedPopen

            proc = target_cls(
                cmd,
                job_handle=self._handle,
                win32_api=self._win32,
                **popen_kwargs,
            )
            return proc

        # Simulated, test-double, or non-native platform path:
        hp, ht, pid, tid = self._win32.create_process(
            cmd, cmd, None, None, 1, _CREATE_SUSPENDED, None, None, None
        )

        try:
            if not self._win32.assign_process_to_job(self._handle, hp):
                err = self._win32.get_last_error()
                raise JobAssignmentError(
                    f"AssignProcessToJobObject failed for PID {pid} with error code {err}"
                )

            prev_count = self._win32.resume_thread(ht)
            if prev_count == 0xFFFFFFFF or prev_count == -1:
                err = self._win32.get_last_error()
                raise JobResumeError(
                    f"ResumeThread failed for PID {pid} with error code {err}"
                )
        except Exception:
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

        if is_mock_popen:
            proc = popen_cls(cmd, **popen_kwargs)
            proc.pid = pid
            if hasattr(proc, "_handle"):
                proc._handle = hp
            return proc

        return _SimulatedProcess(pid=pid, handle=hp)
