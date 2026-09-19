# Phase 8D Implementation Plan: Concurrent Desktop Runtime & Execution Engine (Final Remediation)

**Authoritative Roadmap Reference:** `phase_8_desktop_architecture_plan.md`  
**Standing Architectural Rules:** `AGENTS.md`  
**Current Baseline Commit:** `a95649d` (`feat(phase-8c): implement os keyring resolver and encrypted fallback store`)  
**Baseline Test Suite Status:** `224 passed, 0 failed`  
**Target Architectural Milestone:** Phase 8D — Concurrent Desktop Runtime, Execution Engine & Service Reconciliation  

---

## 1. Executive Summary & Objective

Phase 8A reconciled domain entities and application ports. Phase 8B established the SQLite WAL persistence layer with atomic claiming and migration runners. Phase 8C established OS Keyring and encrypted file credential security with zero raw secret persistence.

**Phase 8D** bridges these foundations into a fully operational, concurrent, in-process desktop execution engine.

### Core Objectives:
1. **Thread-Safe In-Memory Rate Limiter (`ThreadSafeMemoryRateLimiter`):** Replace the legacy JSON-file rate limiter (`utils/rate_limiter.py`) with a high-performance in-memory sliding-window rate limiter using `time.monotonic()` and thread mutex locks.
2. **Desktop Application Services Reconciliation:** Reconcile all application services (`JobSubmissionService`, `JobExecutionService`, `JobRecoveryService`, `QuickConvertService`, `LocalSettingsService`, `ArtifactService`) to operate natively with desktop SQLite Unit-of-Work, atomic job claiming, cooperative cancellation, immutable source PDF ingestion, and pure transport-neutral application events.
3. **Transport-Neutral Application Event Bus (`InMemoryEventBus`):** Implement a thread-safe in-process event bus for publishing typed events with subscriber error isolation and lock-free callback dispatch.
4. **Desktop Composition Root (`DesktopAppContainer`):** Implement the canonical desktop composition root wiring SQLite persistence, OS Keyring security, local artifact storage, PyMuPDF document processing, memory rate limiting, AI execution, event dispatch, and application services into a cohesive dependency graph.
5. **Concurrent Desktop Job Runtime (`DesktopJobRuntime`):** Implement the in-process background worker execution engine that manages worker thread concurrency bounded by `AppSettings.max_concurrent_jobs`, claims pending jobs atomically, executes document processing pipelines asynchronously, handles cooperative cancellation, and recovers from worker-level failures safely without crashing the desktop process.

---

## 2. External Claim Verification & Configurable Rate Limits (`AGENTS.md` Rule 1)

In compliance with `AGENTS.md` Rule 1, external AI provider rate limits and quotas are not treated as immutable hardcoded constants. Provider rate limits (Requests Per Minute - RPM) vary significantly across subscription tiers, regions, and external API revisions:

- **Verification Status:** `UNVERIFIED — based on training data, may be outdated`.
- **Architectural Policy:**
  - Rate limits are **fully configurable** at runtime.
  - `ThreadSafeMemoryRateLimiter` accepts an optional `default_rpms: Optional[Dict[str, int]]` mapping.
  - If no custom RPM is provided, sensible defensive defaults are used (e.g. `google: 15`, `openai: 60`, `openrouter: 30`, `custom: 60`), but individual slot RPM overrides are fully supported.
  - No domain entity or database constraint hardcodes provider RPM rules.

---

## 3. Explicit Responsibility Boundaries

