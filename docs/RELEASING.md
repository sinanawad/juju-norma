# Releasing juju-norma

How `juju-norma` is published to CharmHub and released. The model deliberately
mirrors how Juju's *own* CI consumes test charms (the `juju-qa-*` family): a charm
is published to a channel and consumers deploy it **by name from that channel** —
no revision-pinning ceremony, no provenance handshake.

We publish on the default **`latest`** track (every charm has it; **no track
registration needed**). See *Future: version tracks* below for when/why we'd add a
Juju-major track (`4`).

## Channels (the `latest` track)

| Channel | Who writes it | When |
|---|---|---|
| `latest/edge` | `publish-edge.yaml` (automatic, on merge) | every merge to `main` (after CI is green) |
| `latest/candidate` | `release-tag.yaml` (automatic, on `v*` tag) | on pushing a `v*` git tag — promotes the current `latest/edge` |
| `latest/stable` | `promote.yaml` (manual) | after a human vets `latest/candidate` |

**Consumers (e.g. Juju CI) deploy by name from the channel** — exactly like the
bare `juju deploy juju-qa-dummy-source` deploys in `juju/juju`'s test suites:

```bash
juju deploy juju-norma                       # latest/stable (the default), or:
juju deploy juju-norma --channel latest/edge --resource norma-bin=<rev>
```

A specific test that needs byte-identical determinism pins a revision itself
(`juju deploy juju-norma --revision N`), exactly as `tests/suites/refresh/` pins
`juju-qa-test --revision 22`. That is the *consumer's* per-test choice, not a
release-chain obligation.

## One-time prerequisite

- **`CHARMHUB_TOKEN` must carry `package-manage`** (promote/release need it; a
  lib-check-only token is `package-view`). `publish-edge` already releases to edge,
  so it is almost certainly fine — confirm with `charmcraft whoami`.

(No track registration is required for `latest`.)

## Cutting a release

1. **Land everything on `main`** and wait for its `publish-edge` run to finish —
   so `latest/edge` holds the exact revision you intend to release.
2. **Tag `main`'s HEAD** (the discipline that keeps it simple — see below):
   ```bash
   git checkout main && git pull
   git tag -a v0.1.0 -m "v0.1.0"
   git push origin v0.1.0
   ```
   `release-tag.yaml` then promotes `latest/edge → latest/candidate`, builds the
   subordinate charm + `norma` binary from the tag, and cuts a SLSA-attested
   GitHub Release (principal + subordinate `.charm`, the binary, `SHA256SUMS`).
3. **Vet `latest/candidate`** — deploy from it, run the smoke acceptance:
   ```bash
   juju deploy juju-norma --channel latest/candidate --resource norma-bin=<rev>
   juju status   # active/idle
   ```
4. **Promote to `latest/stable`** when satisfied: *Actions → "Promote charm
   channel" → Run workflow → `latest/candidate` → `latest/stable`*. Consumers
   deploying by name then pick it up automatically.

## Tag-HEAD discipline (why there is no SHA gate)

`release-tag` promotes **whatever is currently on `latest/edge`**, which is
whatever `main`'s last merge published. So **tag `main`'s HEAD after its edge
publish completes** and the released bits are exactly the tagged commit — no
revision lookup, no SHA verification. Tagging an *older* commit (while `main` has
advanced) would ship the newer edge bits instead; don't. This is the same trust
model Juju CI uses for `juju-qa-*` charms — simple by design.

## Partial-failure recovery

- A `charmcraft promote`/`release`/`download` blip is retried 5× automatically
  (the `cc()` wrapper). If it still fails, re-run the failed workflow job — promote
  is idempotent (it just moves a channel pointer).
- To undo a bad release on a channel, close it and re-point:
  `charmcraft close juju-norma latest/candidate` then re-promote the good revision,
  or `charmcraft release juju-norma --revision <good-rev> --channel latest/candidate
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
# watch release-tag.yaml: promote latest/edge->latest/candidate, GH Release created
# then clean up:
git push --delete origin v0.0.1-rc1 && git tag -d v0.0.1-rc1
gh release delete v0.0.1-rc1 --yes
charmcraft close juju-norma latest/candidate   # retract the rehearsal from candidate
```

## Future: version tracks (deferred)

`juju-norma` is coupled to a Juju major (`assumes: juju >= 4.0`). Today only Juju 4
is in play, so the single `latest` track is sufficient and simplest. **When a
second Juju major (or an incompatible norma min-Juju bump) arrives**, register a
Juju-major track so each Juju CI line follows the newest *compatible* revision
(`--channel 4/stable`, `--channel 5/stable`, …) instead of a `latest` that could
serve an incompatible charm. This is how `juju-qa-test` itself works (`2.0/`,
`3.0/` tracks).

Registering a track is then two steps:
1. **Request a track guardrail** for `juju-norma` in the *Charmhub requests*
   category at <https://discourse.charmhub.io> (e.g. regex `[0-9]+` to permit `4`,
   `5`, …). Guardrails are Canonical-reviewed; tracks must match one.
2. **Create the track** once the guardrail exists:
   `charmcraft create-track --name juju-norma --track 4`.
   Then re-channel these three workflows `latest/* → 4/*` and tell consumers to
   follow `4/stable`.
