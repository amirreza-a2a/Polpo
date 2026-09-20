# 03: Blocking Linux CI Workflow (ubuntu-24.04)

**What to build:** Implement the primary continuous integration workflow (`.github/workflows/ci.yml`) on the Tier 1 reference Linux distribution (`ubuntu-24.04` x86_64) with Python 3.12. The workflow executes on all pull requests targeting `main` and pushes to `main`, enforcing immutable third-party action pinning (full 40-character SHAs with verified release tags), exact commit-range quality gating via `scripts/run_quality_gates.py`, and full headless test suite execution under Linux. Both jobs are strictly merge-blocking for integration into `main`.

**Blocked by:** 02 (Reusable Quality Gates & Hygiene Validation Runner)

**Status:** ready-for-agent

## Scope

- Create `.github/workflows/ci.yml` triggered on:
  - `pull_request`: branches: `[main]`
  - `push`: branches: `[main]`
- Enforce strict third-party action pinning to immutable 40-character commit SHAs with verified release tags:
  - `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`
  - `actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0`
- Stable, unique job identifiers and display names:
  - Job ID `quality-gates` (name: `quality-gates`):
    - Runs on explicit runner label `ubuntu-24.04` (x86_64 architecture).
    - Check out repository with `fetch-depth: 0` to enable git commit-range diff traversal.
    - Set up Python 3.12 using `actions/setup-python` with `cache: 'pip'`.
    - Install `requirements.txt` and `requirements-dev.txt`.
    - Pass exact event SHAs to `scripts/run_quality_gates.py`:
      - For PRs: `--mode=commit-range --base ${{ github.event.pull_request.base.sha }} --head ${{ github.event.pull_request.head.sha }}`
      - For pushes: `--mode=commit-range --base ${{ github.event.before }} --head ${{ github.event.after }}` (with automated zero-SHA fallback).
  - Job ID `test-linux` (name: `test-linux`):
    - Runs on explicit runner label `ubuntu-24.04` (x86_64 architecture).
    - `needs: quality-gates` (fail fast if hygiene or invariants fail).
    - Install Linux headless Qt system dependencies (`libgl1`, `libegl1`, `libxkbcommon-x11-0`, `libdbus-1-3`) via `sudo apt-get`.
    - Set environment variable `QT_QPA_PLATFORM: offscreen`.
    - Execute `pytest -q` to run all 922+ tests.
- Workflow permissions set to minimal least-privilege: `permissions: contents: read`.

## Non-Goals

- Using floating action tags (`@v4`, `@v5`, `@v7`, `@main`).
- Multi-distro Linux matrix on PRs (Ubuntu 24.04 is the single reference distribution).
- Modifying production application code or dependencies.

## Files Likely Impacted

- Create: `.github/workflows/ci.yml`

## Acceptance Criteria

- [ ] `.github/workflows/ci.yml` exists and validates with valid GitHub Actions schema.
- [ ] All third-party actions (`actions/checkout`, `actions/setup-python`) are pinned to verified 40-character commit SHAs (`v7.0.1` and `v7.0.0`).
- [ ] Runner label is explicitly pinned to `ubuntu-24.04` (avoiding floating `-latest`).
- [ ] Job names are unique and stable: `quality-gates` and `test-linux`.
- [ ] Quality gate correctly receives commit-range SHAs for PR (`base.sha`..`head.sha`) and push (`before`..`after`) events.
- [ ] Headless Qt dependencies are installed without failure on `ubuntu-24.04`.
- [ ] `test-linux` executes `pytest -q` with `QT_QPA_PLATFORM: offscreen` and all 922+ tests pass.
- [ ] Any failure in `quality-gates` or `test-linux` results in a failing check status.

## Verification / Tests Required

- Validate workflow syntax with an action linter or manual schema inspection.
- Verify commit-range SHA evaluation logic for push and pull request scenarios.
- Verify headless test execution completes with exit code 0.

## Cross-Platform Implications & Linux Policy

- `ubuntu-24.04` represents the primary POSIX reference environment. Documented in workflow comments that Ubuntu 24.04 serves as reference and does not represent continuous automated testing of all Linux distributions.

## Failure & Rollback Considerations

- If apt package names differ on `ubuntu-24.04`, adjust package names to ensure required OpenGL/EGL shared libraries are present.
- Rollback: Revert `.github/workflows/ci.yml`.