To prevent overlapping logic, race conditions, or duplicate claiming, the responsibility boundary between `DesktopJobRuntime` and `JobExecutionService` is strictly defined:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                             DesktopJobRuntime                               │
│  - Concurrency management (bounds workers by AppSettings.max_concurrent_jobs)│
│  - Dispatching & polling loop (decides WHEN workers run)                    │
│  - Job Claiming: executes uow.jobs.claim_next_pending() in a dedicated      │
│    BEGIN IMMEDIATE transaction                                              │
│  - Worker lifecycle & thread pool management (ThreadPoolExecutor)           │
│  - Catching unhandled worker exceptions & marking jobs FAILED               │
│  - Graceful shutdown, task draining, and runtime pause/resume               │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ passes claimed job_id
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            JobExecutionService                              │
│  - Executes EXACTLY ONE already-claimed job (execute_claimed_job(job_id))   │
│  - Document processing pipeline (page rendering, OCR, cropping, markdown)   │
│  - Observes cancel_requested checkpoints & transitions to CANCELLED         │
│  - Transitions state to PAUSED (on API exhaustion) or DONE (on completion)  │
│  - Publishes typed application events (Progress, Switch, Done, Failed)      │
│  - Does NOT poll or claim jobs independently in normal worker dispatch      │
└─────────────────────────────────────────────────────────────────────────────┘
```

| Concern | `DesktopJobRuntime` | `JobExecutionService` |
| :--- | :--- | :--- |
| **Who decides when workers run?** | **Yes** (manages worker pool) | No |
| **Who claims pending jobs?** | **Yes** (atomic `claim_next_pending`) | No (executes pre-claimed job) |
| **Who executes the pipeline?** | No (delegates to service) | **Yes** (`execute_claimed_job`) |
| **Who observes cancellation?** | Flags `cancel_requested=True` on stop | Checks checkpoint & transitions |
| **Who handles worker exceptions?** | Catches unhandled thread crashes | Handles domain / AI / I/O errors |
| **Who publishes events?** | Runtime lifecycle events | Pipeline progress & state events |

---

## 4. Application Event Bus Concurrency Semantics (`InMemoryEventBus`)

The `InMemoryEventBus` implements `IApplicationEventPublisher` with strict thread safety and lock-free callback dispatch:

### 4.1 Locking and Dispatch Rules:
1. **Thread-Safe Registration:** `subscribe(event_type, handler) -> SubscriptionHandle` and `unsubscribe(handle)` acquire a dedicated `threading.Lock()` to modify the internal `_subscribers: Dict[Type, List[Callable]]` registry.
2. **Lock-Free Callback Execution:** `publish(event)` acquires the registry lock **only long enough to take a shallow copy/snapshot** of matching handlers for `type(event)` and its base classes. The lock is released **before** invoking any subscriber callbacks.
   - *Rationale:* Prevents deadlocks if a subscriber callback invokes a service, subscribes/unsubscribes, or triggers another event.
3. **Subscriber Error Isolation:** Each callback is invoked inside an individual `try...except Exception as e:` block. If one subscriber raises an exception, the error is logged via `logger.exception()`, but it **never** halts event propagation to other subscribers or raises into the publishing worker.
4. **Deterministic Ordering:** Events published on a single worker thread are delivered to subscribers synchronously in publication order. Subscribers for the same event type are invoked in registration order.

---

## 5. Precise Transaction Boundaries & Non-Blocking Invariant

### 5.1 The Cardinal Concurrency Invariant
```text
NO AI network request
NO PDF rendering (PyMuPDF)
NO image processing / cropping
NO expensive filesystem operation
NO event subscriber callback execution
may occur inside an active SQLite write transaction.
```

### 5.2 Transaction Mapping per Service

```
Service / Component             Operation                   Transaction Boundary & Commit Point
─────────────────────────────────────────────────────────────────────────────────────────────────────────
JobSubmissionService            submit_job                  1. doc_processor.get_page_count() [NO DB]
                                                            2. storage.store_source_file() [DISK I/O, NO DB]
                                                            3. uow.create(): insert Job (PENDING,
                                                               file_path=final_artifact_uri), COMMIT [DB < 1ms]
                                                            4. event_bus.publish(JobStateChangedEvent) [NO DB]

DesktopJobRuntime               claim_and_dispatch          1. uow.create(): uow.jobs.claim_next_pending()
                                                               (BEGIN IMMEDIATE), COMMIT [DB < 1ms]
                                                            2. Dispatch job_id to ThreadPoolExecutor [NO DB]

