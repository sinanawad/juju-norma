# Releasing juju-norma

How `juju-norma` is published to CharmHub and released. The model deliberately
mirrors how Juju's *own* CI consumes test charms (the `juju-qa-*` family): a charm
is published to a channel and consumers deploy it **by name from that channel** —
no revision-pinning ceremony, no provenance handshake. `juju-qa-test` itself ships
`2.0/`, `3.0/` tracks for version scoping; our `4` track is the same idea for the
Juju-4.x line.

## Channels (the `4` track)

| Channel | Who writes it | When |
|---|---|---|
| `4/edge` | `publish-edge.yaml` (automatic, on merge) | every merge to `main` (after CI is green) |
| `4/candidate` | `release-tag.yaml` (automatic, on `v*` tag) | on pushing a `v*` git tag — promotes the current `4/edge` |
| `4/stable` | `promote.yaml` (manual) | after a human vets `4/candidate` |

**Consumers (Juju 4.x CI) follow the channel — newest norma compatible with Juju 4:**

```bash
juju deploy juju-norma --channel 4/stable --resource norma-bin=<rev>   # or let CharmHub bind it
```

A specific test that needs byte-identical determinism pins a revision itself
(`juju deploy juju-norma --channel 4/stable --revision N`), exactly as
`tests/suites/refresh/refresh.sh` pins `juju-qa-test --revision 22`. That is the
*consumer's* per-test choice, not a release-chain obligation.

## One-time prerequisites

1. **Register the `4` track** for `juju-norma` on CharmHub. Tracks cannot be
   created from CI; do it once in the CharmHub web UI (the charm's *Settings →
   Tracks*, subject to the publisher guardrail) or via a Canonical request.
   **Until the `4` track exists, `publish-edge` / `release-tag` / `promote` will
   fail** (CharmHub rejects a release to an unknown track). Verify with
   `charmcraft status juju-norma` (the channel map lists existing tracks).
2. **`CHARMHUB_TOKEN` must carry `package-manage`** (promote/release need it;
   a lib-check-only token is `package-view`). `publish-edge` already releases to
   edge, so it is almost certainly fine — confirm with `charmcraft whoami`.

## Cutting a release

1. **Land everything on `main`** and wait for its `publish-edge` run to finish —
   so `4/edge` holds the exact revision you intend to release.
2. **Tag `main`'s HEAD** (the discipline that keeps it simple — see below):
   ```bash
   git checkout main && git pull
   git tag -a v0.1.0 -m "v0.1.0"
   git push origin v0.1.0
   ```
   `release-tag.yaml` then promotes `4/edge → 4/candidate`, builds the subordinate
   charm + `norma` binary from the tag, and cuts a SLSA-attested GitHub Release
   (principal + subordinate `.charm`, the binary, `SHA256SUMS`).
3. **Vet `4/candidate`** — deploy from it, run the smoke acceptance:
   ```bash
   juju deploy juju-norma --channel 4/candidate --resource norma-bin=<rev>
   juju status   # active/idle
   ```
4. **Promote to `4/stable`** when satisfied: *Actions → "Promote charm channel" →
   Run workflow → `4/candidate` → `4/stable`*. Consumers following `4/stable` then
   pick it up automatically.

## Tag-HEAD discipline (why there is no SHA gate)

`release-tag` promotes **whatever is currently on `4/edge`**, which is whatever
`main`'s last merge published. So **tag `main`'s HEAD after its edge publish
completes** and the released bits are exactly the tagged commit — no revision
lookup, no SHA verification, no embedded-sha matching. Tagging an *older* commit
(while `main` has advanced) would ship the newer edge bits instead; don't. This is
the same trust model Juju CI uses for `juju-qa-*` charms — simple by design.

## Partial-failure recovery

- A `charmcraft promote`/`release`/`download` blip is retried 5× automatically
  (the `cc()` wrapper). If it still fails, re-run the failed workflow job — promote
  is idempotent (it just moves a channel pointer).
- To undo a bad release on a channel, close it and re-point:
  `charmcraft close juju-norma 4/candidate` then re-promote the good revision, or
  `charmcraft release juju-norma --revision <good-rev> --channel 4/candidate
  --resource norma-bin:<rev>`.
- The GitHub Release is independent of CharmHub; delete/recreate it with `gh
  release delete <tag>` / re-run `release-tag` if its artifacts are wrong.

## Release notes

`release-tag` uses `gh release create --generate-notes` (auto changelog from merged
PRs). For a human-facing summary, edit the published release and add a **"Notable
changes"** section at the top — the line a downstream pin-bump references.

## Dry run

Before the first real `v0.1.0`, rehearse the whole chain end-to-end with a
throwaway pre-release tag, then delete it:

```bash
git tag -a v0.0.1-rc1 -m "release dry-run" && git push origin v0.0.1-rc1
# watch release-tag.yaml: promote 4/edge->4/candidate, GH Release created
# then clean up:
git push --delete origin v0.0.1-rc1 && git tag -d v0.0.1-rc1
gh release delete v0.0.1-rc1 --yes
charmcraft close juju-norma 4/candidate   # retract the rehearsal from candidate
```
