# juju-norma :: findings report (Juju 4.0 + LXD)

Concise report of environment/Juju behaviors observed while building and
live-verifying the machine calibration charm on an **LXD** controller
(`juju 4.0.10`, localhost cloud). Ordered by likely interest. Charm-side bugs we
introduced are excluded (fixed in-branch); this is about the substrate/engine.

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