JobExecutionService             execute_claimed_job         1. uow.create(): verify claimed Job status,
                                                               read prompt, API chain [DB < 1ms]
                                                            2. storage.retrieve(SOURCE_PDF) [DISK, NO DB]
                                                            3. PER PAGE LOOP:
                                                               a. check cancel_requested [DB read < 1ms]
                                                               b. render_page_to_jpeg() [CPU, NO DB]
                                                               c. check cancel_requested [DB read < 1ms]
                                                               d. ai_executor.execute_vision() [NET, NO DB]
                                                               e. extract_and_crop_images() [CPU, NO DB]
                                                               f. uow.create(): update processed_pages,
                                                                  api_switch_log, report usage, COMMIT [<1ms]
                                                               g. publish JobProgressEvent [NO DB]
                                                            4. FINALIZATION:
                                                               a. storage.store(OUTPUT_MD) [DISK, NO DB]
                                                               b. uow.create(): set status=DONE, save
                                                                  Pipeline2Job if auto, COMMIT [DB < 1ms]
                                                               c. publish JobStateChangedEvent &
                                                                  JobCompletedEvent [NO DB]

JobRecoveryService              reconcile_stale_jobs        1. uow.create(): uow.jobs.reconcile_stale_jobs(),
                                                               COMMIT [DB < 1ms]
                                                            2. publish JobStateChangedEvents [NO DB]

QuickConvertService             convert_image               1. uow.create(): read prompt, API slots [DB < 1ms]
                                                            2. ai_executor.execute_vision() [NET, NO DB]
                                                            3. uow.create(): report usage, COMMIT [DB < 1ms]
