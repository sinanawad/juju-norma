#!/usr/bin/env bash
# secure-repo.sh — shared by juju-norma and juju-norma-k8s (keep identical).
#
# Turn on GitHub's vulnerability intake and scanning for the repository:
#   - private vulnerability reporting (the channel SECURITY.md points reporters to)
#   - Dependabot alerts, plus Dependabot security-update PRs
#   - CodeQL code scanning, default setup (languages auto-detected:
#     actions, go, python)
#
# Idempotent: re-running leaves already-enabled features as they are.
#
# Usage: scripts/secure-repo.sh [owner/repo]
# Requires: gh CLI authenticated with admin access on the target repository.

set -euo pipefail

REPO="${1:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"

echo "==> Enabling security features on ${REPO}"
gh api -X PUT "/repos/${REPO}/private-vulnerability-reporting" >/dev/null
gh api -X PUT "/repos/${REPO}/vulnerability-alerts" >/dev/null
gh api -X PUT "/repos/${REPO}/automated-security-fixes" >/dev/null
gh api -X PATCH "/repos/${REPO}/code-scanning/default-setup" \
  -f state=configured -f query_suite=default >/dev/null

echo "==> Verifying"
echo -n "private vulnerability reporting: "
gh api "/repos/${REPO}/private-vulnerability-reporting" --jq .enabled
echo -n "Dependabot alerts: "
if gh api "/repos/${REPO}/vulnerability-alerts" --silent 2>/dev/null; then
  echo enabled
else
  echo DISABLED
fi
echo -n "Dependabot security updates: "
gh api "/repos/${REPO}/automated-security-fixes" --jq .enabled
echo -n "CodeQL default setup: "
gh api "/repos/${REPO}/code-scanning/default-setup" --jq '"\(.state) \(.languages)"'
