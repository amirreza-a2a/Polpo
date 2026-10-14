# 03: Subprocess Supervisor, Environment Sanitization & Process Tree Cancellation

**What to build:** Implement `SubprocessSupervisor` in `infrastructure/pdf/subprocess_supervisor.py`. Enforces cross-platform subprocess lifecycle management: environment variable sanitization (stripping `QUARTO_*`, `TEXINPUTS`), bounded execution timeouts, memory-safe output collection, cooperative cancellation with full process tree termination, and Windows/POSIX hardening flags.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

## Scope

- Implement `SubprocessSupervisor` in `infrastructure/pdf/subprocess_supervisor.py`:
  - **Environment Sanitization:** Sanitizes process environment. Explicitly strips `QUARTO_PROJECT_DIR`, `QUARTO_PROFILE`, `QUARTO_DENO_EXTRA_OPTIONS`, and `TEXINPUTS` overrides. Retains system essentials (`PATH`, `SYSTEMROOT`, `TEMP`, `TMP`, `USER`, `HOME`).
  - **Process Execution:** Spawns child processes using `subprocess.Popen` with safe argument lists (no `shell=True`).
  - **Win32 Hardening:** On Windows, sets `creationflags = subprocess.CREATE_NO_WINDOW` (`0x08000000`).
  - **POSIX Hardening:** On POSIX, sets `start_new_session=True` so child processes can be terminated as a distinct process group.
  - **Bounded Output Streaming:** Collects stdout and stderr in background reader threads with a hard size cap (default 5 MB per stream) to prevent memory exhaustion from runaway TeX log generation.
  - **Timeout Supervision:** Enforces execution deadlines (default 60s for PDF render). On timeout expiry, aggressively kills the child process tree and raises `SubprocessTimeoutError`.
  - **Cooperative Cancellation:** Accepts an `is_cancelled: Callable[[], bool]` callback or cancellation event. When cancellation triggers, kills the process tree immediately and raises `SubprocessCancelledError`.
  - **Tree Termination:** On Windows, invokes `taskkill /F /T /PID <pid>` to terminate child XeLaTeX processes spawned by Quarto. On POSIX, sends `os.killpg(os.getpgid(p.pid), signal.SIGKILL)`.
  - **F-03 Enforced (Trust Boundary Disclosure):** Docstrings explicitly disclose that `-no-shell-escape` prevents shell process spawning via `\write18` but does not sandbox filesystem reads via TeX primitives.

## Non-Goals

- Constructing Quarto command lines or arguments (handled by `QuartoPdfAdapter` in Ticket 05).
- Parsing TeX compiler logs or error messages (handled by `TexLogSanitizer` in Ticket 05).

## Files Likely Impacted

- Create: `infrastructure/pdf/subprocess_supervisor.py`
- Create: `tests/unit/test_subprocess_supervisor.py`

## Public Interfaces / Contracts

```python
@dataclass(frozen=True)
class SubprocessResult:
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

    Security Trust Boundary:
    Child processes execute under ordinary desktop user privileges.
    -no-shell-escape blocks shell command execution (\write18) but does not provide
    OS filesystem read sandboxing for TeX primitives.
    """
    def execute(
        self,
        cmd: List[str],
        cwd: Path,
        timeout_seconds: float = 60.0,
        is_cancelled: Optional[Callable[[], bool]] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> SubprocessResult:
        """Executes a subprocess command with sanitization, timeouts, and cancellation."""
        ...
```

## Architectural Invariants

- **Desktop Concurrency (AGENTS.md Rule 12):** Execution must be non-blocking with respect to calling threads and support cooperative cancellation.
- **No Orphan Subprocesses:** If Quarto or XeLaTeX hangs, times out, or is cancelled, all descendant processes MUST be reliably killed.
- **Memory Safety:** Streaming stdout/stderr must never cause unbounded RAM usage.

## Acceptance Criteria

- [ ] `SubprocessSupervisor` sanitizes inherited environment, removing `QUARTO_*` and `TEXINPUTS` variables.
- [ ] On Windows, child processes are spawned with `CREATE_NO_WINDOW`.
- [ ] On POSIX, child processes run in a separate process group (`start_new_session=True`).
- [ ] Output collection is capped at 5 MB; excessive output truncates safely without failing the stream.
- [ ] Timed-out execution cleanly kills the entire process tree (`taskkill` on Windows, `killpg` on POSIX) and sets `timed_out=True`.
- [ ] Cancellation signal triggers immediate process tree termination and sets `cancelled=True`.
- [ ] Return code, stdout, and stderr are captured accurately for clean exits.
- [ ] **F-03 Enforced:** Module and class docstrings document the OS privilege model and TeX filesystem read limitation.
- [ ] All unit tests pass in `tests/unit/test_subprocess_supervisor.py`.

## Verification / Tests Required

- `test_supervisor_executes_simple_command()`: echoes output, captures returncode 0.
- `test_supervisor_sanitizes_environment()`: verifies child environment does not contain `QUARTO_*` or `TEXINPUTS`.
- `test_supervisor_enforces_timeout()`: spawns a sleeping subprocess; asserts tree kill and `timed_out=True`.
- `test_supervisor_enforces_cancellation()`: triggers cancellation callback; asserts tree kill and `cancelled=True`.
- `test_supervisor_bounds_memory_usage()`: streams large output volume; verifies stdout truncation without OOM.

## Commit Boundary

Single commit: `feat(export): implement subprocess supervisor with environment sanitization and tree cancellation`
