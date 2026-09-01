# AGENTS.md — Standing Rules for PolpoT

These rules apply to every mission in this project — architecture, planning, coding,
refactoring, debugging, auditing, testing, and documentation — regardless of what any
single prompt says.

If a specific prompt conflicts with a rule here, follow this file unless the prompt
explicitly and intentionally overrides the rule.

---

## 1. Verify Before Judging "Current", "Valid", or "Deprecated"

Your training data has a knowledge cutoff in the past. You are **not** a reliable
authority on:

* current AI model names or versions;
* current library or SDK versions;
* breaking changes in external APIs or SDKs;
* current API endpoint shapes;
* current rate limits or pricing;
* package deprecations;
* platform support claims.

Whenever a task depends on whether an external model, library, SDK, API, or platform
feature is current, valid, supported, or deprecated:

* If live web search or current documentation is available, verify the claim before
  acting.
* If live verification is unavailable, do not silently "correct" unfamiliar names,
  versions, or endpoints.
* Mark the claim explicitly as:

`UNVERIFIED — based on training data, may be outdated`

and surface it as an open question rather than changing working code based on an
assumption.

This applies equally to implementation plans, audits, tests, and documentation.

---

## 2. Desktop-First, Local-First, Serverless Architecture

PolpoT is a **desktop-first, local-first, embedded application**.

The Qt desktop client is the primary product and the primary architectural consumer.

The canonical production architecture is:

```text
PySide6 / QML
      ↓
Desktop Controllers / ViewModels
      ↓
Application Services
      ↓
Application Ports
      ↓
Infrastructure Adapters
      ↓
Local SQLite / OS Keyring / Local Storage / AI Provider SDKs
```

There is **no application backend server**.

The desktop application MUST NOT require:

* cPanel;
* VPS infrastructure;
* a remote application server;
* FastAPI;
* uvicorn;
* HTTP communication between the UI and application layer;
* JWT authentication;
* MySQL or another external database server;
* Telegram for application state or storage.

The only normal network activity in the desktop application is **outbound HTTPS
communication directly from the user's machine to configured AI providers**.

Architectural decisions MUST optimize for:

1. domain correctness and maintainability;
2. reusable application services;
3. local-first desktop execution;
4. security of local data and credentials;
5. concurrency and reliability;
6. testability and architectural isolation;
7. cross-platform portability;
8. legacy Telegram compatibility where still intentionally preserved.

A server must never be introduced merely because it would simplify local implementation.

---

## 3. No Server Dependency

A successful desktop installation MUST be capable of:

* starting without an Internet connection;
* opening and browsing local history;
* reading and writing local settings;
* accessing local artifacts;
* managing local prompts;
* managing locally stored API configuration;
* performing all non-AI local operations;

without requiring any remote PolpoT service.

AI operations naturally require outbound connectivity to the configured AI provider.

No local HTTP server should be started merely to connect the desktop UI to application
services.

Do not introduce loopback REST endpoints such as:

```text
localhost:8000
127.0.0.1
```

as an internal communication mechanism unless a future architecture decision explicitly
requires one.

---

## 4. Telegram Is a Frozen Legacy Transport

Telegram is no longer an architectural driver.

The existing Telegram implementation is treated as a **frozen legacy transport
adapter**.

Unless explicitly requested:

* do not add new product capabilities to Telegram;
* do not design new application services around Telegram requirements;
* do not add Telegram-specific persistence requirements;
* do not introduce Telegram concepts into desktop domain models;
* do not make desktop functionality depend on Telegram modules;
* do not allow Telegram compatibility to distort the desktop architecture.

The following areas are legacy compatibility surfaces:

* `interfaces/telegram/`
* `handlers/`
* `main.py`
* remaining Telegram-specific infrastructure required only to preserve the frozen
  transport.

Desktop code MUST have zero dependencies on Telegram transport modules.

---

## 5. Business Logic Does Not Belong in Presentation Adapters

Presentation adapters are intentionally thin.

This applies to:

* Qt controllers;
* QML-facing models;
* legacy Telegram handlers;
* future non-UI adapters.

Presentation code may:

* validate basic presentation-level input;
* translate UI input into typed commands/DTOs;
* invoke application services;
* expose results to the UI;
* subscribe to application events;
* perform presentation-only formatting.