```

---

## 6. `JobSubmissionService` Failure Semantics & Invariants

To guarantee source document integrity and prevent incomplete jobs from being claimed:

```text
       ┌─────────────────────────────────────────────────────────────┐
       │ 1. Compute page count via doc_processor                     │ ──► Failure: raise DomainError
       └──────────────────────────────┬──────────────────────────────┘     (no DB records, no files)
                                      │
                                      ▼
       ┌─────────────────────────────────────────────────────────────┐
       │ 2. Generate artifact handle & write source PDF to           │ ──► Failure: raise DomainError
       │    storage under immutable path (e.g. artifacts/source_...  │     (no DB records created)
       └──────────────────────────────┬──────────────────────────────┘
                                      │ immutable artifact URI obtained
                                      ▼
       ┌─────────────────────────────────────────────────────────────┐
       │ 3. Open uow.create():                                       │ ──► Failure: storage.delete(artifact)
       │    Insert Job(file_path=artifact_uri, status=PENDING, ...)  │     raise DomainError
       │    COMMIT                                                   │
       └──────────────────────────────┬──────────────────────────────┘
                                      │ job committed with valid file_path
                                      ▼
       ┌─────────────────────────────────────────────────────────────┐
       │ 4. Emit JobStateChangedEvent(id, None, PENDING) & return DTO │
       └─────────────────────────────────────────────────────────────┘
```

**Invariant:** A committed `PENDING` job must always reference an existing, immutable source artifact on disk from the moment of its first commit.

---

## 7. Canonical Cancellation State Machine & API Ownership

### 7.1 API Ownership
`JobExecutionService.cancel_job(job_id: int) -> bool` is the canonical application entry point for cancellation.

### 7.2 State Transitions & Event Ordering

```
                      ┌───────────────────────────────────────┐
                      │              Job: PENDING             │
                      └──────────────────┬────────────────────┘
                                         │ cancel_job(id)
                                         ▼
                      ┌───────────────────────────────────────┐
                      │            Job: CANCELLED             │
                      │ 1. COMMIT uow                         │
                      │ 2. JobStateChangedEvent(PENDING->CANC)│
                      │ 3. JobCancelledEvent(id)              │
                      └───────────────────────────────────────┘
                                         ▲
                                         │ worker observes cancel_requested
                                         │ at nearest page/AI checkpoint
                      ┌──────────────────┴────────────────────┐
                      │           Job: PROCESSING             │
                      │       (cancel_requested = True)       │
                      └───────────────────────────────────────┘
```

### Cancellation Rules:
1. **Pending Jobs:** When `cancel_job(job_id)` is called on a `PENDING` job:
   - Sets status to `CANCELLED` in SQLite.
   - `uow.commit()`.
   - Emits `JobStateChangedEvent(job_id, PENDING, CANCELLED)`.
   - Emits `JobCancelledEvent(job_id)`.
2. **Processing Jobs:** When `cancel_job(job_id)` is called on a `PROCESSING` job:
   - Sets `cancel_requested = True` in SQLite (`uow.jobs.request_cancellation(job_id)`).
   - The active worker checks `cancel_requested` at 4 distinct checkpoints:
     - Checkpoint 1: Prior to page JPEG rendering.
     - Checkpoint 2: Prior to outbound AI API request.
     - Checkpoint 3: After receiving AI API response (before cropping/markdown storage).
     - Checkpoint 4: Prior to final markdown output commit.
   - When detected, worker transitions `PROCESSING → CANCELLED` in SQLite, commits, emits `JobStateChangedEvent(job_id, PROCESSING, CANCELLED)`, emits `JobCancelledEvent(job_id)`, and exits cleanly.
3. **In-Flight Network Requests:** Cancellation is cooperative; an already-issued outbound HTTP request will complete or timeout at the network socket layer, but the worker will discard the response at Checkpoint 3 and exit as `CANCELLED`.
4. **Terminal States:** Jobs in `DONE`, `FAILED`, `PAUSED`, or `CANCELLED` cannot be cancelled (raises `DomainError`).
5. **Mutual Exclusivity:** A job that is cancelled **never** emits `JobCompletedEvent`.

---

## 8. Canonical Event Ordering for All State Transitions

For every state transition, events are published **only after transaction commit** in strict deterministic order:

| Transition | Action & Transaction Boundary | Exact Event Emission Order |
| :--- | :--- | :--- |
| **Submission** | `uow.jobs.save(job)` (PENDING) $\rightarrow$ `COMMIT` | 1. `JobStateChangedEvent(id, None, PENDING)` |
| **Claiming** | `uow.jobs.claim_next_pending()` (PROCESSING) $\rightarrow$ `COMMIT` | 1. `JobStateChangedEvent(id, PENDING, PROCESSING)` |
| **Page Progress** | `uow.jobs.update_progress()` $\rightarrow$ `COMMIT` | 1. `JobProgressEvent(id, page, total, pct)` |
| **API Fallback** | `uow.jobs.update_progress()` $\rightarrow$ `COMMIT` | 1. `ApiSwitchEvent(id, old, new, reason, page)` |
| **Completion** | `uow.jobs.update_status(DONE)` $\rightarrow$ `COMMIT` | 1. `JobStateChangedEvent(id, PROCESSING, DONE)`<br>2. `JobCompletedEvent(id, output_uri)` |
| **Cancellation** | `uow.jobs.update_status(CANCELLED)` $\rightarrow$ `COMMIT` | 1. `JobStateChangedEvent(id, old_st, CANCELLED)`<br>2. `JobCancelledEvent(id)` |
| **Handled Failure** | `uow.jobs.update_status(FAILED)` $\rightarrow$ `COMMIT` | 1. `JobStateChangedEvent(id, PROCESSING, FAILED)`<br>2. `JobFailedEvent(id, error, is_retryable=False)` |
| **API Exhaustion** | `uow.jobs.update_status(PAUSED)` $\rightarrow$ `COMMIT` | 1. `JobStateChangedEvent(id, PROCESSING, PAUSED)`<br>2. `JobFailedEvent(id, error, is_retryable=True)` |

---

## 9. `DesktopJobRuntime` Logical Concurrency & Shutdown Semantics

### 9.1 Logical Concurrency Control (No Private Hack Resizing)
- `DesktopJobRuntime` uses a thread pool alongside a **logical worker-capacity accounting mechanism** (`threading.Condition` / `threading.Lock`):
  - Active worker count: `_active_workers: int`.
  - Target maximum: `AppSettings.max_concurrent_jobs`.
  - The dispatch loop checks `_active_workers < max_concurrent_jobs` before calling `uow.jobs.claim_next_pending()`.
  - When `AppSettings.max_concurrent_jobs` is modified by the user, the runtime immediately adjusts the capacity threshold without modifying private `ThreadPoolExecutor` internals.
  - **Invariant:** `active job executions <= AppSettings.max_concurrent_jobs` at all times.

### 9.2 Three Distinct Shutdown Operations
1. **`pause()` / `stop_accepting()`:** Dispatcher stops claiming new pending jobs from SQLite. Active workers continue executing their current jobs until completion or natural pause.
2. **`cancel_active_jobs()`:** Iterates over active worker job IDs and sets `cancel_requested = True` in SQLite to signal running workers to cooperatively cancel.
3. **`shutdown(wait=True, timeout=10.0)`:**
   - Stops the dispatcher thread.
   - Shuts down the `ThreadPoolExecutor`.
   - If `wait=True`, waits up to `timeout` seconds for active workers to exit.
   - If workers fail to finish within `timeout`, logs a warning and exits. Any incomplete jobs will be cleanly recovered to `PAUSED` on next startup.

---

## 10. Worker Failure vs. Startup Crash Recovery

```text
Known / Handled Pipeline Failure (JobExecutionService)
    ├── AI chain exhausted -> transitions PROCESSING to PAUSED (is_retryable=True)
    └── File corrupted / render error -> transitions PROCESSING to FAILED (is_retryable=False)

Unexpected Worker Crash (DesktopJobRuntime)
    └── Unhandled Python exception in worker thread -> Runtime catches, logs traceback,
        and transitions job to FAILED

Unexpected Process Crash / Power Loss (DesktopAppContainer Startup)
    └── JobRecoveryService.reconcile_stale_jobs() executes on startup before runtime starts,
        converting all orphaned PROCESSING jobs in SQLite to PAUSED
```

---

## 11. `ThreadSafeMemoryRateLimiter` Specification (`IRateLimiter`)

Implements the canonical port [`application/ports/rate_limiter.py`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/ports/rate_limiter.py):

```python
class IRateLimiter(ABC):
    @abstractmethod
    def wait_if_needed(self, slot: ApiSlot) -> None: ...
    @abstractmethod
    def mark_request_sent(self, slot: ApiSlot) -> None: ...
```

### Exact Method Semantics:
- **`wait_if_needed(slot: ApiSlot) -> None`:**
  - Queries `slot.credential_ref.identifier` (or fallback `slot.provider.lower()`).
  - Inspects monotonic timestamps in the last 60.0s window (`time.monotonic()`).
  - If request count in window $\ge$ configured RPM: calculates `wait_time = 60.0 - (now - earliest_timestamp_in_window)`.
  - Releases lock during `time.sleep(wait_time)`.
  - **Does NOT record any timestamp.**
- **`mark_request_sent(slot: ApiSlot) -> None`:**
  - Called immediately after an outbound request is issued.
  - Acquires lock, appends current `time.monotonic()` to the deque for the slot, and evicts timestamps older than `now - 60.0s`.

---

## 12. Canonical Desktop Dependency Graph (`DesktopAppContainer`)

```text
                     interfaces/desktop/
                    (DesktopAppContainer)
                             │
       ┌─────────────────────┴─────────────────────┐
       ▼                                           ▼
application/services/                     infrastructure/
(JobSubmission, JobExecution,             (SQLite UoW, Keyring,
 JobRecovery, Settings, Artifacts)        MemoryRateLimiter, PyMuPDF)
       │                                           │
       ▼                                           ▼
application/ports/ ◄───────────────────────────────┘
(IUnitOfWork, ICredentialResolver,
 IRateLimiter, IArtifactStorage,
 IApplicationEventPublisher)
       │
       ▼
     core/
(Job, ApiSlot, AppSettings,
 JobStateTransitionPolicy, FallbackPolicy)
```

---

## 13. File-by-File Implementation & Classification Matrix

| File Path | Classification | Detailed Action & Rationale |
| :--- | :--- | :--- |
| `infrastructure/rate_limiting/memory_rate_limiter.py` | **CREATE** | In-memory thread-safe rate limiter with monotonic sliding window. |
| `infrastructure/events/__init__.py` | **CREATE** | Event package exports. |
| `infrastructure/events/event_bus.py` | **CREATE** | Thread-safe `InMemoryEventBus` with lock-free callback dispatch. |
| `interfaces/desktop/__init__.py` | **CREATE** | Desktop interface package initializer. |
| `interfaces/desktop/composition.py` | **CREATE** | Canonical `DesktopAppContainer` composition root. |
| `interfaces/desktop/workers/__init__.py` | **CREATE** | Workers package initializer. |
| `interfaces/desktop/workers/runtime.py` | **CREATE** | `DesktopJobRuntime` managing concurrency and atomic job claiming. |
| `application/services/job_submission.py` | **MODIFY** | Pre-insertion source PDF ingestion, optional schedule, clean legacy user quota code. |
| `application/services/job_execution.py` | **MODIFY** | Execute pre-claimed jobs, enforce cancellation checkpoints, publish typed application events. |
| `application/services/job_recovery.py` | **MODIFY** | Add `reconcile_stale_jobs()`, resume/retry without user quota checks. |
| `application/services/quick_convert.py` | **MODIFY** | Clean desktop-first image conversion without server user quota checks. |
| `tests/unit/test_memory_rate_limiter.py` | **CREATE** | Unit and concurrency tests for `ThreadSafeMemoryRateLimiter`. |
| `tests/unit/test_event_bus.py` | **CREATE** | Unit tests for `InMemoryEventBus` (thread safety, error isolation). |
| `tests/unit/test_desktop_services_reconciliation.py` | **CREATE** | Tests for reconciled desktop services with SQLite UoW and EventBus. |
| `tests/unit/test_desktop_runtime_concurrency.py` | **CREATE** | Multi-worker parallel processing, dynamic capacity scaling, and cancellation tests. |
| `infrastructure/persistence/sqlite/*` | **DO NOT TOUCH** | Phase 8B completed & verified. |
| `infrastructure/security/*` | **DO NOT TOUCH** | Phase 8C completed & verified. |
| `core/*` | **DO NOT TOUCH** | Phase 8A completed & verified. |
| `interfaces/telegram/*`, `handlers/*`, `main.py` | **FROZEN LEGACY** | Preserved frozen compatibility layer. |
| `interfaces/api/*` | **FROZEN LEGACY** | Preserved until Phase 8G decommissioning. |

---

## 14. Machine-Testable Invariants

1. **No Incomplete Pending Jobs:** A committed `PENDING` job always has non-empty `file_path` pointing to a valid on-disk artifact.
2. **No Duplicate Claims:** When $N$ concurrent workers attempt to claim pending jobs simultaneously, no two workers ever receive the same job ID (`len(set(claimed_ids)) == len(claimed_ids)`).
3. **Non-Blocking SQLite Invariant:** Static inspection and runtime hooks verify zero network I/O, PDF rendering, or image cropping occurs inside active `with uow:` blocks.
4. **Secret Isolation:** Zero raw secrets appear in SQLite tables, logs, DTOs, or job records.
5. **Cancellation Mutual Exclusivity:** A cancelled job emits `JobCancelledEvent` and **never** emits `JobCompletedEvent`.
6. **Concurrency Bound:** Active worker threads executing jobs simultaneously never exceed `AppSettings.max_concurrent_jobs`.
7. **Recovery Order:** `reconcile_stale_jobs()` recovers all crashed `PROCESSING` jobs to `PAUSED` before the runtime dispatcher begins claiming work.
8. **EventBus Error Isolation:** If a subscriber callback raises an unhandled exception, all remaining subscribers still receive the event and the publisher finishes cleanly.
9. **Clean Architecture Invariants:** AST analysis verifies zero imports of `keyring`, `sqlite3`, `PySide6`, `fastapi`, or `infrastructure` in `core/` or `application/`.

---

## 15. Incremental Implementation Sequence

```text
Step 1: In-Memory Rate Limiter & Event Bus Infrastructure
    ├── infrastructure/rate_limiting/memory_rate_limiter.py
    ├── infrastructure/events/event_bus.py
    └── tests/unit/test_memory_rate_limiter.py, tests/unit/test_event_bus.py

Step 2: Service Layer Desktop Reconciliation
    ├── Refactor application/services/job_submission.py (pre-insertion source ingestion)
    ├── Refactor application/services/job_execution.py (claimed job execution, cancellation checkpoints, events)
    ├── Refactor application/services/job_recovery.py (startup stale reconciliation)
    ├── Refactor application/services/quick_convert.py (desktop-first OCR)
    └── tests/unit/test_desktop_services_reconciliation.py

Step 3: Desktop Composition Root & Concurrent Runtime
    ├── interfaces/desktop/composition.py (DesktopAppContainer)
    ├── interfaces/desktop/workers/runtime.py (DesktopJobRuntime)
    └── tests/unit/test_desktop_runtime_concurrency.py

Step 4: Full Verification & Architectural Invariant Audit
    ├── Run complete pytest suite (224 baseline + all new Phase 8D tests)
    ├── Verify git diff --check and AST layer boundaries
    └── Prepare Phase 8D implementation report
```
