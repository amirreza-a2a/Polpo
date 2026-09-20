# 05: Periodic & Pre-Release Validation Workflow (macOS & Python Compatibility)

**What to build:** Create a dedicated secondary GitHub Actions workflow (`.github/workflows/periodic-validation.yml`) to execute Tier 2 platform verification without impacting the sub-2-minute everyday PR feedback loop. This workflow executes: (1) native macOS Apple Silicon testing on the explicit `macos-15` (ARM64) runner with Python 3.12, and (2) Python 3.10 and 3.11 backward-compatibility testing on the reference Linux runner (`ubuntu-24.04`). The workflow provides three distinct execution triggers: scheduled weekly runs, post-tag verification on `v*` pushes, and a manual `workflow_dispatch` mechanism designed specifically for pre-release validation *before* publishing official release tags.

```text
Tier 2
├── macOS 15 ARM64 / Python 3.12
├── Ubuntu 24.04 x86_64 / Python 3.10
└── Ubuntu 24.04 x86_64 / Python 3.11
```

**Blocked by:** 03 (Blocking Linux CI Workflow)

**Status:** ready-for-agent

## Scope

- Create `.github/workflows/periodic-validation.yml` with explicit, differentiated triggers:
  1. `schedule`: Weekly regression sweep (`cron: '0 3 * * 1'`, Mondays at 03:00 UTC) on `main`.
  2. `workflow_dispatch`: Manual trigger with inputs (`ref`: branch, commit SHA, or release candidate to test; `run_macos`: boolean; `run_python_matrix`: boolean). Enables full pre-release validation on a release branch *prior* to tagging or releasing.
  3. `push: tags: ['v*']`: Post-tag verification ensuring that any pushed release tag is fully validated across all Tier 2 environments.
- Action pinning to immutable 40-character commit SHAs with verified release tags:
  - `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`
  - `actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0` with `cache: 'pip'`.
- Job 1: `test-macos` (native macOS Apple Silicon / ARM64, GitHub Actions runner label: `macos-15`, Python 3.12):
  - GitHub Actions runner label: `runs-on: macos-15` (avoiding floating `-latest` or `macos-26`).
  - Sets up Python 3.12 with pip cache.
  - Installs `requirements.txt` and `requirements-dev.txt`.
  - Sets environment variable `QT_QPA_PLATFORM: offscreen`.
  - Executes full `pytest -q` suite across all tests under native Darwin kernel, APFS case-preserving filesystem, and macOS PySide6 Cocoa bindings.
- Job 2: `test-python-matrix` (`ubuntu-24.04` x86_64, matrix: `[3.10, 3.11]`):
  - Installs Linux headless system libraries.
  - Executes `pytest -q` under Python 3.10 and 3.11 to catch any unintentional use of 3.12-only syntax or standard library APIs.

## Non-Goals

- Blocking everyday pull requests (Tier 2 environments are decoupled from the daily PR gate to preserve developer velocity).
- Claiming verification of older macOS versions (older macOS generations, including macOS 14 and earlier, are not part of this Tier-2 validation target) or Intel x86_64 Mac hardware.
- Testing Python 3.10/3.11 across all OSes (compatibility matrix is scoped to the Linux reference runner).

## Files Likely Impacted

- Create: `.github/workflows/periodic-validation.yml`

## Acceptance Criteria

- [ ] `.github/workflows/periodic-validation.yml` validates cleanly with standard GitHub Actions schema.
- [ ] Workflow explicitly distinguishes between weekly cron, manual pre-release dispatch, and post-tag verification.
- [ ] `workflow_dispatch` allows running the workflow on any specified Git ref prior to creating a release tag.
- [ ] Runner label for macOS is explicitly pinned to `macos-15` (Apple Silicon ARM64; avoiding floating `-latest` or `macos-26`).
- [ ] `test-macos` executes all tests in headless offscreen mode without failure.
- [ ] `test-python-matrix` verifies Python 3.10 and 3.11 on `ubuntu-24.04`.
- [ ] Third-party actions are pinned to verified 40-character commit SHAs (`v7.0.1` and `v7.0.0`).

## Engineering Rationale & Cross-Platform Implications

- *Measured Engineering Trade-off*: Linux and Windows provide the highest-value differential coverage on blocking PRs (POSIX pathing on Linux; NTFS mandatory locking and drive-letter URIs on Windows). While macOS shares POSIX pathing with Linux and case-insensitivity with Windows, native macOS testing remains essential periodically and before releases to verify Darwin PySide6 platform integration, Mach-O binary wheel loading, and APFS behavior.
- *Decoupled Execution*: Decoupling macOS and Python 3.10/3.11 from daily PRs maintains fast merge feedback while retaining thorough automated compatibility gates before releases.
- *Support Disclaimers*: Documentation acknowledges that `macos-15` verifies modern Apple Silicon Macs (macOS 15 Sequoia ARM64); older macOS generations, including macOS 14 and earlier, as well as legacy Intel hardware, are not part of this Tier-2 validation target.

## Failure & Rollback Considerations

- If Python 3.10 fails due to typing annotations (e.g., unquoted union syntax without `from __future__ import annotations`), fix the syntax while maintaining compatibility across `3.10 <= Python < 3.13`.
- Periodic validation workflow runs independently of `ci.yml` and will never block main PR merges.