Presentation code MUST NOT contain:

* API fallback decisions;
* retry policy;
* job state transition rules;
* persistence logic;
* SQL statements;
* credential storage logic;
* AI provider selection policy;
* quota/business policy;
* artifact lifecycle rules;
* scheduling policy.

Business decisions belong in `core/` or `application/`.

---

## 6. Clean Architecture and Dependency Direction

The dependency direction is inward:

```text
interfaces/
    ↓
application/
    ↓
core/

infrastructure/
    ↓
application ports
```

The following dependency rules are mandatory.

### 6.1 `core/`

`core/` MUST remain independent from:

* PySide6;
* Qt;
* QML;
* sqlite3;
* keyring;
* FastAPI;
* Starlette;
* Telegram;
* infrastructure implementations;
* application services;
* external database drivers;
* AI vendor SDKs;
* filesystem frameworks;
* UI frameworks.

### 6.2 `application/`

`application/` MUST remain independent from:

* PySide6;
* Qt;
* QML;
* sqlite3;
* keyring;
* FastAPI;
* Starlette;
* Telegram;
* infrastructure implementations;
* concrete database drivers;
* concrete AI vendor SDKs.

Application code communicates with infrastructure through ports/interfaces.

### 6.3 Desktop Presentation

`interfaces/desktop/controllers/` MUST NOT directly import:

* sqlite3;
* pymysql;
* keyring;
* Google AI SDKs;
* OpenAI SDKs;
* other concrete vendor SDKs.

Controllers must use application services.

### 6.4 QML

QML MUST communicate only with explicitly exposed QObject/ViewModel interfaces.

QML MUST NOT:

* execute SQL;
* access application database files directly;
* read keyring entries;
* implement business rules;
* import Python backend modules as an alternative to the controller boundary.

---

## 7. No Duplicated Logic Across Parallel Flows

Before implementing functionality that resembles an existing flow:

1. locate the existing implementation;
2. determine whether it can be reused;
3. generalize the existing abstraction if appropriate;
4. avoid copying logic into another service or adapter.

Examples include:

* API key registration;
* model detection;
* prompt selection;
* job creation;
* fallback handling;
* retry handling;
* artifact management;
* settings persistence;
* scheduling;
* progress events.

If duplication is unavoidable, document why.

Do not silently create two implementations of the same business rule.

---

## 8. Persistence Rules

All persistent state must have a clearly defined owner and storage boundary.

For the desktop application:

* SQLite is the canonical local application database;
* database access belongs in infrastructure;
* application services access persistence only through ports;
* presentation code must never issue SQL directly.

SQLite connections MUST NOT be shared blindly across worker threads.

Worker execution must use independently scoped database connections/Unit-of-Work
instances appropriate to the concurrency model.

SQLite concurrency-sensitive operations MUST use explicit transaction semantics.

Operations that claim work for execution MUST be atomic and protected against
double-execution.

Do not use ad-hoc schema mutations such as:

```python
try:
    cursor.execute("ALTER TABLE ...")
except:
    pass
```

Schema changes must be:

* explicit;
* versioned;
* deterministic;
* testable;
* reversible where practical.

---

## 9. Database Changes Are Explicit and Reversible

Every schema change must correspond to an explicit migration step.

A migration must document:

* the schema version;
* the changes introduced;
* compatibility assumptions;
* upgrade behavior;
* rollback considerations where applicable.

Do not hide schema changes inside unrelated application logic.

SQLite schema creation and migration must happen through a dedicated persistence
component.

---

## 10. Secret and Credential Security

Secrets are security-sensitive data.

Never hardcode:

* API keys;
* passwords;
* access tokens;
* signing keys;
* encryption keys;
* credentials.

API credentials MUST NOT be stored in:

* SQLite database fields;
* job metadata;
* JSON API-chain serialization;
* DTOs;
* logs;
* debug output;
* source control;
* plaintext configuration files.

Domain objects should contain only a credential reference such as `CredentialRef`.

Raw credentials may be resolved only at the infrastructure boundary required to perform
an AI operation.

Preferred desktop credential storage:

```text
OS Keyring
    ↓
Windows Credential Manager
macOS Keychain
Linux Secret Service / KWallet
```

