# ADR-002: MathJax Process Supervisor Reliability & Concurrency Contract

- **Status:** Accepted
- **Date:** 2026-09-30
- **Scope:** Headless MathJax Subprocess Supervision (`infrastructure/math/mathjax_supervisor.py`, `infrastructure/math/mathjax_client.py`, `interfaces/desktop/composition.py`)
- **Authoritative Baseline:** Commit `18ae2b6` (Post-P02 Manual Region Publication & TICK-EXP-4 Document Export)
- **Problem Register References:** P07 (Indefinite RPC Hang), P10 (Restart Accounting Invariant), P12 (Overly Broad Cleanup Exception Swallowing), P08 (Silent Math Failure Fallback Dependencies)

---

## 1. Context

PolpoT is an embedded, desktop-first, local-first application. To render mathematical formulas (both display `$$...$$` and inline `$..$`), the application executes a dedicated headless Node.js daemon (`resources/mathjax/mathjax_worker.js`) running pinned `mathjax-full@3.2.2` under Node 22 LTS. Communication travels over line-delimited JSON-RPC 2.0 via standard I/O pipes (`stdin`, `stdout`, `stderr`).

In production:
1. **Desktop Concurrency:** MathJax rendering is never invoked synchronously on the Qt GUI thread. It is triggered by background threads managed by `MarkdownViewerController` (`MarkdownViewerWorker` thread pool) and `MarkdownEditorController` (`MarkdownEditorWorker`).
2. **QML Presentation:** The QML scene graph accesses pre-rendered SVGs exclusively through `MathImageProvider`, which performs fast in-memory cache lookups against `MathSvgCache`. It never directly invokes the supervisor or blocks the UI thread.
3. **The P07 Defect:** `MathJaxProcessSupervisor._call_rpc_locked()` previously performed an unbuffered, blocking `proc.stdout.readline()` while holding `self._lock` with zero timeout. If the Node worker hangs (e.g. an infinite loop or deadlock in JavaScript), the RPC call hangs forever, the supervisor lock remains permanently held, subsequent render calls stall, and application shutdown deadlocks on exit.
4. **The P10 Defect:** The restart rate limiter (`MAX_RESTARTS_PER_MINUTE = 3`) mistakenly recorded initial worker startup in `self._restart_timestamps`, burning 1 restart slot on clean boot and prematurely tripping the circuit breaker after only 2 genuine restarts.
5. **The P12 Defect:** Process termination, pipe closing, and stderr draining made heavy use of blanket `except Exception: pass`, concealing descriptor leaks, permission errors, and diagnostic stderr output.

---

## 2. Problem Statement

How must `MathJaxProcessSupervisor` be redesigned to guarantee:
1. Bounded RPC execution with deterministic timeout detection across POSIX and Windows?
2. Clean separation between worker cold startup and formula rendering timeouts?
3. Lock-free, non-blocking application shutdown that never deadlocks the desktop GUI thread?
4. Prevention of "poison pill" restart budget exhaustion during rapid live-typing in the Markdown editor?
5. Structured, granular error typing enabling deterministic fallback to readable TeX under P08?
6. Safe process lifecycle management with zero orphaned Node processes or zombie child handles?

---

## 3. Settled Architectural Decisions

### D01 — Per-Request Timeout Configuration & Validation
- **Decision:** The per-request timeout is configured via constructor parameter `request_timeout_seconds: float = 5.0`, defaulting to the environment variable `POLPO_MATHJAX_REQUEST_TIMEOUT` if set, and falling back to `5.0` seconds.
- **Validation Invariant:** If `POLPO_MATHJAX_REQUEST_TIMEOUT` is set to an empty string, non-numeric value, or a float $\le 0.0$, the supervisor must log a warning and fall back to `5.0` seconds. Invalid environment variables must **never** crash supervisor initialization.
- **Architectural Boundary:** Timeout configuration is an infrastructure concern and must not be persisted in core `AppSettings` or SQLite tables (Clean Architecture inward dependency rule).
- **Scope:** The request timeout applies strictly to an individual in-flight RPC roundtrip, completely decoupled from process startup or cold launch.

