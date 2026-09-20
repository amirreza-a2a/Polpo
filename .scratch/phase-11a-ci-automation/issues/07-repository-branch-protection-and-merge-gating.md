# 07: Repository Branch Protection & Merge-Gating Rules

**What to build:** Enforce repository-level branch protection on `main` via the GitHub REST API / `gh` CLI, ensuring that all Tier 1 CI checks must pass and branches must be up to date before pull requests can be merged into `main`. Workflow YAML files define job executions, but only GitHub repository protection settings enforce merge blocking. This ticket executes the actual configuration against `amirreza-a2a/Polpo` using authenticated `gh api`, verifies the resulting protection policy, and authors `docs/ci-repository-rules.md` documenting the rules and rollback procedures.

**Blocked by:** 06 (Secret Scanning Gate & Platform Support Documentation)

**Status:** ready-for-agent

## Scope

- Configure Branch Protection on `main` using authenticated `gh api`:
  - Target: `PUT /repos/amirreza-a2a/Polpo/branches/main/protection`
  - Required Status Checks (`required_status_checks`):
    - `strict: true` (Enforces "Require branches to be up to date before merging" — prevents semantic merge races by requiring PR branches to be rebased/updated to the latest `main` tip before merging).
    - `contexts`: Exactly matching the stable job names in `.github/workflows/ci.yml`:
      1. `"quality-gates"` (Hygiene, compileall, AST architecture invariants)
      2. `"test-linux"` (Full test suite on `ubuntu-24.04`, Python 3.12)
      3. `"test-windows"` (Full test suite on `windows-2025`, Python 3.12)
      4. `"secret-scan"` (Gitleaks credential scan)
  - Administrator Enforcement (`enforce_admins: true`):
    - "Do not allow bypassing the above settings" — applies restrictions uniformly to repository administrators to prevent accidental unverified pushes.
  - Linear History (`required_linear_history: true`):
    - Prevents merge commits on `main`, ensuring clean rebase or squash-and-merge history.
  - Force Push & Deletion Restrictions:
    - `allow_force_pushes: false`
    - `allow_deletions: false`
  - Merge Queue:
    - Explicitly **DEFERRED** (not enabled; standard strict branch protection is sufficient for current project concurrency).
- Implementation Automation:
  - Create `scripts/configure_branch_protection.sh` automating the idempotent `gh api` call and subsequent verification query.
  - Execute the script using the existing authenticated `gh` CLI session (`repo` scope).
  - Verify with `gh api repos/amirreza-a2a/Polpo/branches/main/protection` that HTTP 200 is returned and all 4 contexts are enforced.
- Documentation:
  - Author `docs/ci-repository-rules.md` capturing the configuration payload, GUI configuration steps, and emergency rollback procedures.

## Non-Goals

- Assuming workflow YAML alone can block merges without repository-level enforcement.
- Enabling GitHub Merge Queue.
- Requiring multiple human pull-request approvals (which would block single-maintainer / agentic workflows).

## Files Likely Impacted

- Create: `scripts/configure_branch_protection.sh`
- Create: `docs/ci-repository-rules.md`

## Acceptance Criteria

- [ ] `scripts/configure_branch_protection.sh` exists, is executable, and contains the idempotent `gh api` payload for `main`.
- [ ] Branch protection is actively configured on `amirreza-a2a/Polpo` for the `main` branch.
- [ ] Querying `gh api repos/amirreza-a2a/Polpo/branches/main/protection` returns HTTP 200 with:
  - `strict: true`
  - `contexts` containing exactly `["quality-gates", "test-linux", "test-windows", "secret-scan"]`
  - `enforce_admins.enabled: true`
  - `allow_force_pushes.enabled: false`
  - `allow_deletions.enabled: false`
- [ ] `docs/ci-repository-rules.md` accurately documents the protection settings, command payloads, and rollback process.
- [ ] Verification confirms that an open PR cannot be merged if any required status check is missing or failing.

## Verification / Tests Required

- Execute `scripts/configure_branch_protection.sh` in the terminal.
- Run `gh api repos/amirreza-a2a/Polpo/branches/main/protection --jq '{strict: .required_status_checks.strict, contexts: .required_status_checks.contexts, enforce_admins: .enforce_admins.enabled}'` and assert matching output.
- Check `git status` to ensure only intended files were created.

## Failure & Rollback Considerations

- If an emergency hotfix requires direct push access during an operational outage, protection can be temporarily adjusted using `gh api -X DELETE /repos/amirreza-a2a/Polpo/branches/main/protection` by a repository administrator, then immediately reapplied via `scripts/configure_branch_protection.sh`.
