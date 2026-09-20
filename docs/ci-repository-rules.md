# Continuous Integration & Repository Merge-Gating Rules

This document outlines the GitHub branch protection and automated merge-gating policy enforced on the canonical `main` branch of repository `amirreza-a2a/Polpo`.

---

## 1. Purpose

Automated CI workflows (`.github/workflows/ci.yml`) define test, lint, and security checks executed on code changes. However, GitHub Actions YAML alone does **not** prevent branches from being merged or pushed directly.

GitHub classic branch protection provides the server-side enforcement layer that strictly blocks merges to `main` until all required checks have completed successfully and linear history requirements are met.

This policy is explicitly designed for a desktop-first, single-maintainer and autonomous agentic workflow:
* **Fully automated gating**: Merges are gated on rigorous, deterministic automated verification rather than human approval bottlenecks.
* **No mandatory human review gates**: Pull requests do not require a mandatory human review count to merge once all automated gates pass.
* **Zero admin bypass**: Repository administrators are subject to the configured branch-protection enforcement and cannot bypass it while the protection policy remains active.

---

## 2. Current Protected Branch

* **Branch**: `main`
* **Protection Type**: GitHub Classic Branch Protection Rule

---

## 3. Required Status Checks

The following **four exact checks** from the blocking CI matrix (`.github/workflows/ci.yml`) must pass before any pull request can be merged into `main`:

1. `quality-gates`:
   * **Runner**: `ubuntu-24.04` (Python 3.12)
   * **Scope**: Diff and whitespace hygiene (`git diff --check`), clean Python bytecode compilation across all source directories (`core`, `application`, `infrastructure`, `interfaces`, `tests`), and strict AST-based architectural dependency invariants.
2. `test-linux`:
   * **Runner**: `ubuntu-24.04` (Python 3.12, `QT_QPA_PLATFORM: offscreen`)
   * **Scope**: Full test suite execution across all unit and integration tests on Linux.
3. `test-windows`:
   * **Runner**: `windows-2025` (Windows Server 2025 Datacenter x64, Python 3.12, `QT_QPA_PLATFORM: offscreen`)
   * **Scope**: Full test suite execution across all unit and integration tests on native Windows Server, ensuring cross-platform path handling, file locking semantics, and thread safety.
4. `secret-scan`:
   * **Runner**: `ubuntu-24.04` (Gitleaks Action pinned to immutable SHA `e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e # v3.0.0`)
   * **Scope**: Git history scanning using `.gitleaks.toml` configuration to ensure no plaintext secrets, tokens, or live API credentials are committed to the repository.

No additional required status checks exist.

---

## 4. Up-to-Date Requirement (`strict: true`)

* **Setting**: `strict: true`
* **Operational Behavior**: A pull request branch must be strictly up to date with the latest commit on `main` before it can be merged. If `main` advances while a PR is open, the PR branch must incorporate the latest changes (via rebase or merge from `main`) and re-run CI checks to ensure that concurrent integrations do not introduce regressions.

---

## 5. Administrator Enforcement (`enforce_admins: true`)

* **Setting**: `enforce_admins: true`
* **Operational Behavior**: Repository administrators are subject to the configured branch-protection enforcement and cannot bypass it while the protection policy remains active. All merges to `main` must strictly satisfy every required status check and linear history constraint without exception.

---

## 6. Linear History (`required_linear_history: true`)

* **Setting**: `required_linear_history: true`
* **Operational Behavior**: Merge commits (`git merge --no-ff`) are rejected by GitHub on branch `main`. All pull request merges must maintain a clean, linear git history.
* **Supported Merge Methods**: The repository is configured to allow **Squash and merge** and **Rebase and merge**, both of which produce linear history compliant with this rule.

---

## 7. Force Push & Deletion Restrictions

* **Force Pushes**: `allow_force_pushes: false`
  * Force pushes (`git push --force` or `--force-with-lease`) to `main` are strictly forbidden and blocked by GitHub.
* **Deletions**: `allow_deletions: false`
  * The `main` branch cannot be deleted while this protection policy remains active.

---

## 8. Human Approval Policy

