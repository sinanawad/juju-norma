#!/usr/bin/env bash
# charmhub-edge.sh — shared by juju-norma and juju-norma-k8s (keep identical).
#
# Helpers for the unattended latest/edge publish, keyed on the commit of the
# revision currently on edge. CharmHub records each revision's version, which the
# charm's `version` part sets to `git describe --always` of the packed commit
# ("3d2ff3a", "v0.1.0-16-g114d266", or "v0.1.0" exactly at a tag), so the live
# edge revision maps back to a commit in this history.
#
#   charmhub-edge.sh needs-publish <charm> <sha>
#     Print "true" or "false" (reason on stderr): must <sha> be published?
#     Compared with what is live on edge, not with the previous commit, so an
#     artifact change whose publish failed is still picked up by the next green
#     push, even when that push only touches docs. Changes confined to paths
#     that don't alter what consumers deploy (docs, tests, CI config, dev
#     tooling, artifacts never published to CharmHub) don't publish:
#     republishing identical bits only adds revision-number churn on edge. The
#     publish workflows live under .github/ too, so a change that only alters
#     how the artifact is built (version stamp, platforms) waits for the next
#     shipping change; dispatch the publish workflow to force it. An unknown
#     baseline publishes (fail open: the upload step reports real store errors).
#
#   charmhub-edge.sh would-roll-back <charm> <sha>
#     Print "true" if latest/edge already carries a commit newer than <sha>, so
#     publishing <sha> would roll edge back (e.g. re-running an old run after
#     newer commits shipped); "false" otherwise, including when edge is <sha>.
#
#   charmhub-edge.sh tag-revision <charm> <sha>
#     Wait for latest/edge to carry <sha>, then tag <sha> `rev<N>` (lightweight,
#     through the GitHub API: needs GH_TOKEN with contents: write, and GH_REPO).
#     Idempotent; refuses to move an existing tag that points elsewhere.
#
# EDGE_VERSION (and EDGE_REVISION) override the CharmHub lookup for dry runs;
# EDGE_WAIT_ATTEMPTS (default 20, 15 s apart) bounds tag-revision's wait.
# Run inside a full clone (fetch-depth: 0).

set -euo pipefail

NON_ARTIFACT=(
  '*.md' 'docs/' 'specs/' 'scripts/' 'tests/' '.github/' '.specify/' '.claude/'
  '.gitignore'
  'subordinate/'            # machine: the subordinate ships only in GitHub Releases
  'charmcraft-sudoer.yaml'  # k8s: the sudoer variant ships only in GitHub Releases
)

# "<revision> <version>" of latest/edge, or nothing.
edge_info() {
  if [[ -n "${EDGE_VERSION+set}" ]]; then
    echo "${EDGE_REVISION:-0} ${EDGE_VERSION}"
    return
  fi
  curl -fsS --retry 3 --connect-timeout 10 --max-time 30 \
    "https://api.charmhub.io/v2/charms/info/$1?fields=channel-map.revision.revision,channel-map.revision.version" |
    jq -r '[.["channel-map"][] | select(.channel.track == "latest" and .channel.risk == "edge")
            | "\(.revision.revision) \(.revision.version)"][0] // empty'
}

# The full commit a recorded version maps to, or nothing.
version_commit() {
  local ref="$1"
  if [[ "$ref" =~ -g([0-9a-f]{7,40})$ ]]; then
    ref="${BASH_REMATCH[1]}"
  fi
  [[ -n "$ref" ]] || return 0
  git rev-parse --verify --quiet "${ref}^{commit}" || true
}

needs_publish() {
  local charm="$1" sha info version base changed
  sha="$(git rev-parse --verify "$2^{commit}")"
  info="$(edge_info "$charm")" || info=""
  version="${info#* }"
  base="$(version_commit "$version")"

  publish() { echo "publish: $*" >&2; echo true; exit 0; }
  skip() { echo "skip: $*" >&2; echo false; exit 0; }

  if [[ -z "$base" ]]; then
    publish "edge version '${version:-<none>}' does not map to a commit in this history"
  fi
  if [[ "$base" == "$sha" ]]; then
    skip "latest/edge already carries ${sha:0:7}"
  fi
  if git merge-base --is-ancestor "$sha" "$base"; then
    skip "latest/edge carries ${base:0:7}, which is newer than ${sha:0:7}"
  fi
  if ! git merge-base --is-ancestor "$base" "$sha"; then
    publish "edge commit ${base:0:7} is not an ancestor of ${sha:0:7} (history diverged)"
  fi
  changed="$(git diff --name-only "$base" "$sha" -- . "${NON_ARTIFACT[@]/#/:(exclude)}")"
  if [[ -n "$changed" ]]; then
    publish "artifact paths changed since edge commit ${base:0:7}: $(echo "$changed" | head -5 | paste -sd' ')"
  fi
  skip "only non-artifact paths changed since edge commit ${base:0:7}"
}

would_roll_back() {
  local charm="$1" sha info base
  sha="$(git rev-parse --verify "$2^{commit}")"
  info="$(edge_info "$charm")" || info=""
  base="$(version_commit "${info#* }")"
  if [[ -n "$base" && "$base" != "$sha" ]] && git merge-base --is-ancestor "$sha" "$base"; then
    echo "latest/edge carries ${base:0:7}, which is newer than ${sha:0:7}" >&2
    echo true
  else
    echo false
  fi
}

tag_revision() {
  local charm="$1" sha info rev="" version existing
  sha="$(git rev-parse --verify "$2^{commit}")"
  : "${GH_REPO:?GH_REPO must be set}"
  for _ in $(seq 1 "${EDGE_WAIT_ATTEMPTS:-20}"); do
    info="$(edge_info "$charm")" || info=""
    version="${info#* }"
    if [[ "${info%% *}" =~ ^[0-9]+$ && "$(version_commit "$version")" == "$sha" ]]; then
      rev="${info%% *}"
      break
    fi
    echo "latest/edge shows '${info:-<nothing>}', not ${sha:0:7} yet; retrying in 15s" >&2
    sleep 15
  done
  if [[ -z "$rev" ]]; then
    echo "latest/edge never showed ${sha:0:7}; not tagging" >&2
    exit 1
  fi
  existing="$(gh api "repos/${GH_REPO}/git/ref/tags/rev${rev}" --jq .object.sha 2>/dev/null || true)"
  if [[ "$existing" == "$sha" ]]; then
    echo "rev${rev} already tags ${sha:0:7}" >&2
    return 0
  fi
  if [[ -n "$existing" ]]; then
    echo "rev${rev} already exists on ${existing:0:7}; not moving it" >&2
    exit 1
  fi
  gh api "repos/${GH_REPO}/git/refs" -f ref="refs/tags/rev${rev}" -f sha="$sha" >/dev/null
  echo "tagged ${sha:0:7} as rev${rev}" >&2
}

case "${1:-}" in
  needs-publish) needs_publish "${@:2}" ;;
  would-roll-back) would_roll_back "${@:2}" ;;
  tag-revision) tag_revision "${@:2}" ;;
  *)
    echo "usage: $0 needs-publish|would-roll-back|tag-revision <charm> <sha>" >&2
    exit 2
    ;;
esac
