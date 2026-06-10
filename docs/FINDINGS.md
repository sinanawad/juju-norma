# juju-norma :: findings report (Juju 4.0 + LXD)

Concise report of environment/Juju behaviors observed while building and
live-verifying the machine calibration charm on an **LXD** controller
(`juju 4.0.10`, localhost cloud). Ordered by likely interest. Charm-side bugs we
introduced are excluded (fixed in-branch); this is about the substrate/engine.

## Integration-suite execution note (localhost LXD)

The jubilant suites must run **one heavy suite per `pytest` process** (each gets a
fresh session model), NOT all-in-one. Batching many machine-provisioning suites
into a single process saturates the single localhost-LXD host: new machines take
>15 min to boot and tests time out with `waiting for machine` (machine `stopped`)
— which looks like a charm/test failure but is host contention. Read-only suites
(config, actions, lifecycle) batch fine; scaling/storage/subordinate/relations
(which add machines or apps) should be invoked separately. CI on a dedicated
runner can batch more, but the per-suite invocation is the safe default. Teardown
must use `destroy-model --force --no-wait` (a plain destroy hangs forever on an
errored unit; F20/F21 deliberately error).

## 0. Intentional test-bed affordances — MUST NOT be copied into a production charm

This is a *sterile calibration* charm: several deliberate choices are safe here
because the workload is a throwaway HTTP echo, but would be defects in a real
charm. Flagged so they are never cargo-culted:

- **Unauthenticated `POST /toggle-health`** (`workload/main.go`) — flips the
  health signal with no auth, bound to all interfaces. It's how F-health is
  exercised; if `juju expose`d on a real network anyone reachable can flip it.
  Production: bind to localhost or require auth.
- **App secret granted to every `calibration-provider` relation**
  (`charm.py _manage_app_secret`) — intentional for the harness; production
  grants should be scoped to relations that actually need the secret.
- **Workload systemd unit runs as root, no sandboxing** (`norma.py
  build_systemd_unit`) — no `User=`/`NoNewPrivileges=`/`ProtectSystem=`.
  Acceptable for a sterile binary; add hardening directives for a real workload.
- **Graceful shutdown is best-effort** — the workload now drains on SIGTERM
  (`srv.Shutdown`, 5s); fine for `Type=simple`. (Was L6; now addressed.)

## A. Genuine Juju behaviors worth a doc note or ticket

1. **New relation endpoints / storage-count / lxd-profile bind at DEPLOY, not REFRESH.**
   Adding a `provides: cos-agent` endpoint, widening `storage: data` count range,
   or adding `lxd-profile.yaml` and then `juju refresh`-ing an existing app does
   NOT take effect on already-deployed units:
   - cos-agent: `juju refresh` → `ERROR ... endpoints "cos-agent" do not exist`;
     subsequent `juju integrate` → `relation endpoint not found`.
   - storage: `juju add-storage data=1G` on the refreshed unit →
     `storage name "data" not supported by charm`.
   A **fresh deploy** with the new metadata works in every case.
   *Suggestion:* document this clearly (metadata that changes the unit's
   topology is deploy-bound), or surface a clearer error from `refresh` when a
   newly-declared endpoint/storage won't be applied to existing units.

2. **LXD provider rejects block charm storage.**
   `juju add-storage <unit> blk=lxd,1G` (and via `lxd-zfs`) →
   `storage directive pool "…" does not support charm storage "block"`. Only
   filesystem storage provisions on LXD. Block storage (a machine-only feature)
   is therefore unverifiable without MAAS/EC2/OpenStack.
   *Suggestion:* none (provider limitation) — but worth stating explicitly in the
   storage docs that LXD = filesystem-only for charm storage.