When an OS keyring is unavailable, a cryptographically secure encrypted fallback may
be used.

A fallback MUST NOT derive its root key solely from:

* machine identifiers;
* MAC addresses;
* hostnames;
* predictable system metadata.

A user-controlled secret such as a master passphrase must be used when persistent
encrypted fallback storage is required.

Never print a live credential in full in logs, reports, tests, or chat output.

If a credential must be referenced, redact it.

---

## 11. No Plaintext Secret Leakage Through Serialization

Any object that can be persisted or serialized MUST be reviewed for secret leakage.

In particular:

* `ApiSlot` must not contain `api_key: str`;
* job API-chain serialization must contain `CredentialRef` information only;
* DTOs must not return raw credentials;
* database repositories must never persist raw credentials;
* debug representations must not expose secrets.

Tests should explicitly verify the absence of plaintext credentials in:

* entities;
* serialized objects;
* SQLite records;
* generated artifacts;
* logs where practical.

---

## 12. Desktop Concurrency

Desktop processing is expected to support multiple simultaneous jobs.

Long-running operations MUST NOT execute on the Qt GUI thread.

This includes:

* PDF rendering;
* OCR;
* AI API requests;
* large file operations;
* expensive document processing;
* database-heavy processing.

Use an appropriate background execution model such as:

* `QThreadPool`;
* `QRunnable`;
* worker threads;
* other explicitly justified background execution mechanisms.

The GUI thread owns presentation state.

Worker threads communicate progress and completion through transport-neutral application
events and Qt-safe signals/bridges.

Do not mutate Qt GUI models directly from worker threads.

---

## 13. Atomic Job Ownership

When multiple workers are able to execute pending jobs, job claiming MUST be atomic.

A worker must never:

1. read a pending job;
2. release the database;
3. later mark it as processing;

without protecting the claim operation transactionally.

The repository layer should expose explicit operations such as:

```text
claim_job(...)
claim_next_pending(...)
```

using transaction semantics appropriate for SQLite concurrency.

Tests MUST cover:

* two workers attempting to claim one job;
* multiple workers claiming multiple jobs;
* no double execution;
* recovery from interrupted claims;
* concurrent status updates.

---

## 14. Cancellation Must Be Cooperative

Cancellation is a stateful application concern.

Cancellation MUST NOT be implemented by abruptly killing arbitrary worker threads.

The preferred model is:

```text
UI request
    ↓
runtime cancellation request
    ↓
persistent / thread-safe cancellation state
    ↓
worker checks cancellation boundary
    ↓
safe state transition to CANCELLED
```

Workers should check cancellation at appropriate boundaries such as:

* before starting a page;
* before an expensive AI request;
* after an AI request;
* before committing the next stage.

Cancelled jobs must not accidentally transition into `DONE`.

---

## 15. Scheduling Must Be Persistent

Scheduled jobs must survive application restarts.

A schedule MUST NOT exist only in memory.

Persistent scheduling data belongs in the local database.

The scheduler may use an in-process timer such as `QTimer`, but the timer is only the
trigger mechanism; SQLite remains the source of truth.

Startup logic must reconcile:

* due jobs;
* missed schedules;
* stale processing jobs;
* interrupted work.

Never assume the application was continuously running.

---

## 16. Source Artifact Ingestion

When a PDF is submitted for processing, the desktop application should ingest a stable
local copy before asynchronous execution begins.

The source document should be stored under the job's artifact directory.

This protects against:

* user deleting the original file;
* user moving the original file;
* rename operations;
* external synchronization changes;
* application restart;
* recovery after failure.

Processing should operate on the ingested local artifact rather than relying on an
external user-selected path remaining valid.

---

## 17. Domain Model Discipline

Avoid "god objects".

State must remain separated by responsibility.

Examples:

* `AppSettings` → UI/runtime preferences;
* `ApiSlot` → provider configuration and credential reference;
* `Job` → document execution state;
* schedule metadata → scheduling concerns;
* artifact entities → stored outputs and handles.

Do not place unrelated configuration, credentials, runtime state, and scheduling state
into one giant entity for convenience.

When an entity begins accumulating unrelated responsibilities, stop and reassess the
boundary.

---