* **Setting**: `required_pull_request_reviews: null`
* **Operational Behavior**: Pull request reviews are not required. Automated bots, maintainers, and agentic workflows can merge PRs as soon as all four automated status checks pass and the branch is up to date with `main`.

---

## 9. Restrictions Policy

* **Setting**: `restrictions: null`
* **Operational Behavior**: Push restrictions are not configured; push access is governed by standard repository write permissions subject to required status checks and branch protection rules.

---

## 10. Merge Queue Policy

* **Status**: `Merge Queue: DEFERRED`
* **Operational Behavior**: GitHub Merge Queue is deferred and not enabled. Integrations follow standard pull request gating.

---

## 11. Applied Configuration Payload

The branch protection policy is defined and applied via the GitHub REST API (`PUT /repos/amirreza-a2a/Polpo/branches/main/protection`):

```json
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "quality-gates",
      "test-linux",
      "test-windows",
      "secret-scan"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
```

### Automation Script & Prerequisites

The policy application is automated via [`scripts/configure_branch_protection.sh`](../scripts/configure_branch_protection.sh).

**Prerequisites**:
* `bash` (v4+)
* `gh` (GitHub CLI, authenticated with repository admin privileges)
* `jq` (command-line JSON processor for deterministic assertions)

To apply or reconcile this configuration programmatically:

```bash
./scripts/configure_branch_protection.sh
```

### Verification Query

To verify all active branch protection attributes on `main` using the GitHub CLI:

```bash
gh api \
  repos/amirreza-a2a/Polpo/branches/main/protection \
  --jq '{
    strict: .required_status_checks.strict,
    contexts: .required_status_checks.contexts,
    enforce_admins: .enforce_admins.enabled,
    required_linear_history: .required_linear_history.enabled,
    allow_force_pushes: .allow_force_pushes.enabled,
    allow_deletions: .allow_deletions.enabled,
    required_pull_request_reviews: .required_pull_request_reviews,
    restrictions: .restrictions
  }'
```

Expected output:

```json
{
  "allow_deletions": false,
  "allow_force_pushes": false,
  "contexts": [
    "quality-gates",
    "test-linux",
    "test-windows",
    "secret-scan"
  ],
  "enforce_admins": true,
  "required_linear_history": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "strict": true
}
```

---

## 12. Negative PR Verification Note

The branch-protection API was verified live and the four required checks are enforced. No open PR existed during verification, so no disposable failing/missing-check PR was created solely for this ticket. GitHub's protected-branch semantics require required checks to pass before merging.

---

## 13. GitHub Web UI Configuration Reference

To configure or inspect these settings manually through the GitHub Web UI:

1. Navigate to **Repository Settings** → **Branches** (under *Code and automation*).
2. Under **Branch protection rules**, locate or select **Add classic branch protection rule**.
3. Set **Branch name pattern**: `main`.
4. Check **Require status checks to pass before merging**:
   * Check **Require branches to be up to date before merging** (`strict: true`).
   * Search for and select the four required status checks:
     * `quality-gates`
     * `test-linux`
     * `test-windows`
     * `secret-scan`
5. Check **Require linear history**.
6. Do **not** check *Require a pull request before merging* (leave `required_pull_request_reviews` null).
7. Check **Do not allow bypassing the above settings** (`enforce_admins: true`).
8. Ensure **Allow force pushes** remains **unchecked** (`allow_force_pushes: false`).
9. Ensure **Allow deletions** remains **unchecked** (`allow_deletions: false`).
10. Ensure **Require merge queue** remains **unchecked**.
11. Click **Save changes**.

---

## 14. Emergency Rollback Procedure

In the event of an emergency where branch protection must be temporarily removed (for example, repository recovery or disaster recovery scenarios):

### Emergency Rollback Command

```bash
gh api \
  -X DELETE \
  repos/amirreza-a2a/Polpo/branches/main/protection
```

> [!WARNING]
> Emergency rollback immediately removes all merge gates and protections from `main`. Rollback is strictly an emergency administrative operation and must never be used during normal development.

### Immediate Reapplication

Once the emergency is resolved, immediately restore full branch protection by running:

```bash
./scripts/configure_branch_protection.sh
```
