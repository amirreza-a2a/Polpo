# 04: Blocking Windows CI Matrix Runner (windows-2025)

**What to build:** Add a native Windows runner job (`test-windows`) to `.github/workflows/ci.yml` using the explicit runner label `windows-2025` (Windows Server 2025 Datacenter x86_64) with Python 3.12 and pinned third-party actions (`v7.0.1` and `v7.0.0`). This job runs the full 922+ test suite in parallel with `test-linux` after `quality-gates` passes. It validates Win32 path normalization, RFC 8089 drive-letter URIs, single-pass percent-decoding, and NTFS mandatory file locking, serving as a mandatory, merge-blocking PR gate.

**Blocked by:** 03 (Blocking Linux CI Workflow)

**Status:** ready-for-agent

## Scope

- In `.github/workflows/ci.yml`, add job `test-windows`:
  - `runs-on: windows-2025` (explicit, stable Windows Server 2025 Datacenter x86_64 image; avoiding floating `-latest` alias).
  - `name: test-windows` (stable, unique check name).
  - `needs: quality-gates` (runs concurrently with `test-linux`).
  - Action pinning with full 40-character SHAs:
    - `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`
    - `actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0` with `cache: 'pip'`.
  - Install dependencies: `requirements.txt` and `requirements-dev.txt`.
  - Set environment variable: `QT_QPA_PLATFORM: offscreen`.
  - Run full test suite: `pytest -q`.
- Verify and resolve any Windows-specific test teardown issues where unclosed SQLite connections or file descriptors trigger NTFS `PermissionError` during fixture cleanup.

## Non-Goals

- Conflating Windows Server CI with consumer Windows 10/11 desktop verification (documented explicitly as the Windows OS family CI environment).
- Modifying production application code or path-resolution logic in `composition.py`.
- Running Windows builds on non-blocking / advisory mode (Windows testing is strictly merge-blocking).

## Files Likely Impacted

- Modify: `.github/workflows/ci.yml`
- Potential test fixture fixes in `tests/` if unclosed file handles cause Windows-only cleanup failures.

## Acceptance Criteria

- [ ] `.github/workflows/ci.yml` defines the stable, unique job `test-windows` running on `windows-2025` (x86_64) with Python 3.12.
- [ ] All actions in the job are pinned to verified 40-character commit SHAs (`v7.0.1` and `v7.0.0`).
- [ ] `test-windows` and `test-linux` execute in parallel upon successful completion of `quality-gates`.
- [ ] Full test suite (922+ tests) passes on the native Windows runner.
- [ ] NTFS mandatory file locking is respected: zero `PermissionError` or `WinError 32` ("file in use by another process") during temp directory teardown.
- [ ] A failure in `test-windows` causes the GitHub Actions check to fail, blocking PR merge into `master`.

## Verification / Tests Required

- Validate workflow syntax with `actionlint`.
- Audit test teardown in SQLite and storage tests to verify all connections and file streams are explicitly closed before directory cleanup.

## Cross-Platform Implications & Terminology

- *Platform Family Verification*: The `windows-2025` runner exercises the Win32 subsystem, NTFS mandatory locking, path separators, and `file:///C:/...` URI handling. It validates core runtime safety for the Windows platform family.
- *Desktop Disclaimer*: Documented in workflow comments and project docs that Windows Server CI does not test consumer desktop shell integration (taskbar, notifications, Start Menu) or physical hardware GPU display drivers.

## Failure & Rollback Considerations

- If any test fails under Windows due to file locking in fixtures, fix the test fixture cleanup logic. If `windows-2025` runner image experiences upstream provisioning delays, `windows-2022` serves as a pinned fallback.