## 18. Application Events Are Transport-Neutral

Application events must not depend on Qt, Telegram, FastAPI, or another transport.

Examples:

```text
JobProgressEvent
ApiSwitchEvent
JobCompletedEvent
JobFailedEvent
JobCancelledEvent
JobStateChangedEvent
```

These events belong to the application boundary.

The desktop layer may adapt them into Qt Signals.

The frozen Telegram transport may adapt them into Telegram notifications.

The application event itself must remain transport-neutral.

---

## 19. Clean Code Standards

Prefer:

* small focused functions;
* meaningful names;
* explicit types;
* narrow responsibilities;
* straightforward control flow;
* simple abstractions;
* composition over unnecessary inheritance.

Avoid:

* speculative abstractions;
* unnecessary wrapper layers;
* god classes;
* hidden global state;
* magical behavior;
* overly clever code;
* duplicated business rules;
* compatibility shims that exist only to avoid correcting an architectural problem.

Use the simplest implementation that correctly satisfies the architecture.

Do not make code more abstract merely because a future feature might someday exist.

---

## 20. Professional Comment and Documentation Standards

All technical source-code documentation MUST be written in **English**.

This includes:

* code comments;
* docstrings;
* TODO/FIXME notes;
* architectural annotations;
* developer-facing warnings;
* test comments;
* implementation notes;
* log messages intended for developers.

Do not write technical comments in Persian or mixed Persian/English.

User-facing application text is exempt and may use the product's required language.

### 20.1 Comments Explain Why, Not What

Do not write comments that merely restate obvious code.

Bad:

```python
# Create the database connection
conn = sqlite3.connect(path)
```

Good:

```python
# Each worker owns its connection so SQLite handles concurrency without
# sharing a connection across worker threads.
conn = sqlite3.connect(path)
```

### 20.2 Prefer Self-Documenting Code

Before adding a comment, first ask whether the code can be made clearer through:

* better naming;
* explicit types;
* smaller functions;
* clearer control flow;
* a dedicated abstraction.

### 20.3 No Comment Noise

Do not add comments such as:

```python
# Step 1
# Check the job
# Process the file
# Save the result
```

when the code already expresses those operations clearly.

Do not leave:

* commented-out code;
* obsolete implementation blocks;
* decorative comment clutter;
* meaningless separator banners.

Comments should provide information that would otherwise be difficult to infer.

### 20.4 Architectural Comments

Use comments for non-obvious architectural constraints.

Good candidates include:

* why `BEGIN IMMEDIATE` is required;
* why database connections are worker-local;
* why credentials are represented by `CredentialRef`;
* why PDFs are copied on ingestion;
* why a compatibility adapter exists;
* why a dependency direction is enforced;
* why a recovery path handles stale jobs.

---

## 21. Compatibility Code Must Be Explicit

Any temporary compatibility code MUST contain a concise English comment explaining:

1. why it exists;
2. which legacy behavior it supports;
3. whether it is frozen or transitional;
4. what milestone can remove it.

Example:

```python
# Compatibility bridge for the frozen Telegram transport.
# Desktop code must not depend on this path.
# This bridge is retained until the legacy transport is formally removed.
```

Do not create compatibility shims merely to conceal architectural violations.

---

## 22. TODO / FIXME Discipline

TODO and FIXME comments must describe concrete work.

Bad:

```python
# TODO: fix this
```

Good:

```python
# TODO(phase-8h): replace the temporary recovery heuristic with
# persisted worker-lease metadata.
```

Do not add vague or speculative TODOs.

---

## 23. Dead Code Must Be Removed

New or modified code MUST NOT leave behind:

* unused imports;
* unused variables;
* unreachable branches;
* obsolete helpers;
* abandoned compatibility paths;
* commented-out implementations;
* duplicate implementations.

When functionality is replaced, remove the obsolete implementation unless it is
explicitly classified as frozen legacy code.

Frozen legacy code must remain isolated and must not constrain the new architecture.

---

## 24. Minimal and Focused Diffs

When modifying existing code:

* change only what the current task requires;
* preserve unrelated behavior;
* avoid unrelated formatting changes;
* avoid opportunistic renaming;
* avoid unnecessary file rewrites.

A diff should be understandable from the architectural or behavioral objective that
motivated it.