### D02 — Worker Startup / Cold-Start Timeout & Health Handshake
- **Decision:** Process startup is governed by a dedicated `startup_timeout_seconds: float = 15.0` (configurable via constructor and validated `POLPO_MATHJAX_STARTUP_TIMEOUT` with identical fallback/warning semantics).
- **Health Handshake:** Immediately after spawning the Node worker process via `subprocess.Popen`, the supervisor performs a synchronous readiness handshake by sending the JSON-RPC `"ping"` request. The worker script (`resources/mathjax/mathjax_worker.js`) already implements `"ping"` and responds with `"pong"`.
- **Isolation of Failure Domains:** Cold V8 initialization and parsing of `mathjax-full` modules (which may take 1–3s on slow Windows CI runners) are cleanly separated from formula rendering. A slow initial launch will never trigger a false-positive request timeout.
- **Handshake Failure:** If the worker process exits prematurely, fails to answer `"ping"`, or exceeds `startup_timeout_seconds`, it is immediately killed, reaped, and a structured `MathWorkerStartupError` is raised.

### D03 — Hung Process Termination & Kill Escalation Contract
- **Decision:** When an RPC or startup handshake times out, the supervisor terminates the child worker process using a two-phase cross-platform escalation protocol:
  1. `proc.terminate()` is invoked (`SIGTERM` on POSIX; Win32 `TerminateProcess` on Windows).
  2. Bounded grace wait: up to `0.5s` via `proc.wait(timeout=0.5)`.
  3. Escalation: if `proc.poll() is None`, invoke `proc.kill()` (`SIGKILL` on POSIX; Win32 `TerminateProcess` on Windows) followed by a bounded wait `proc.wait(timeout=0.5)` to reap the process exit status and prevent POSIX `<defunct>` zombies.
  4. Stream closure: explicitly close `stdin`, `stdout`, and `stderr`, catching narrow `(OSError, ValueError)`.
  5. State clearance: set `self._process = None`.
- **Windows vs POSIX Semantics:** On Windows, `terminate()` and `kill()` both call `TerminateProcess`. The 0.5s grace step provides real `SIGTERM` cleanup on POSIX; on Windows it ensures handle release and binary unlock before any subsequent respawn.
- **Direct Binary Execution Guard:** `resolve_node_executable()` must resolve to the direct binary executable (`node` on POSIX, `node.exe` on Windows). On Windows, shell shims (`.cmd`, `.bat`, `.ps1`) are explicitly rejected to prevent `cmd.exe` wrapper processes from orphaning child Node workers upon `TerminateProcess`. Launch always uses `shell=False`.
- **Non-Blocking Stderr Draining (P12):** Stderr is drained continuously into a bounded in-memory buffer (max 16 KB) by a daemon thread, or read strictly after `proc.poll() is not None` (reaped exit status). Stderr is never read with a blocking call while the process is running or hung. Blanket `except Exception: pass` is replaced with narrow `(OSError, ValueError)` handling.
- **Diagnostics:** Every forced termination is logged at `logging.WARNING` level with reason (`REQUEST_TIMEOUT`, `STARTUP_TIMEOUT`, `SHUTDOWN`), process PID, and escalation status.

### D04 — Cross-Platform RPC Timeout Mechanism & Lock Decoupling
- **Decision:** Because anonymous Windows pipes do not support `select.select()` and Python's `readline()` blocks in the C runtime, timeout detection is implemented using a **dedicated companion reader daemon thread per process** paired with an unbounded `queue.Queue()`.
- **Timeout Implementation:**
  - For each spawned worker generation, a daemon thread reads lines from `proc.stdout` and pushes them into the process-specific `queue.Queue()`.
  - The caller writes the request to `stdin` and waits via `queue.get(timeout=self._request_timeout)`.
  - On timeout: `queue.get()` raises `queue.Empty`. The caller thread initiates two-phase termination (D03) and raises `MathRenderTimeoutError`.
- **Write-Path Timeout & Blocked Write Protection:**
  - Windows pipe buffers are ~4 KB, while TeX payloads can reach 16 KB. If a worker stops reading `stdin`, `stdin.write()` or `flush()` could block.
  - A watchdog timer is armed **before** writing to `stdin`. If writing or flushing blocks past the timeout, the watchdog terminates/kills the process from outside the lock. This breaks the pipe, immediately unblocking the write thread with `BrokenPipeError` or `OSError`.
- **Generation Token & Stale Response Safety:**
  - Each spawned worker process receives a monotonically increasing generation token (`self._process_generation += 1`) and its own response queue. A late response from a previously killed worker can never be read by a subsequent request.
  - Responses are verified against request `id`. Mismatched IDs are dropped with a warning.
