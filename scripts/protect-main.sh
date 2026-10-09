#!/usr/bin/env bash
# protect-main.sh — shared by juju-norma and juju-norma-k8s (keep identical).
#
# Apply branch protection to `main`:
#   - block force-push + branch deletion
#   - REQUIRE the fast, deterministic CI checks to pass before merge — this is
#     what makes `gh pr merge --auto` actually gate on green.
#
# Notes:
#   - The slow integration smoke job is intentionally NOT required (runner and
#     snap-store availability would block merges); verify it out-of-band.
#   - `enforce_admins:false` lets an admin override in emergencies, but
#     auto-merge still waits for the required checks.
#   - `required_pull_request_reviews:null` keeps solo self-merge working.
#   - `strict:false` avoids forcing every branch up-to-date before merge.
#   - Required check names are the CI job `name:`s, which differ per substrate
#     (ROCK vs workload binary, sudoer variant vs subordinate). Override with
#     REQUIRED_CHECKS='["A","B"]' if a job is renamed.
#   - Every required check must report on EVERY pull request: never put a
#     trigger-level paths filter on the CI workflow's pull_request event, or a
#     PR it filters out waits forever for checks that never run.
#   - This script is the source of truth: the PUT replaces the whole protection
#     setting, so rules added in the GitHub UI are reset on the next run.
#
# Usage: scripts/protect-main.sh [owner/repo] [branch]
# Requires: gh CLI authenticated with admin access on the target repository.

set -euo pipefail

REPO="${1:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
BRANCH="${2:-main}"

if [[ -z "${REQUIRED_CHECKS:-}" ]]; then
  case "$REPO" in
    */juju-norma-k8s)
      REQUIRED_CHECKS='["Lint", "Unit Tests", "Check Libraries", "Pack Charm", "Build ROCK"]' ;;
    */juju-norma)
      REQUIRED_CHECKS='["Lint", "Unit Tests", "Check Libraries", "Pack Charm (+ subordinate)", "Build workload"]' ;;
    *)
      echo "Unknown repo ${REPO}: set REQUIRED_CHECKS='[\"Job name\", ...]'" >&2
      exit 1 ;;
  esac
fi

echo "==> Applying branch protection to ${REPO}@${BRANCH}"
echo "    required checks: ${REQUIRED_CHECKS}"

gh api -X PUT "/repos/${REPO}/branches/${BRANCH}/protection" --input - >/dev/null <<EOF
{
  "required_status_checks": {
    "strict": false,
    "contexts": ${REQUIRED_CHECKS}
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF

echo "==> Verifying"
gh api "/repos/${REPO}/branches/${BRANCH}/protection" \
  --jq '{required_checks: .required_status_checks.contexts, strict: .required_status_checks.strict, allow_force_pushes: .allow_force_pushes.enabled, allow_deletions: .allow_deletions.enabled, enforce_admins: .enforce_admins.enabled}'

echo "==> Done. To undo: gh api -X DELETE /repos/${REPO}/branches/${BRANCH}/protection"