---

## 25. Tests Are Part of the Architecture

Tests must verify both behavior and architectural boundaries.

Important classes of tests include:

* domain behavior;
* application service behavior;
* persistence correctness;
* SQLite transaction behavior;
* concurrent job claiming;
* cancellation;
* scheduling;
* crash recovery;
* credential isolation;
* secret non-persistence;
* controller/service delegation;
* event propagation;
* architecture dependency rules.

Architectural invariants should be enforced automatically where practical using AST or
equivalent static inspection.

A feature is not considered architecturally complete merely because its happy-path test
passes.

---

## 26. Preserve Behavioral Compatibility Intentionally

When replacing an old implementation:

* identify observable behavior that must remain;
* preserve it through the new architecture where still relevant;
* remove behavior that belonged exclusively to obsolete server/Telegram concepts;
* update characterization tests when the old behavior is intentionally retired.

Do not preserve obsolete behavior merely because an old implementation happened to have
it.

The question is:

> Is this behavior part of the product contract, or only a relic of the old architecture?

---

## 27. Review Architectural Trade-Offs Before Implementing Them

For architectural decisions involving:

* storage;
* concurrency;
* scheduling;
* cryptography;
* dependency injection;
* cross-platform behavior;
* threading;
* application events;
* packaging;
* infrastructure adapters;

state the trade-off before implementation when the choice is not already defined by
these rules or by the current approved architecture plan.

Do not silently introduce a new architectural direction.

---

## 28. Cross-Platform Desktop Requirements

Desktop architecture must remain viable across:

* Windows;
* macOS;
* Linux.

Do not use platform-specific functionality in application or domain code.

Platform-specific operations belong in infrastructure or presentation adapters.

Examples:

* file manager integration;
* keyring backends;
* application-data directories;
* process launch behavior;
* filesystem conventions.

Avoid assuming POSIX-only primitives such as `fcntl.flock` in core desktop execution
paths.

---

## 29. Local Application Data Boundaries

Use platform-appropriate application-data directories.

Do not hardcode a single Linux path for all platforms.

Database files, credential vaults, caches, and artifacts should each have an explicit
storage purpose.

Do not place mutable application state inside the source repository.

---

## 30. No Architectural Backsliding

New code MUST NOT reintroduce:

* server-centric assumptions;
* multi-user identity requirements into local desktop domain models;
* Telegram-derived business rules;
* JWT authentication for local application use;
* FastAPI as an internal UI bridge;
* MySQL as a local runtime requirement;
* public/shared API pools unless explicitly reintroduced as a product feature;
* raw API-key fields in domain entities;
* Qt dependencies in `core/`;
* infrastructure dependencies in `core/` or `application/`;
* direct SQL in presentation controllers.

When an obsolete architectural concept is encountered, prefer removing the concept over
creating another compatibility layer.

---

## 31. Desktop Is the Product Boundary

The desktop application is not a thin replacement UI over an old backend.

It is the primary product.

The application architecture should therefore optimize for:

* offline-first local state;
* predictable local execution;
* responsive UI;
* user-owned data;
* user-owned credentials;
* concurrent document processing;
* persistent local history;
* persistent local scheduling;
* local artifact management;
* secure BYOK workflows;
* future cross-platform expansion.

Future mobile clients may reuse domain/application concepts where technically feasible,
but mobile support must not compromise the desktop's local-first architecture.

A future client does not justify introducing a remote server today.

---

## 32. Completion Checklist

Before declaring an implementation phase complete, perform a final cleanup and audit
covering:

1. all tests pass;
2. `git diff --check` is clean;
3. no unused imports remain;
4. no dead code was introduced;
5. technical comments/docstrings are in English;
6. no technical comments were unnecessarily added;
7. no plaintext secrets are persisted or logged;
8. architecture boundaries remain intact;
9. no desktop code depends on Telegram;
10. no desktop code depends on a PolpoT server;
11. concurrency behavior is explicitly tested where applicable;
12. persistence changes are versioned and testable;
13. compatibility code is clearly marked;
14. obsolete implementations are removed or explicitly frozen;
15. the implementation still matches the approved desktop-first architecture.

The implementation is not considered complete until this cleanup pass has been
performed.