- **Sentinels & Fast Wake-ups:**
  - The reader thread pushes an `_EofSentinel` when `stdout` closes (worker exit/crash).
  - `shutdown()` pushes an `_AbortSentinel` into the queue, waking waiters instantly without waiting out the timeout.
  - The queue is unbounded (`maxsize=0`) so the reader thread never blocks on `put()`.
- **Waiters Behind RPC Lock:** Threads acquiring the RPC lock immediately re-check `self._is_shutdown` and the circuit breaker state, failing fast in $O(1)$ if the supervisor is down or tripped.
- **Daemon Threads:** Reader and drain threads are created with `daemon=True` and joined during shutdown with a bounded timeout (`0.2s`).

### D05 — In-Flight Request Retry Policy & Circuit Breaker Budget
- **Decision:** **Fail-Fast with Zero In-Flight Retries.** When a request times out or crashes the worker, the in-flight request fails immediately. It is **never** automatically retried on a new worker.
- **Poison-Pill Mitigation & Negative Memo:**
  - Automatically retrying a pathological formula (e.g. infinite macro expansion `\def\a{\b\b}...`) would cause an immediate second hang (5s + 15s cold start + 5s = 25s delay) and burn 2 of the 3 allowed restart slots on a single formula.
  - **Negative Failure Memo:** A session-scoped LRU cache bounded to 1,000 entries (keyed by `MathRenderRequest.compute_hash()`) is maintained in `infrastructure/math` (consumed by `MathJaxClient`).
  - When a formula causes a `MathRenderTimeoutError` or `MathWorkerCrashedError`, its hash is recorded in the negative memo. Subsequent renders of that exact formula fail immediately in $O(1)$ without touching the worker.
  - **Live-Typing Burst Protection (P08 Dependency):** In `MarkdownEditorPane`, live preview debounces typing by 250ms. If an incomplete/pathological formula variant times out during editing, subsequent preview renders for that document block are throttled, and if 2 consecutive timeouts occur within a document editing session, math preview degrades to raw-TeX fallback until typing quiescence (2.0s) or manual reload.
- **Restart Budget Accounting (P10 Resolution):**
  - **Invariant:** `initial start != restart`. Clean initial process startup records **0** restarts.
  - **Budget-consuming events:** Exactly three events consume a restart slot: (1) request timeout, (2) unexpected worker crash / stdout EOF, and (3) startup handshake failure.
  - **Non-budget-consuming events:** TeX syntax errors, client-side buffer limit rejections, negative-memo hits, and shutdown kills do **not** consume budget.
- **Explicit 3-State Circuit Breaker:**
  - `CLOSED`: Normal operation; failure count in rolling 60s window $< 3$.
  - `OPEN`: Failure count $\ge 3$ within 60s. All calls fail fast in $O(1)$ with `MathCircuitBreakerOpenError`. A mandatory cooldown period (`COOLDOWN_SECONDS = 30.0`) begins.
  - `HALF_OPEN`: Entered after cooldown elapses and window slides. Allows exactly **one probe request**. If the probe succeeds, the breaker resets to `CLOSED`. If the probe fails, the breaker immediately re-opens for another 60.0s cooldown.
- **Worst-Case Latency Bound:** For a document with $N$ distinct pathological formulas, worst-case latency is bounded by $3 \times (5\text{s} + 1.5\text{s}) \approx 20\text{s}$ (at most $60\text{s}$ under severe cold disk conditions), after which the circuit breaker trips `OPEN` and all remaining formulas fail in $<1\text{ms}$.

### D06 — Shutdown Semantics & Application Exit Coordination
- **Decision:** `MathJaxProcessSupervisor.shutdown()` is non-blocking, idempotent, and executed prior to controller executor teardown in `DesktopAppContainer`.
- **Ordered Teardown Sequence in `DesktopAppContainer.shutdown()`:**
  1. `mathjax_supervisor.begin_shutdown()` (or `shutdown()`) is invoked **first**. It sets `self._is_shutdown = True`, pushes `_AbortSentinel` to response queues, and terminates/kills `self._process`.
  2. Any in-flight RPC in `MarkdownViewerWorker` unblocks immediately with `MathSupervisorShutdownError`.
  3. `markdown_viewer_controller.shutdown()` and `export_controller.shutdown()` shut down their thread pools without waiting out RPC timeouts.
  4. Finalize scheduler, runtime, and supervisor resource handles.
