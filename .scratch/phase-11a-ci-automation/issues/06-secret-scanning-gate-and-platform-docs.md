# 06: Secret Scanning Gate & Platform Support Documentation

**What to build:** Integrate an established automated secret scanner (`gitleaks/gitleaks-action`) pinned to immutable commit SHA `e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e` (`v3.0.0`) into `.github/workflows/ci.yml` with minimal read-only permissions to prevent plaintext secret leaks ([`AGENTS.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/AGENTS.md) Rules 10 & 11). Update [`README.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/README.md) and [`CONTEXT.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/CONTEXT.md) with the finalized 4-tier platform support policy, developer commands, CI status badges, and an explicit tracking note for the separate `platformdirs` desktop hardening task.

**Blocked by:** 04 (Blocking Windows CI Matrix Runner), 05 (Periodic & Pre-Release Validation Workflow)

**Status:** ready-for-agent

## Scope

- Automated Secret Scanning:
  - Add a dedicated `secret-scan` job (name: `secret-scan`) to `.github/workflows/ci.yml`.
  - Pin the action to its verified immutable commit SHA:
    `gitleaks/gitleaks-action@e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e # v3.0.0`
  - Restrict permissions to least-privilege: `permissions: contents: read`.
  - Fork PR behavior: Runs purely read-only without requiring privileged secrets, write tokens, or external API keys.
  - PR comments are explicitly disabled; failures report directly via the GitHub Actions job log and step summary, eliminating the need for `pull-requests: write` permissions.
  - False-positive handling: Add `.gitleaks.toml` configuration to allowlist known synthetic test hashes and mock tokens.
- Canonical Documentation Updates ([`README.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/README.md) and [`CONTEXT.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/CONTEXT.md)):
  - Document the formal 4-tier platform support model:
    - **Tier 1 (Continuously Tested / Merge-Blocking)**:
      - Ubuntu 24.04 LTS x64 (Python 3.12).
      - Windows Server 2025 x64 via `windows-2025` / Python 3.12 (Windows Server 2022 remains a non-continuously-tested compatibility target).
    - **Tier 2 (Supported / Periodically Verified)**:
      - macOS 15 Apple Silicon / ARM64 / Python 3.12 (explicitly `runs-on: macos-15`) via weekly scheduled runs, manual pre-release dispatch, and release candidate tags.
      - Python 3.10 and 3.11 backward compatibility on Ubuntu reference runner.
      - Modern glibc Linux distributions (Fedora 38+, Debian 12+, Arch Linux) supported by ABI runtime standard (not continuously tested in this phase).
    - **Tier 3 (Best-Effort)**:
      - macOS 12 & 13, legacy Intel x86_64 Mac hardware.
      - Windows Subsystem for Linux (WSL2).
      - Headless Linux without D-Bus (using `EncryptedFileCredentialStore` fallback).
    - **Tier 4 (Unsupported)**:
      - Musl-based Linux (Alpine), 32-bit OS, Windows < 10, macOS < 12, remote web/cloud server environments.
  - Explicitly distinguish what CI proves on Windows Server (Win32 kernel, NTFS locking, path normalization) vs. consumer Windows 10/11 desktop support claims (which additionally require interactive shell and display verification).
  - Explicitly distinguish that Ubuntu 24.04 is the CI reference distribution and does not imply continuous automated testing of all Linux distributions.
  - Clarify the macOS declared support floor (12+) vs. the current Tier-2 CI runner image (macOS 15 Apple Silicon via `macos-15`).
  - Document developer workflow commands: installing `requirements-dev.txt`, running `pytest`, and running `scripts/run_quality_gates.py`.
  - Add CI status badges linking to workflow runs.
- Tracking Note Creation:
  - Create `docs/debt/2026-09-20-platformdirs-data-path-migration.md` tracking the separate task to migrate [`interfaces/desktop/composition.py:67`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/composition.py#L67) default data paths to `platformdirs.user_data_dir("PolpoT")`.

## Non-Goals

- Writing a custom or bespoke regex secret scanner (established, maintained tools like `gitleaks` must be used).
- Modifying `composition.py` or altering production application data-path resolution in this phase.
- Adding unverified claims of continuous testing for Tier 2 or Tier 3 platforms.

## Files Likely Impacted

- Modify: `.github/workflows/ci.yml` (add gitleaks step)
- Create: `.gitleaks.toml`
- Modify: `README.md`
- Modify: `CONTEXT.md`
- Create: `docs/debt/2026-09-20-platformdirs-data-path-migration.md`

## Acceptance Criteria

- [ ] `.github/workflows/ci.yml` includes the `secret-scan` job pinned to `gitleaks/gitleaks-action@e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e # v3.0.0`.
- [ ] The `secret-scan` job runs with `permissions: contents: read` and does not require PR comment write permissions.
- [ ] Synthetic test tokens or mock hashes in `tests/` do not cause false-positive CI failures (allowlisted via `.gitleaks.toml`).
- [ ] [`README.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/README.md) accurately documents the 4-tier support model, platform distinctions, and developer commands.
- [ ] [`CONTEXT.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/CONTEXT.md) reflects the CI quality gates and platform verification boundaries.
- [ ] Windows Server CI is explicitly distinguished from Windows 10/11 desktop support.
- [ ] Ubuntu 24.04 is clearly designated as the reference CI platform, and other glibc distros as supported targets not continuously tested in this phase.
- [ ] Tracking document `docs/debt/2026-09-20-platformdirs-data-path-migration.md` exists and defines the scope of the future desktop data-path fix.
- [ ] `git diff --check` passes cleanly across all touched files.

## Verification / Tests Required

- Test `gitleaks` configuration locally or verify job syntax with `actionlint`.
- Verify markdown rendering and links in `README.md`, `CONTEXT.md`, and `docs/debt/`.
- Confirm zero changes to production python code.

## Cross-Platform Implications

- `gitleaks` executes natively in GitHub Actions without platform-specific dependencies.
- Clear documentation prevents platform compatibility assumptions by contributors and automated agents.

## Failure & Rollback Considerations

- If false positives occur with test mocks (e.g. dummy test API keys like `sk-test-1234`), define targeted allowlist rules in `.gitleaks.toml`.
- Documentation changes carry zero runtime risk.