3. **Charm `lxd-profile.yaml` not applied on the localhost LXD cloud; nested
   `--to lxd:N` does not provision.**
   With `lxd-profile.yaml` correctly packaged, a fresh top-level deploy did not
   create a `juju-<model>-<app>-<rev>` LXD profile, and the profile's unique
   marker (`linux.kernel_modules`) never appeared on the container's expanded
   config (baseline `security.nesting=true` is model-wide, so it isn't proof).
   `juju add-unit --to lxd:N` never registered the nested machine (`N/lxd/0`
   absent) and the unit went `agent:lost`.
   *Suggestion:* confirm whether the localhost LXD provider is expected to apply
   charm lxd-profiles to top-level instances; if only nested containers are
   supported, the nesting path needs `security.nesting` on the host AND a working
   nested-agent network — neither worked out-of-the-box here. Likely needs a real
   (non-nested) LXD host or MAAS to calibrate F15.

3b. **`relation-get` is denied while a relation is departing/breaking.**
   When a *related* app is removed, the surviving app gets
   `*-relation-departed`/`-broken`; reading **even your own** unit databag
   (`rel.data[self.unit]`) during that hook can raise
   `ModelError: ERROR permission denied`. An unguarded reconcile that writes
   relation data on every event will crash all units to `error` when a peer app
   is removed. *Suggestion:* this is easy to hit and hard to discover — worth a
   prominent note in the relation-data docs ("relation-get may be denied during
   teardown; guard writes"). (We fixed our charm with a try/except; regression
   tested.) The good news: `juju resolve` after deploying the fix recovers cleanly.

3c. **`root-disk` constraint on localhost LXD fails machine creation.**
   `juju deploy --constraints "root-disk=8G"` is accepted (`juju constraints`
   shows it) but the machine goes `down`:
   `Failed loading storage pool: Storage pool not found`. `cores`/`mem`/
   `virt-type=virtual-machine` work (the latter produced a real LXD VM).
   *Suggestion:* root-disk on the LXD provider needs a configured storage pool
   for root volumes; surface a clearer error (or fall back) when none exists.

## B. charmcraft (tooling)

4. **`lxd-profile.yaml` not auto-primed with the `uv` plugin.**
   charmcraft 4.2.1 did NOT include the charm-root `lxd-profile.yaml` in the
   packed `.charm` when the part uses the `uv` plugin + a custom `override-build`
   (verified with `unzip -l`). Recognized charm files (metadata/config/actions)
   WERE included. Workaround: explicit
   `cp $CRAFT_PART_SRC/lxd-profile.yaml $CRAFT_PART_INSTALL/` in `override-build`.
   *Suggestion:* charmcraft should auto-prime recognized charm files
   (lxd-profile.yaml) regardless of the build plugin, or document that custom
   `override-build` suppresses it.

5. **`git describe` in `override-build` fails when packing from a subdir.**
   The subordinate charm (`subordinate/`) has no `.git` in its build instance →
   `fatal: not a git repository` (code 128). Guard with
   `(git describe --always 2>/dev/null || echo "0.1.0")`. Expected, but a common
   foot-gun for mono-repo / subdir charms.

## C. Environment / ergonomics (not bugs)

6. **`juju ssh` needs an ssh key on a freshly `add-model`-ed model** —
   `Permission denied (publickey)`. `juju exec --unit` (runs via the agent as
   root) is the reliable path for in-unit diagnostics; no key needed.

7. **grafana-agent channel/base** — `latest/stable` does not exist; use
   `2/stable` (rev 844 is multi-base incl. ubuntu@24.04). After integrating
   `cos-agent`, grafana-agent stays `blocked: Missing grafana-cloud-config/
   send-remote-write/...` — expected (it needs a downstream COS). The
   **provider** side (our charm) correctly pushes metrics jobs + alert rules +
   dashboards through the single `cos-agent` relation (2862-byte databag).

8. **`juju run … --format=json` writes progress to stderr** — merging with
   `2>&1` corrupts the JSON. Read stdout only (`2>/dev/null`).

9. **Transient `systemctl restart` failure on multi-unit refresh** — one unit
   logged `Job for norma.service failed` during a refresh and auto-recovered via
   the charm's idempotent `apply()` (`reset-failed` + retry). Self-healing;
   watch for reproducibility under heavy concurrent refresh.

## D. Time-gated / cloud-only (calibrated by unit tests, ROADMAP for live)

- **Secret rotate/expire hook dispatch** — rotation policy is monthly (Juju's
  minimum is coarse); handlers are unit-verified and hardened to never crash
  teardown. Live dispatch needs elapsed time.
- **Multi-space bindings (F12)** — LXD exposes only `alpha`; `juju bind` across
  spaces needs MAAS/EC2.
- **Cloud-only constraints (F13)** — `instance-type`, `tags`, `image-id`,
  `instance-role`, `allocate-public-ip`, multi-`zones`/`spaces` need a real
  cloud. LXD honors `arch, cores, mem, root-disk, root-disk-source, virt-type,
  zones` (verified: `cores=2 mem=2G root-disk=8G` applied; `virt-type=virtual-machine`
  produced a real LXD VM).

## Net assessment

The charm exercises the full LXD-verifiable machine feature surface. The three
machine-distinct features that could NOT be fully verified on localhost LXD —
**block storage, lxd-profile application, multi-space bindings** — are all
environment limitations (need MAAS or a real cloud / non-nested LXD host), not
charm defects. The most actionable engine/tooling items are **A1** (refresh vs
deploy metadata binding — confusing errors) and **B4** (charmcraft not priming
lxd-profile.yaml with the uv plugin).

## F. Calibration anomaly dossiers (FINDINGS#N — citable IDs)

Numbered, evidence-first dossiers for suspected ENGINE anomalies caught by the
calibration suite (the P2-11 convention: xfail/skip reasons cite `FINDINGS#N`;
each dossier stays "ready to file" upstream for when the external-integration
gate opens).

### FINDINGS#1 — app-owned secret becomes unresolvable after scale churn; recovery commit then error-loops update-status

**Status: INVESTIGATING** (run-2 postmortem pending). First captured 2026-06-10,
the first instrumented run after the P2-1 forensics landed (charm rev
`e66d879`); previously visible only as the undiagnosable full-suite
`test_secrets` failure ("has-content=false", ~10th test, after
badbehavior/relations/scaling churn the shared session model).

**Environment**: LXD localhost, controller agent **4.0.10.1**, client 4.0.12
(source build), full F1-F22 suite in one session model.

**Observed (run 1, 2026-06-10 05:54–07:25 UTC, model `norma-5122bd9b`)**:

1. During `test_scale_up` (add-unit 1→2): new unit/1 provisions to
   `active/idle` normally, then the **leader** `juju-norma/0` flips
   `error: hook failed: "update-status"` (09:25:13+03:00) and **keeps failing
   update-status every interval for the rest of the run** (a deterministic
   retry loop, ≥7 recurrences in the status stream).
2. All later waits cascade: scale_up/scale_down (Timeout), dynamic-storage
   cycle, subordinate, refresh/upgrade — 6 failed / 4 errors vs the historical
   single test_secrets failure. One removed machine lingered `life=dead`.
3. The new forensic readout captured the secret state on the same leader:
   `get-secret-info` → `error='SecretNotFoundError: '` (empty message) for
   `secret-id=secret://1281c5f1-8539-4f59-89c2-718849ddd849/mkarsh538mlnhc5ghl4g`
   — i.e. peer app-data still holds the URI, `secret-get --refresh` says the
   secret does not exist. The ACTION (read-only) completes fine while the
   HOOK (update-status) fails — the divergence is in the write/commit path.

**CONFIRMED MECHANISM (run-2 kept-model postmortem, model `norma-426e8c20`,
evidence archived in `.omc/findings/1/`)** — TWO engine anomalies, both proven:

1. **In-hook `SecretNotFoundError` for an app-owned secret that EXISTS.** At
   07:51:28Z, in the leader's `norma-peers-relation-joined` for the new unit
   (scale-up 1→2), `model.get_secret(id=<pointer>)` raised SecretNotFoundError
   while the pointer was still the ORIGINAL secret `evvn7qoo…` — which `juju
   secrets` lists alive (sole revision, owner juju-norma) hours later. Ledger
   proof: the first `secret-recreated` entry has `stale-id=…/evvn7qoo…`.
   Leadership stable (`show-unit`: leader: true) — not a lease-flap artifact.

2. **`CommitHookChanges` is NOT atomic: a failed hook's `relation-set`
   persists.** The charm's self-heal buffered `secret-add` + pointer write;
   commit failed server-side every time (`cannot apply changes: creating
   secrets: secret with label "calibration-password" already exists` — itself
   proving the secret existed and anomaly 1's NotFound was wrong), the hook
   errored — yet each iteration's ledger `stale-id` equals the PREVIOUS failed
   iteration's phantom `new-id` (12+ links: evvn7qoo→gd8a54fa→avtiaab6→
   q151vifg→…), and `show-unit` confirms the final phantom in the Juju-side
   app databag. Relation data from failed hooks persisted EVERY time,
   violating the failed-hook-changes-nothing contract. This poisoned pointer
   is the long-lived corruption behind the historical test_secrets artifact.

No charm-side Python traceback exists in the entire run apart from the
intentional bad-behavior one (H2 refuted). The old pre-P2-1 charm hid all of
this as a silent `return` + undiagnosable `has-content=false`.

**Charm-side consequence (fixed in-branch, p2-1b)**: a blind re-create with
the same label can never commit while the real secret holds the label → the
self-heal must RECOVER-BY-LABEL first (the commit rejection proves a label
lookup would succeed), re-create only if the label is free, and suppress via a
ledger loop-detector otherwise (anomaly 2 makes the phantom pointer durable,
so an unsuppressed retry loops forever at ~uniter-backoff cadence — observed
07:51→08:21+).

**Repro**: 2/2 instrumented full-suite runs (`JUJU_CLI=/data/dev/juju/_bin/juju
JUJU_CONTROLLER=lxd make integration`); anomaly window = leader's
relation-joined during scale-up churn on the shared session model.

**Status**: charm-side recovery validated live (run 3, fixed charm):
**cascade eliminated** — 1 failed / 34 passed / 2 xfailed in 22:42 vs run 2's
6 failed / 4 errors in 91:29; hook errors bounded + self-cleared
(`secret-recreate-suppressed` ×10, suite continued normally). Anomaly 1 fired
again (3/3 runs) and run 3 SHARPENED it: zero `secret-pointer-repaired` events
— post-trigger the leader agent resolves its owned secret neither by id NOR by
label, persistently, while `juju secrets` lists it alive controller-side ⇒
wholesale unit-agent-side secret-resolution loss, not a stale id index.
test_secrets still fails — correctly: it reports a real engine state.

**TIP CLASSIFICATION (run 4, controller built from 4.0 @ `0c7e2b4e5b`,
agent 4.0.12.1, 2026-06-10)** — the two anomalies SPLIT:

- **Anomaly 2 (non-atomic CommitHookChanges): FIXED on tip.** All 17 recreate
  attempts carry `stale-id` = the REAL secret (`ul4qg30…`) — the pointer never
  went phantom across 17 failed commits (vs the 12-link phantom chain on
  4.0.10.1). Rollback works. Reclassified: *since-fixed between 4.0.10.1 and
  `0c7e2b4e5b`* — backport-audit question for released 4.0.x.
- **Anomaly 1 (unit-agent resolution loss): LIVE on tip.** Same trigger
  (leader's peer relation-joined, scale-up 1→2); by-id AND by-label both
  `SecretNotFoundError` (empty message — NOT lease; `repair-error` field
  proves it) for ≥78 minutes (fresh probe 10:59Z), while `juju secrets`
  serves the secret throughout. Deterministic 4/4 across both controllers.
  **This is the headline unfiled bug.**
- Charm-side: detector v3 (suppress when a prior attempt exists for the SAME
  stale pointer) bounds the loop in the rollback world too — run 4 looped 17×
  because v2 keyed only on the persisted phantom.

Evidence: `.omc/findings/1/run4-tip-{ledger,secrets,debug}.{json,log}`. The
broken tip model `lxd-tip:norma-c9c68d3b` is KEPT LIVE for hands-on
inspection (destroy with `juju destroy-model lxd-tip:norma-c9c68d3b
--no-prompt --force --no-wait` when done; the `lxd-tip` controller can go the
same way after).

**Next**: P2-11 disposition for the full tier (xfail citing FINDINGS#1 vs
keep-red) — user decision; upstream filing gated (PX-3), dossier
ready-to-file.
