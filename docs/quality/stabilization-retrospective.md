# Polpo Stabilization Milestone Retrospective & Final Handoff

**Document:** `docs/quality/stabilization-retrospective.md`<br/>
**Repository:** `amirreza-a2a/Polpo`<br/>
**Working Directory:** `/home/amirreza-a2a/DevelopPOlpo/PolpoT`<br/>
**Date:** 2026-10-30<br/>
**Starting Baseline SHA (STAB-01 Closeout):** `79e7940e1f4cdb2938ecb336c58eed22bd18b1fa`<br/>
**Implementation Merge SHA:** `714e78d07b22412f528c044b3695182151aede0e` (PR #162)<br/>
**Historical STAB-02 Closeout Merge SHA:** `2b55de09c33dddbffe039b34d2ee35662daab4e7` (PR #163)<br/>
**Subsequent Reconciliation Hardening Merge SHA:** `8a7670a3c68fe07248786074863e2182aef0cc1c` (PR #164)<br/>
**Subsequent Evidence-Correction Merge SHA:** `15d4cb09a731f6f7272eaa8704bf2b424d0260b2` (PR #165)<br/>
**Post-Completion Reconciliation Merge SHA (Current Handoff Baseline):** `f33836d5b1ffa622397f63c0c5e5eedf71cdc0f5` (PR #166)<br/>
**Milestone:** `STAB-02` (Final Stabilization Milestone Closeout)<br/>
**Status:** COMPLETE & AUTHORITATIVE<br/>

---

## 1. Executive Summary

The Polpo stabilization initiative was chartered to transform an evolving multi-transport prototype into a desktop-first, local-first, serverless application with enterprise-grade reliability, reproducible build and packaging invariants, deterministic continuous integration, and airtight architectural boundaries.

Prior to stabilization, the repository contained significant architectural drift:
- Pipeline 2 refinement jobs were queued by Pipeline 1 but silently ignored by the desktop worker runtime, with execution occurring solely in legacy server scripts.
- Pipeline 2 outputs bypassed canonical document versioning, risking silent overwrites of user edits.
- Theme switching contained 230+ unauthorized hardcoded color literals that degraded contrast and broke dark/light parity.
- Developer documentation retained obsolete server-centric instructions and inconsistent naming (`PolpoT`).
- Persian technical comments and docstrings populated core infrastructure files, violating project standards.
- CI workflows relied on Node.js 20 actions facing platform deprecation, with unhardened inline expressions and potential release dry-run hazards.
- Windows-specific runtime risks (subprocess console flashing, process group termination, and file-locking differences) lacked regression proof.

Across 12 major task initiatives encompassing 40 pull requests through STAB-02 closeout (PRs #124–#163), 41 pull requests including subsequent post-STAB-02 reconciliation hardening (PRs #124–#164), and 42 pull requests encompassing all stabilization tasks and evidence corrections (PRs #124–#165), with PR #166 serving as an additional post-completion reconciliation ([HEAD `93e552563abe14ee6801d3d70afdd3d3a996504c`](https://github.com/amirreza-a2a/Polpo/commit/93e552563abe14ee6801d3d70afdd3d3a996504c), workflow run [41100010840](https://github.com/amirreza-a2a/Polpo/actions/runs/41100010840), merged at `f33836d5b1ffa622397f63c0c5e5eedf71cdc0f5`), the stabilization roadmap (`docs/quality/stabilization-roadmap.md`) systematically resolved these defects without introducing feature creep or breaking changes. Progressing from the STAB-01 closeout baseline (`79e7940e1f4cdb2938ecb336c58eed22bd18b1fa`) through the STAB-02 implementation merge (`714e78d07b22412f528c044b3695182151aede0e`, PR #162), the historical STAB-02 milestone closeout (`2b55de09c33dddbffe039b34d2ee35662daab4e7`, PR #163), subsequent reconciliation hardening (`8a7670a3c68fe07248786074863e2182aef0cc1c`, PR #164), final evidence correction (`15d4cb09a731f6f7272eaa8704bf2b424d0260b2`, PR #165), and post-completion reconciliation (`f33836d5b1ffa622397f63c0c5e5eedf71cdc0f5`, PR #166), the codebase achieves:
1. **Zero Test Failures with Explicitly Classified Skips:** All executed tests passed with zero failures and fully classified skips across reference environments:
   - **Local Linux Reference Host (Noble 24.04):** 2,212 passed, 6 skipped (Windows Job Object integration tests), 2 warnings, 201 subtests passed across 2,218 collected items in 136.04s (historical STAB-01 implementation PR #160 recorded 123.55s on a lighter suite).
   - **Linux CI Runner (`ubuntu-24.04`):**
     - **Exact PR #166 CI Execution (Workflow run [41100010840](https://github.com/amirreza-a2a/Polpo/actions/runs/41100010840), HEAD `93e552563abe14ee6801d3d70afdd3d3a996504c`):** 2,204 passed, 14 skipped (6 Windows Job Object + 8 optional host tooling / environmental condition tests), 0 failed, 2 warnings, 198 subtests passed across 2,218 collected items in 79.16s (total job duration 4m 38s; independently verified on PR #165 run [41096211959](https://github.com/amirreza-a2a/Polpo/actions/runs/41096211959) with identical counts).
     - **Historical STAB-01 CI Run (push run `40940519039` on commit `79e7940e1f4cdb2938ecb336c58eed22bd18b1fa`):** 2,205 passed, 13 skipped, 201 subtests passed across 2,218 collected items in 152.03s.
   - **Windows Server 2025 CI (`windows-2025`):**
     - **Exact PR #166 CI Execution (Workflow run [41100010840](https://github.com/amirreza-a2a/Polpo/actions/runs/41100010840), HEAD `93e552563abe14ee6801d3d70afdd3d3a996504c`):** 2,167 passed, 51 skipped (all 6 Windows Job Object tests pass; 36 Linux-only installer/updater + 7 POSIX-only + 8 optional tooling / environmental condition tests skip), 0 failed, 2 warnings, 198 subtests passed across 2,218 collected items in 205.55s (total job duration 5m 16s; independently verified on PR #165 run [41096211959](https://github.com/amirreza-a2a/Polpo/actions/runs/41096211959) with identical counts).
     - **Historical Intermediate Baselines:** STAB-01 push CI run `40940519039` recorded 2,168 passed, 50 skipped, 201 subtests passed in ~4m 42s (earlier intermediate baseline STAB-00 reported 2,013 passed / 50 skipped prior to subsequent test suite additions).
2. **Deterministic Quality Gates:**
   - **Local Reference Host Execution:** Diff hygiene, bytecode compilation across 5 packages, and 88 AST architecture and contract invariant tests (164 subtests) pass in 4.15s (local execution).
   - **CI Quality-Gates Runner (`ubuntu-24.04`, Workflow run [41100010840](https://github.com/amirreza-a2a/Polpo/actions/runs/41100010840)):** 86 passed, 2 skipped (actionlint and real user DB host tooling tests), 161 subtests passed in 1m 0s (job conclusion: success).
3. **Headless Self-Test Coverage:** The desktop diagnostic self-test (`interfaces.desktop.self_test`) verifies 12 runtime and packaging invariants (Pandoc AST parsing, MathJax SVG rendering, SQLite bootstrap, UnitOfWork access, credential isolation, and QML compilation) in isolated headless environments.
4. **Enforced Security and Release Safeguards:** All GitHub Actions workflows run on Node 24 pinned to immutable 40-character commit SHAs, release publication is strictly gated to authenticated pushes on `refs/tags/v*`, and the mandatory release freeze remains intact (`v0.1.0-rc.1` pre-release candidate).

---

## 2. Mission Timeline

The stabilization roadmap progressed through six distinct architectural phases:

```text
[Phase 0: Baseline] ──> [Phase 1: Pipeline 2] ──> [Phase 2: Theme System]
       │                         │                         │
     STAB-00                   P2-01                    THEME-01
                               P2-02                    THEME-02
                              P2-02-R1
                               P2-03
       ┌─────────────────────────┴─────────────────────────┘
       ▼
[Phase 3: Documentation & Code Hygiene] ──> [Phase 4: CI & Automation]
       │                                           │
     DOC-01                                      CI-01
     DOC-02                                   CI-01-HARDEN
     FA-01                                    CI-SCAN-COV
    FA-01-RES
       ┌───────────────────────────────────────────┘
       ▼
[Phase 5: Cross-Platform Runtime & Final Milestone]
       │
     STAB-01
     STAB-02 (Current Closeout)
```

### Detailed Mission Progression

| Task ID | Implementation PR | Merge Commit SHA | Closeout / Hardening PR(s) | Focus Area & Resolution |
| :--- | :--- | :--- | :--- | :--- |
| **STAB-00** | [PR #124](https://github.com/amirreza-a2a/Polpo/pull/124) | `4ce951f1198fdaba04b0c4db231fed4a1d410916` | [PR #125](https://github.com/amirreza-a2a/Polpo/pull/125) (`c2f12d7d...`) | Verification baseline establishment, exact gate commands, and 2,063 test skip categorization across Linux and Windows. |
| **P2-01** | [PR #126](https://github.com/amirreza-a2a/Polpo/pull/126) | `726cd03ba429f8cc45428caf3fc3320f09504d83` | [PR #127](https://github.com/amirreza-a2a/Polpo/pull/127) (`e6f6ba31...`), [PR #128](https://github.com/amirreza-a2a/Polpo/pull/128) (`7d7caa01...`) | Diagnosed desktop runtime dispatch gap for Pipeline 2; wired concurrent worker dispatching with capacity controls, start gates, and crash isolation. |
| **P2-02** | [PR #129](https://github.com/amirreza-a2a/Polpo/pull/129) | `b1c41aa583c0e52032045414e6606d7f6160fb2c` | [PR #130](https://github.com/amirreza-a2a/Polpo/pull/130) (`f404c7d2...`) | Hardened Pipeline 2 lifecycle, atomic claiming under `BEGIN IMMEDIATE`, cooperative cancellation checkpoints, sanitized error handling, and startup recovery. |
| **P2-02-R1** | [PR #131](https://github.com/amirreza-a2a/Polpo/pull/131) | `144be722fe0718bcac36553336546ef32d5c6d0f` | [PR #132](https://github.com/amirreza-a2a/Polpo/pull/132) (`2ef193d7...`), [PR #133](https://github.com/amirreza-a2a/Polpo/pull/133) (`b9b7141e...`), [PR #134](https://github.com/amirreza-a2a/Polpo/pull/134) (`b0d73aa4...`) | Cross-process database locking (`SQLiteInstanceLock` via `fcntl` on POSIX and `msvcrt.locking` with out-of-band offset on Windows); eliminated broad exception masking; hardened against permission errors and in-memory URI lock bypass. |
| **P2-03** | [PR #135](https://github.com/amirreza-a2a/Polpo/pull/135) | `a2e2cfc68622f57e9b1fadfa92d4e30461baa5f9` | [PR #136](https://github.com/amirreza-a2a/Polpo/pull/136) (`e8989db8...`), [PR #137](https://github.com/amirreza-a2a/Polpo/pull/137) (`ec5854f7...`), [PR #138](https://github.com/amirreza-a2a/Polpo/pull/138) (`e0ad4acb...`) | Clarified Pipeline 2 refinement as auxiliary derivative artifact (`ADR-004`); added SQLite Migration 005 (`source_version`); implemented OCC publication in `DocumentPublicationService`; protected unsaved human edits in editor pane. |
| **THEME-01** | [PR #139](https://github.com/amirreza-a2a/Polpo/pull/139) | `d5716b2da42ef0fb2723d7a8543a9fd00f523c31` | [PR #140](https://github.com/amirreza-a2a/Polpo/pull/140) (`d5c8be67...`) | Audited authoritative 41-token `ThemePalette`; eliminated unauthorized hardcoded color literals across QML components; preserved CV bounding box graphic constants; enforced product branding. |
| **THEME-02** | [PR #141](https://github.com/amirreza-a2a/Polpo/pull/141) | `f11c95c4c44cdb753156d205e387f035e0fee1bf` | [PR #142](https://github.com/amirreza-a2a/Polpo/pull/142) (`65a9f4b1...`), [PR #143](https://github.com/amirreza-a2a/Polpo/pull/143) (`3d71f98a...`), [PR #144](https://github.com/amirreza-a2a/Polpo/pull/144) (`347431dc...`) | Verified dynamic theme switching across system/dark/light; defaulted `Qt.ColorScheme.Unknown` to dark; coupled MathJax foreground colors to theme with cache URL isolation; verified WCAG 2.2 contrast compliance; eliminated 231 hardcoded fallback colors. |
| **DOC-01** | [PR #145](https://github.com/amirreza-a2a/Polpo/pull/145) | `c7181a57d6fbe2c59fc9d1b648110295f377a1f0` | [PR #146](https://github.com/amirreza-a2a/Polpo/pull/146) (`5a9d8b60...`) | Reconciled public documentation (`README.md`, `CHANGELOG.md`, `docs/linux-installation.md`, `docs/packaging.md`) with desktop reality; enforced release freeze notices; separated automated CI tiers from community distro mappings. |
| **DOC-02** | [PR #147](https://github.com/amirreza-a2a/Polpo/pull/147) | `515fd05317b661f42e32041d315aaaf72dc22f72` | [PR #148](https://github.com/amirreza-a2a/Polpo/pull/148) (`d7eabb5c...`), [PR #149](https://github.com/amirreza-a2a/Polpo/pull/149) (`6c04721f...`) | Reconciled developer documentation (`AGENTS.md`, `CONTEXT.md`, `docs/agents/skills.md`, `docs/ci-repository-rules.md`); isolated frozen legacy surfaces; cataloged 16 environment skills and banned vendoring; verified branch protection. |
| **FA-01** | [PR #150](https://github.com/amirreza-a2a/Polpo/pull/150) | `22dba4135840f766c093218f548e8451d67dfc1c` | [PR #151](https://github.com/amirreza-a2a/Polpo/pull/151) (`8642432a...`), [PR #152](https://github.com/amirreza-a2a/Polpo/pull/152) (`39c1157f...`), [PR #153](https://github.com/amirreza-a2a/Polpo/pull/153) (`2aca2e8e...`) | Translated Persian technical comments and docstrings across 20 source files to English; preserved Persian prompt templates and test strings; verified 100% AST invariance across all modified Python files. |
| **CI-01** | [PR #154](https://github.com/amirreza-a2a/Polpo/pull/154) | `6f1c9efa6ab5d14b3b2f2de0d4225eb447aa00f2` | [PR #155](https://github.com/amirreza-a2a/Polpo/pull/155) (`94253289...`), [PR #156](https://github.com/amirreza-a2a/Polpo/pull/156) (`bb83f41e...`), [PR #157](https://github.com/amirreza-a2a/Polpo/pull/157) (`ab4ea4f9...`), [PR #158](https://github.com/amirreza-a2a/Polpo/pull/158) (`dd081b99...`), [PR #159](https://github.com/amirreza-a2a/Polpo/pull/159) (`4dc3ccb3...`) | Migrated GitHub Actions to Node 24 pinned to 40-char SHAs; validated release gating; eliminated unsafe expression interpolation in `run:` blocks; added standalone workflow expression scanner and contract tests. |
| **STAB-01** | [PR #160](https://github.com/amirreza-a2a/Polpo/pull/160) | `bb41c826ee34dbde9dcb748ddfd2896329faf669` | [PR #161](https://github.com/amirreza-a2a/Polpo/pull/161) (`79e7940e...`) | Cross-platform audit: Windows `CREATE_NO_WINDOW` subprocess suppression in Pandoc; self-test Migration 005 and UnitOfWork coverage; hardened concurrency wait loops; resolved timing flakes. |
| **STAB-02** | [PR #162](https://github.com/amirreza-a2a/Polpo/pull/162) | `714e78d07b22412f528c044b3695182151aede0e` | [PR #163](https://github.com/amirreza-a2a/Polpo/pull/163) (`2b55de09...`), [PR #164](https://github.com/amirreza-a2a/Polpo/pull/164) (`8a7670a3...`), [PR #165](https://github.com/amirreza-a2a/Polpo/pull/165) (`15d4cb09...`), [PR #166](https://github.com/amirreza-a2a/Polpo/pull/166) (`f33836d5...`) | Full verification pass, retrospective authoring, feature-ready handoff baseline, subsequent documentation link/SHA reconciliation hardening, final evidence-correction reconciliation (PR #165), and post-completion reconciliation (PR #166). |

---

## 3. Architecture Lessons

### 3.1 Local-First Desktop Architecture & Strict Boundary Invariants

Polpo's core invariant is that it is a **desktop-first, local-first, serverless application** (`AGENTS.md` Rule 2). The application must start, read and write local SQLite databases, manage API credentials via OS Keyring, render Markdown and MathJax formulas, and browse local history completely offline.

Earlier legacy code had blurred boundaries by introducing HTTP-like concepts, shared connection pools, and server worker loops. Stabilization re-established clean inward dependency flow:
```text
interfaces/desktop/ (Controllers, ViewModels, QML)
        ↓
application/services/ (JobExecution, DocumentPublication, Theme, Recovery)
        ↓
core/entities/ & core/ports/ (Pure domain entities, repository contracts, CredentialRef)
        ↑
infrastructure/ (SQLite persistence, Keyring credentials, Pandoc/MathJax adapters)
```

Direct presentation imports of `sqlite3`, raw SQL strings in controllers, and concrete vendor SDK imports in `interfaces/` are strictly blocked by automated AST invariant tests (`tests/unit/test_architecture_boundaries.py`).

### 3.2 Derivative Artifacts Must Not Silently Overwrite Canonical Documents (ADR-004)

During Phase 1 (P2-03), an architectural ambiguity was uncovered: when Pipeline 2 (LLM typography and Persian punctuation refinement) completes, does its output automatically replace the document produced by Pipeline 1?

The analysis formalized in [ADR-004](../adr/2026-10-22-adr-004-pipeline2-artifact-lineage-and-canonical-publication-contract.md) established:
1. **Pipeline 2 outputs are auxiliary derivative artifacts**, categorized as `ArtifactType.PIPELINE2_MARKDOWN` (`p2_output_{job_id}.md`).
2. **Canonical adoption is an explicit, deliberate human action** initiated from the desktop UI (`MarkdownEditorPane.qml`).
3. Automatic replacement would silently destroy user edits made in the review workspace between Pipeline 1 completion and Pipeline 2 completion.
4. Adoption must advance the canonical document version via `DocumentPublicationService.publish_version()` under Optimistic Concurrency Control (OCC). If the user modified the document while Pipeline 2 was running, the OCC check rejects the stale adoption, preventing silent loss of human edits.

### 3.3 Atomic Job Claiming and Cross-Process Single-Instance Database Locking

In desktop applications, multiple threads and multiple operating system processes can access the SQLite database:
1. **Worker Threads within One Process:** Multiple threads sharing a single `sqlite3.Connection` trigger SQLite threading errors or lock starvation. Stabilization established `AGENTS.md` Rule 8 and 13: each background worker owns an independently scoped connection/UnitOfWork instance, and job claiming uses `BEGIN IMMEDIATE` transaction boundaries with busy timeouts.
2. **Cross-Process Protection (`SQLiteInstanceLock`):** If a user launches two instances of the desktop application against the same user data directory (`~/.local/share/polpot/polpot.db`), concurrent startup recovery could double-execute or corrupt state. In P2-02-R1, Polpo implemented `SQLiteInstanceLock` using advisory OS file locking:
   - On POSIX/Linux/macOS: Non-blocking `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)`.
   - On Windows: `msvcrt.locking` on an out-of-band byte offset (`1 << 30`) combined with `_winapi` full file-sharing flags (`FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`). This prevents Windows mandatory locking from blocking normal SQLite WAL operations on the database file while ensuring exclusive application instance ownership.

---

## 4. Cross-Platform Engineering Lessons

### 4.1 Windows Subprocess Window Suppression (`CREATE_NO_WINDOW`)

On Windows, executing child console processes (such as `pandoc.exe` or `node.exe`) using standard `subprocess.Popen` creates a visible console window flash on the user's desktop unless explicitly suppressed. In STAB-01, audit of `infrastructure/markdown/pandoc_binary.py` revealed that while some calls used `creationflags=subprocess.CREATE_NO_WINDOW`, Pandoc extensions inspection and fallback execution lacked the flag.

The fix established a strict cross-platform helper:
```python
creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
```
This is verified by dedicated regression tests in `tests/unit/test_pandoc_binary.py`.

### 4.2 Windows Job Object Process Containment

Terminating worker processes cleanly across operating systems requires fundamentally different primitives:
- On Linux/macOS: Process group IDs (`os.setpgid` and `os.killpg(pgid, signal.SIGTERM)`) allow clean termination of an entire process tree.
- On Windows: Process groups do not exist in the POSIX sense. Killing a parent process leaves orphaned child processes running in the background. Polpo solved this via Windows Job Objects (`infrastructure/process/windows_job.py` and `ProcessTreeKiller`), using Win32 API calls (`CreateJobObject`, `SetInformationJobObject` with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, and `AssignProcessToJobObject`). When the job handle closes (even on unexpected crash), the operating system automatically terminates all descendant processes.

### 4.3 Headless Qt Execution Invariants

Running desktop Qt (PySide6 / QML) test suites in headless CI environments (Linux Docker/GitHub runners) requires explicit platform configuration:
- `QT_QPA_PLATFORM=offscreen`
- `QSG_RHI_BACKEND=software`
Without these environment variables, Qt fails to initialize QPA rendering plugins and aborts with exit code 1. All scripts (`scripts/run_quality_gates.py`, `interfaces/desktop/self_test.py`) and CI workflows enforce these defaults.

### 4.4 Bundled Runtimes and Version Pinning

Polpo bundles two standalone runtime binaries to guarantee offline functionality:
1. **Pandoc 3.1.11:** Musl-statically linked on Linux (`resources/bin/linux-x86_64/pandoc`), statically linked on Windows (`resources/bin/windows-x86_64/pandoc.exe`).
2. **Node.js v22.23.2 & MathJax 3.2.2:** Standalone Node binary (`resources/mathjax/node`) executing the headless MathJax worker script (`resources/mathjax/mathjax_worker.js`).

System-installed Quarto and XeLaTeX are treated strictly as **optional host tooling**. Integration tests that compile live PDF documents skip cleanly when Quarto or XeLaTeX is absent (`test_quarto_pdf_export_e2e.py`).

---

## 5. Security Lessons

### 5.1 Credential Isolation via `CredentialRef`

Polpo enforces a BYOK (Bring Your Own Key) model for external AI providers (Google Gemini, OpenAI). A foundational security rule is:
> **Secrets must never be stored in SQLite database fields, job metadata, JSON files, DTOs, logs, or UI viewmodels (`AGENTS.md` Rule 10, 11).**

Domain entities store only a `CredentialRef` containing provider name, key identifier, and key type. The raw secret is stored securely in:
1. **Primary Desktop Storage:** OS Keyring (Windows Credential Manager, macOS Keychain, Linux Secret Service / KWallet via D-Bus).
2. **Keyring Fallback:** An encrypted local vault secured with user-controlled passphrases, never deriving keys from predictable hardware metadata.

Automated AST and runtime checks (`tests/unit/test_architecture_security.py` and `interfaces.desktop.self_test`) inject synthetic canary secrets (`test-canary-secret-value-do-not-leak`) and assert that the secret string never appears in SQLite database dumps, DTO representations, or log streams.

### 5.2 GitHub Actions Security & Expression Injection Hardening

In CI-01 and its hardening PRs (#154, #157, #159), the continuous integration pipeline was audited against modern GitHub Actions security standards:
1. **Node 24 Migration:** All official workflow actions were upgraded from Node 20 to official Node 24 releases (`actions/setup-node@v7.1.0`, `actions/cache@v6.1.0`, `actions/upload-artifact@v7.0.2`, `actions/download-artifact@v8.0.2`, `actions/attest-build-provenance@v4.2.2`, `softprops/action-gh-release@v3.0.3`).
2. **Immutable Commit SHA Pinning:** Every action is pinned to a full 40-character commit SHA with an inline semantic version comment, preventing supply-chain tag-mutation attacks.
3. **Expression Interpolation Prohibition:** Using `${{ github.event... }}` directly inside `run:` shell blocks permits script injection. All expressions were eliminated from `run:` blocks, passing data strictly through environment variables (`env:`). A standalone AST and regex scanner (`tests/unit/workflow_expression_scanner.py`) enforces this invariant in every quality gate run.
4. **Isolated Release Publishing:** The `publish-release` job in `.github/workflows/release.yml` is strictly gated to `github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')`. Manual `workflow_dispatch` runs are executed in dry-run mode and cannot publish release assets.

---

## 6. Testing and CI Lessons

### 6.1 Architectural Invariants Belong in Quality Gates

Rather than relying solely on post-hoc code review, Polpo enforces architectural rules via AST inspection scripts (`scripts/run_quality_gates.py`):
- Layer dependency rules (`core/` imports nothing external, `application/` imports no presentation or infrastructure, presentation imports no SQL).
- Color token compliance (QML files use `theme.*` rather than hardcoded hex colors).
- Persian comment hygiene (`AGENTS.md` Rule 20 English requirement).
- Workflow expression safety (no context interpolation in bash blocks).

Running these 88 invariant tests (164 subtests locally, 86 passed / 2 skipped / 161 subtests in CI) in ~4 seconds locally (1m in CI) provides instant feedback before running the heavier 2.5-minute full test suite.

### 6.2 The Value of Native Windows CI

Running tests on native `windows-2025` runners revealed defects that passed cleanly on Linux:
- Windows file locking semantics (`msvcrt.locking` contention during rapid test teardown).
- Subprocess window flashing requiring `CREATE_NO_WINDOW`.
- Process tree termination differences requiring Windows Job Object assignment.
- Timing race conditions in threading wait loops (addressed in STAB-01 by adding bounded polling loops instead of static sleeps).

Continuous validation across both Linux and Windows is mandatory for desktop software.

### 6.3 Clear Skip Classification Prevents Broken Window Syndrome

A common failure mode in software maintenance is ignoring skipped tests until broken tests are accidentally skipped. Stabilization cataloged every single test skip across all environments:
- **Local Linux Reference Host:** Exactly 6 tests skipped (Windows Job Object integration tests requiring native Win32 APIs).
- **Linux CI Runner:** Exactly 14 tests skipped in PR #166 CI (6 Windows Job Object tests + 8 optional host tooling / environmental condition tests; historical STAB-01 run `40940519039` recorded 13 skipped).
- **Windows CI Runner:** Exactly 51 tests skipped in PR #166 CI (all 6 Windows Job Object tests pass; 36 Linux-only installer/deps tests skip, 7 POSIX-only tests skip, and 8 optional host tooling / environmental condition tests skip; historical STAB-01 run `40940519039` recorded 50 skipped).

Zero unexpected skips exist. Every skip condition is backed by explicit environmental detection (`@pytest.mark.skipif`).

---

## 7. Agent Workflow Retrospective

### 7.1 Effectiveness of Installed Skills

The project established a strict routing matrix for 16 agent skills installed in the external agent environment (`docs/agents/skills.md`):
- **`code-review`**: Running independent two-axis (Standards + Spec) audits against diffs before opening PRs caught subtle regressions, such as missed QML fallbacks in THEME-02 and leftover Persian docstrings in FA-01.
- **`tdd` / `implement`**: Driving bug fixes test-first (writing the red regression test before touching application code) ensured minimal, focused diffs with zero unrelated refactoring.
- **`diagnosing-bugs`**: Essential during P2-01 (tracing the desktop runtime dispatch gap) and P2-02-R1 (diagnosing Windows `msvcrt` file locking conflicts).
- **`domain-modeling` / `grill-with-docs`**: Guided the resolution of Pipeline 2 document semantics in ADR-004.

### 7.2 Lessons in Multi-Agent Delegation & Git Reconciliations

During earlier stabilization phases, delegating tasks across parallel subagents occasionally introduced reconciliation friction:
1. **Baseline Snapshot Desync:** If an agent records a PR merge commit SHA in Section 9's Progress Log but omits updating Section 2.1's active HEAD snapshot, a subsequent agent will observe a discrepancy between git HEAD and the roadmap's recorded snapshot.
2. **Squash Merge SHA Re-generation:** GitHub squash-and-merge generates a new commit SHA on the target branch (`main`). An agent must record the **actual merge commit SHA on `main`**, not the pull request head branch commit SHA.
3. **Dedicated Closeout PRs:** The established convention of separating functional implementation PRs from roadmap documentation closeout PRs kept code diffs small and auditable, but required two sequential CI cycles per task. Future feature development can combine implementation and documentation updates into a single PR when atomic.

### 7.3 Zero Skill Vendoring Policy

A standing rule established in DOC-02 and enforced by AST test `test_no_skills_vendored_in_repository` is that installed agent skills reside strictly in the external runtime environment (`~/.agents/skills/` or `~/.gemini/skills/`). No skill files, directories, or implementations were copied or committed into the Polpo repository.

---

## 8. Remaining Limitations and Risks

The stabilization milestone has addressed all identified functional, architectural, and security bugs. The following items represent intentional boundaries, environment-specific constraints, or deferred non-functional enhancements:

| # | Item / Area | Description | Severity / Impact | Follow-Up Required? | Suggested Owner / Category |
| :- | :--- | :--- | :--- | :--- | :--- |
| 1 | **Optional Tooling Skips in CI** | Quarto CLI and XeLaTeX are not pre-installed on standard GitHub Actions runners; 3 live PDF compilation tests in `test_quarto_pdf_export_e2e.py` skip cleanly in CI. | `Minor Robustness Risk` — Core Pandoc and MathJax pipelines are 100% bundled and tested; Quarto live export is an optional export format. | Optional / Low Priority: could provision Quarto in periodic CI if live export tests require continuous automated coverage. | DevOps / CI |
| 2 | **Local Windows Testing Boundary** | Developers on Linux hosts cannot locally execute the 6 Windows Job Object integration tests (`tests/integration/test_windows_process_tree.py`). | `Minor Robustness Risk` — Windows behavior is fully verified on native `windows-2025` runners in GitHub Actions CI. | None: CI provides the authoritative Windows verification gate. | CI / Architecture |
| 3 | **ResourceWarnings in AI Test Helpers** | Two benign `ResourceWarning: unclosed file` warnings occur in `tests/unit/test_ai_adapters.py:46` during static module reading. | `Minor Robustness Risk` — Zero impact on production runtime; static file read in test fixture. | Cleanup during next test maintenance cycle. | Quality / Tests |
| 4 | **Mandatory Release Freeze** | Stable release publication remains intentionally frozen (`v0.1.0-rc.1` pre-release). | `Intentional Policy` — In place until maintainer grants written authorization. | Explicit authorization required before tag creation or GitHub release publication. | Product / Maintainer |
| 5 | **macOS Platform Validation Scope** | macOS ARM64 execution is validated periodically via `periodic-validation.yml` (`macos-15`), but is not part of the blocking 4-context PR gating matrix. | `Minor Robustness Risk` — macOS uses POSIX locking and offscreen Qt compatible with Linux. | Add blocking macOS runner if macOS-specific desktop defects emerge in user testing. | Architecture / CI |

---

## 9. Final Verification Metrics

### Comprehensive Test Suite Execution

*Evidence: Exact CI metrics verified from GitHub Actions workflow run [41100010840](https://github.com/amirreza-a2a/Polpo/actions/runs/41100010840) on PR #166 (HEAD: `93e552563abe14ee6801d3d70afdd3d3a996504c`), independently consistent with PR #165 run [41096211959](https://github.com/amirreza-a2a/Polpo/actions/runs/41096211959).*

| Metric / Category | Local Linux Host (Noble 24.04) | Linux CI (`ubuntu-24.04`) [PR #166] | Windows CI (`windows-2025`) [PR #166] | Historical CI Run `40940519039` (STAB-01) |
| :--- | :---: | :---: | :---: | :---: |
| **Passed Tests** | **2,212** | **2,204** | **2,167** | Linux: 2,205 / Windows: 2,168 |
| **Skipped Tests** | **6** | **14** | **51** | Linux: 13 / Windows: 50 |
| **Failed Tests** | **0** | **0** | **0** | 0 failures |
| **Warnings** | **2** | **2** | **2** | 2 warnings |
| **Subtests Passed** | **201** | **198** | **198** | 201 subtests |
| **Total Collected** | **2,218** | **2,218** | **2,218** | 2,218 collected items |
| **Execution Duration** | **136.04s** | **79.16s** (job: 4m 38s) | **205.55s** (job: 5m 16s) | Linux: 152.03s / Windows: ~4m 42s |
| **Outcome** | **PASS** | **PASS** | **PASS** | **PASS** |

### Quality Gates & Static Verification

| Gate Component | Command / Tool | Local Host Result | CI Runner Result (Run 41100010840) | Scope / Notes |
| :--- | :--- | :---: | :---: | :--- |
| **Whitespace Hygiene** | `git diff --check` | **PASS** | **PASS** | Zero trailing whitespace or merge conflict markers. |
| **Bytecode Compilation** | `compileall` across 5 packages | **PASS** | **PASS** | Clean compilation across `core`, `application`, `infrastructure`, `interfaces`, `tests`. |
| **Architecture Invariants** | `scripts/run_quality_gates.py` | **88 passed, 0 skipped, 164 subtests** (4.15s) | **86 passed, 2 skipped, 161 subtests** (1m 0s) | AST invariants; CI skips actionlint and local user DB tests cleanly. |
| **Workflow Linting** | `actionlint .github/workflows/*.yml` | **PASS** | **N/A** (tooling optional) | Zero workflow syntax or actionlint schema violations. |
| **Secret Scanning** | Gitleaks v3.0.0 (`secret-scan`) | **PASS** | **PASS** (0 leaks) | 0 leaks detected across full git commit history. |
| **Desktop Self-Test** | `python -m interfaces.desktop.self_test` | **PASS** | **PASS** | 12/12 runtime and packaging invariants verified in isolated environment. |

---

## 10. Handoff to Future Feature Development

With the stabilization milestone successfully closed, Polpo is ready to transition to active product and feature development. Future engineering sessions should adopt the following operational baseline:

### 10.1 Authoritative Main Baseline & Ground Rules

- **Canonical Repository:** `amirreza-a2a/Polpo`
- **Starting Branch:** `main`
- **Historical STAB-02 Closeout Baseline:** `2b55de09c33dddbffe039b34d2ee35662daab4e7` (milestone closeout commit on `main` at PR #163, following PR #162 at `714e78d07b22412f528c044b3695182151aede0e`).
- **Subsequent Reconciliation Hardening Baseline:** `8a7670a3c68fe07248786074863e2182aef0cc1c` (post-STAB-02 documentation link & SHA hardening at PR #164).
- **Subsequent Evidence-Correction Baseline:** `15d4cb09a731f6f7272eaa8704bf2b424d0260b2` (post-STAB-02 retrospective metric, baseline lineage, and PR count evidence correction at PR #165).
- **Post-Completion Reconciliation Baseline (Authoritative External Handoff):** `f33836d5b1ffa622397f63c0c5e5eedf71cdc0f5` (commit on `main` at PR #166, HEAD `93e552563abe14ee6801d3d70afdd3d3a996504c`, workflow run [41100010840](https://github.com/amirreza-a2a/Polpo/actions/runs/41100010840); verified starting baseline for future feature development).
- **Branching Directive:** Developers must always fetch the latest `origin/main` (`git checkout main && git pull origin main`) before branching rather than branching from an arbitrary historical snapshot SHA.
- **Core Directives:**
  - `AGENTS.md`: Mandatory standing rules governing clean architecture, local-first execution, and security.
  - `CONTEXT.md`: Ubiquitous language, entity boundaries, and system overview.
  - `docs/agents/skills.md`: Agent skills selection matrix and sequencing patterns.
  - `docs/ci-repository-rules.md`: GitHub Classic Branch Protection rules (`strict: true`, linear history, 4 required checks).

### 10.2 Mandatory Non-Negotiable Invariants

1. **Desktop-First & Serverless:** Never introduce a server backend, FastAPI, Uvicorn, or loopback REST endpoints (`localhost:8000`). All desktop communication with AI providers occurs via outbound HTTPS calls directly from the desktop client.
2. **Persistence Discipline:** SQLite is the canonical application database. All access passes through repository interfaces under `UnitOfWork`. Connections must be worker-local. Job claiming must be atomic using `BEGIN IMMEDIATE`.
3. **Secret Security:** Never store raw API credentials in SQLite, DTOs, logs, or UI viewmodels. Use `CredentialRef` and OS Keyring.
4. **Cooperative Cancellation:** Workers must check cancellation checkpoints; never terminate threads abruptly.
5. **English Developer Documentation:** All code comments, docstrings, commit messages, and PR descriptions must be written in professional English (`AGENTS.md` Rule 20).
6. **Zero Skill Vendoring:** Do not copy or vendor installed agent skills into the repository.
7. **Release Freeze Intact:** Do not create or move release tags, draft GitHub releases, or upload release assets until explicit authorization is provided.

### 10.3 Standard Verification Commands for Future Changes

Before pushing any commit or opening a pull request, run:

```bash
# 1. Fast Quality Gates (< 5 seconds)
QT_QPA_PLATFORM=offscreen QSG_RHI_BACKEND=software \
  ./.venv/bin/python scripts/run_quality_gates.py --mode=working-tree

# 2. Headless Diagnostic Self-Test (< 5 seconds)
QT_QPA_PLATFORM=offscreen QSG_RHI_BACKEND=software \
  ./.venv/bin/python -m interfaces.desktop.self_test

# 3. Full Configured Test Suite (~2.5 minutes)
QT_QPA_PLATFORM=offscreen QSG_RHI_BACKEND=software \
  ./.venv/bin/python -m pytest tests/ -q -rs
```

### 10.4 GitHub Pull Request & Merge Gating

All pull requests targeting `main` must:
1. Be based on the latest `origin/main` (`strict: true`). Developers must run `git checkout main && git pull origin main` before branching and ensure feature branches are rebased on latest `origin/main`.
2. Pass the four mandatory CI checks:
   - `quality-gates`
   - `secret-scan`
   - `test-linux`
   - `test-windows`
3. Merge using a linear history method (**Squash and merge** or **Rebase and merge**).
