#!/usr/bin/env bash
# ==============================================================================
# scripts/configure_branch_protection.sh
#
# Configures and verifies GitHub classic branch protection for branch 'main'
# in repository 'amirreza-a2a/Polpo'.
#
# Idempotent: can be safely re-run to enforce or reconcile protection rules.
#
# Prerequisites:
#   - bash (v4+)
#   - gh (GitHub CLI, authenticated with repository admin privileges)
#   - jq (JSON processor)
# ==============================================================================

set -euo pipefail

REPO="amirreza-a2a/Polpo"
BRANCH="main"
API_VERSION="2026-03-10"

# 1. Environment & tool prerequisites validation
command -v gh >/dev/null 2>&1 || {
  echo "ERROR: 'gh' (GitHub CLI) is required but not installed or not in PATH." >&2
  exit 1
}

command -v jq >/dev/null 2>&1 || {
  echo "ERROR: 'jq' is required but not installed or not in PATH." >&2
  exit 1
}

echo "Configuring branch protection for ${REPO}:${BRANCH}..."

# 2. Apply branch protection via GitHub REST API PUT endpoint
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: ${API_VERSION}" \
  "repos/${REPO}/branches/${BRANCH}/protection" \
  --input - <<'EOF'
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
EOF

echo "Branch protection PUT request successful."
echo "Verifying applied branch protection..."

# 3. Query applied protection to verify exact policy enforcement
RESPONSE="$(gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: ${API_VERSION}" \
  "repos/${REPO}/branches/${BRANCH}/protection")"

# 4. Extract and assert all policy attributes
STRICT="$(echo "${RESPONSE}" | jq -r '.required_status_checks.strict')"
ACTUAL_CONTEXTS="$(echo "${RESPONSE}" | jq -c '.required_status_checks.contexts')"
ENFORCE_ADMINS="$(echo "${RESPONSE}" | jq -r '.enforce_admins.enabled')"
LINEAR_HISTORY="$(echo "${RESPONSE}" | jq -r '.required_linear_history.enabled')"
ALLOW_FORCE_PUSHES="$(echo "${RESPONSE}" | jq -r '.allow_force_pushes.enabled')"
ALLOW_DELETIONS="$(echo "${RESPONSE}" | jq -r '.allow_deletions.enabled')"
PULL_REQUEST_REVIEWS="$(echo "${RESPONSE}" | jq -c '.required_pull_request_reviews')"
RESTRICTIONS="$(echo "${RESPONSE}" | jq -c '.restrictions')"

EXPECTED_CONTEXTS='["quality-gates","test-linux","test-windows","secret-scan"]'
FAILURES=0

if [[ "${STRICT}" != "true" ]]; then
  echo "ERROR: required_status_checks.strict is '${STRICT}', expected 'true'" >&2
  FAILURES=$((FAILURES + 1))
fi

if [[ "${ACTUAL_CONTEXTS}" != "${EXPECTED_CONTEXTS}" ]]; then
  echo "ERROR: required_status_checks.contexts is '${ACTUAL_CONTEXTS}', expected exact array '${EXPECTED_CONTEXTS}'" >&2
  FAILURES=$((FAILURES + 1))
fi

if [[ "${ENFORCE_ADMINS}" != "true" ]]; then
  echo "ERROR: enforce_admins.enabled is '${ENFORCE_ADMINS}', expected 'true'" >&2
  FAILURES=$((FAILURES + 1))
fi

if [[ "${LINEAR_HISTORY}" != "true" ]]; then
  echo "ERROR: required_linear_history.enabled is '${LINEAR_HISTORY}', expected 'true'" >&2
  FAILURES=$((FAILURES + 1))
fi

if [[ "${ALLOW_FORCE_PUSHES}" != "false" ]]; then
  echo "ERROR: allow_force_pushes.enabled is '${ALLOW_FORCE_PUSHES}', expected 'false'" >&2
  FAILURES=$((FAILURES + 1))
fi

if [[ "${ALLOW_DELETIONS}" != "false" ]]; then
  echo "ERROR: allow_deletions.enabled is '${ALLOW_DELETIONS}', expected 'false'" >&2
  FAILURES=$((FAILURES + 1))
fi

if [[ "${PULL_REQUEST_REVIEWS}" != "null" ]]; then
  echo "ERROR: required_pull_request_reviews is '${PULL_REQUEST_REVIEWS}', expected 'null'" >&2
  FAILURES=$((FAILURES + 1))
fi

if [[ "${RESTRICTIONS}" != "null" ]]; then
  echo "ERROR: restrictions is '${RESTRICTIONS}', expected 'null'" >&2
  FAILURES=$((FAILURES + 1))
fi

if [[ ${FAILURES} -gt 0 ]]; then
  echo "Verification FAILED with ${FAILURES} error(s)." >&2
  exit 1
fi

echo "================================================================="
echo "Branch protection verified successfully for ${REPO}:${BRANCH}"
echo "================================================================="
echo "  strict:                        ${STRICT}"
echo "  contexts:                      ${ACTUAL_CONTEXTS}"
echo "  enforce_admins:                ${ENFORCE_ADMINS}"
echo "  required_linear_history:       ${LINEAR_HISTORY}"
echo "  allow_force_pushes:            ${ALLOW_FORCE_PUSHES}"
echo "  allow_deletions:               ${ALLOW_DELETIONS}"
echo "  required_pull_request_reviews: ${PULL_REQUEST_REVIEWS}"
echo "  restrictions:                  ${RESTRICTIONS}"
echo "================================================================="