- **Spawn/Shutdown Race Prevention:**
  - `_ensure_process_locked()` checks `self._is_shutdown` under lock before launching `Popen`, and re-checks immediately after `Popen` returns. If shutdown occurred while `Popen` was running, the newly created process is immediately killed, reaped, and `MathSupervisorShutdownError` is raised. No orphan Node process is ever leaked.
- **Parent-Death Safety (Verified in Code):**
  - [`resources/mathjax/mathjax_worker.js#L257-L267`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/resources/mathjax/mathjax_worker.js#L257-L267) listens to `rl.on('close')` and `process.on('SIGTERM')` / `SIGINT`, executing `process.exit(0)`. If the parent PolpoT application is terminated or killed (`SIGKILL`), the OS closes the pipe, stdin emits EOF, and Node exits cleanly.
- **Worst-Case Shutdown Latency:** Terminate grace ($0.5\text{s}$) + kill reap ($0.5\text{s}$) + thread joins ($2 \times 0.2\text{s}$) $\approx 1.4\text{s}$ maximum on the GUI thread during `aboutToQuit`, preventing OS force-kill dialogues.

### D07 — Structured Exception Hierarchy
All errors inherit from `MathRenderError`:

```text
MathRenderError (base application port exception)
├── MathWorkerStartupError         # Handshake timeout, bad node path, missing worker script
├── MathRenderTimeoutError         # In-flight RPC exceeded request_timeout_seconds
├── MathWorkerCrashedError         # Worker died unexpectedly (EOF / BrokenPipeError)
├── MathCircuitBreakerOpenError    # 3 failures in 60s; supervisor in circuit-breaker lockout
├── MathSupervisorShutdownError    # Call attempted after supervisor was shut down
├── MathBufferLimitExceededError   # TeX > 16 KB or SVG > 512 KB
└── MathSyntaxError                # Worker reported LaTeX syntax/macro error
```

**P08 Fallback Behavior Classification:**
- **Permanent for formula:** `MathSyntaxError`, `MathBufferLimitExceededError`, `MathRenderTimeoutError`. The formula will not render; P08 displays formatted raw TeX with an error badge.
- **Temporary / Retryable:** `MathCircuitBreakerOpenError` (retryable after cooldown), `MathWorkerCrashedError` (transparent retry on next distinct formula).
- **Silent Teardown:** `MathSupervisorShutdownError` is never logged as a user-facing error and is ignored by presentation controllers during application exit.

### D08 — Deterministic Test Fixtures & CI Strategy
- **Fixtures in `tests/fixtures/`:**
  1. `hung_worker_request.py`: accepts request line, then sleeps indefinitely.
  2. `hung_worker_startup.py`: never responds to `"ping"` handshake.
  3. `crash_worker_mid_request.py`: abruptly closes stdout and exits on receiving request.
  4. `late_worker_response.py`: sleeps beyond timeout, then outputs JSON-RPC response.
  5. `flood_stderr_worker.py`: emits megabytes of stderr logging while serving math.
  6. `sigterm_ignoring_worker.py`: ignores `SIGTERM` to exercise `SIGKILL` escalation on POSIX.
- **Timing & Assertion Rules:**
  - All test assertions must verify state transitions, exception types, and absence of deadlocks using generous bounds (e.g. `assert elapsed < 3.0s`), avoiding brittle millisecond assertions that flake on CI.
  - Dedicated tests cover: (a) spawn/shutdown race, (b) poison-pill negative memo hits, (c) initial startup not consuming restart budget, (d) 3-state circuit breaker probe and cooldown, and (e) non-blocking container shutdown.

---

## 4. Problem Register Reconciliation

| Problem ID | Category | Status After ADR-002 | Next Action |
|---|---|---|---|
| **P07** | MathJax reliability | **INVESTIGATED & SETTLED (ADR-002)** | Unblocked for implementation tickets (TICK-P07) |
| **P10** | Process supervision | **INVESTIGATED & SETTLED (ADR-002)** | Folded into supervisor reliability implementation |
| **P12** | Cleanup exception handling | **INVESTIGATED & SETTLED (ADR-002)** | Folded into supervisor reliability implementation |
| **P08** | Silent math failure | **BLOCKED ON P07 (DEPENDENCY MAPPED)** | Will consume ADR-002 exception hierarchy and negative memo |
| **P02** | Manual region publication | **DONE** | Merged in PRs #31, #32, #33, #34 (Commits `7f8317e`, `c18fc72`, `d4d19de`, `222624c`) |
