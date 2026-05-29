# juju-norma :: implementation progress ledger

Durable, repo-native progress tracker for the feature-coverage build (PLAN
P0–P7, F1–F22). Replaces the (uninstalled) omc ultragoal ledger. Updated as
each feature passes its gates: `make lint && make unit` (unit) + live-LXD
`juju` CLI acceptance (Constitution VIII). Evidence is recorded inline.

- **Cloud:** LXD only. Controller `lxd` (juju 4.0.10), dev model `norma-dev`.
- **Branch:** `feat/feature-coverage`.
- **Juju-bug policy:** persistent abnormal behavior → check vs `/data/dev/juju@4.0`
  source; if a genuine engine bug, mark the feature LIMITATION and move on.

## Status legend
`TODO` · `WIP` · `UNIT` (unit-green) · `DONE` (unit + live-LXD CLI verified) ·
`LIMITATION` (blocked by env/juju issue, documented) · `ROADMAP` (cloud-only, not LXD-verifiable)

## Phases / features

| Phase | F | Feature | Status | Evidence |
|---|---|---|---|---|
| P0 | — | scaffold (charmcraft/pyproject/uv/Makefile/workload/norma.py/driver/charm skeleton/tests) | DONE | lint+unit green; `charmcraft pack` → juju-norma_amd64.charm |
| P1 | F1 | lifecycle + systemd workload | DONE | deploy → active/idle; `systemctl is-active norma`→active; `/health`→OK |
| P1 | F2 | workload via file resource | DONE | `--resource norma-bin=./norma` laid to /usr/local/bin/norma 0755; idempotent apply (no restart storm) |
| P2 | F3 | typed config (string/int/float/bool/secret) | DONE | get-config all types; calibration-int=0→blocked→recover; secret resolve/not-found |
| P2 | F4 | status priority + Active-empty | DONE | blocked>maint>waiting>active; ActiveStatus empty; live blocked/active |
| P2 | F5 | actions (class-A; machine variants) | DONE | run-check(systemd/config/unknown), check-security(substrate=machine,k8s-api=n/a), test-networking([8080/tcp]+bindings), get-version, set-status, fail, get-event-log |
| P2 | F6 | peers + leadership | DONE | get-cluster-info is-leader/unit-count/leader; peer databag write+idempotent |
| P2 | F18 | event-deferral (defer-gate) | DONE | test-defer arm→config-changed deferred(ledger deferred:true)+re-emitted |
| P2 | F19 | introspect (machine: systemd-service collector) | DONE | introspect → 10 sections incl systemd-service{binary-present,service-running,unit-file} |
| P2 | F20 | bad-behavior test-bed | DONE | status modes render violating status; hook-error → workload error → set none + `juju resolve` → active (verified live) |
| P3 | F7 | provides/requires self-relate (calibration iface) | DONE | 2 apps (same charm) integrated; provider sees provider+requirer unit data both ways |
| P3 | F8 | app-databag mode | DONE | leader app bag {app-name,role,planned-units} propagates on both ends |
| P3 | F17 | upgrade-charm + version | DONE | refresh→rev3; charm-version stamped; upgrade-charm count=3 in ledger |
| P4 | F9 | scaling + cluster-info | DONE | add-unit -n2 → 3 units active; get-cluster-info unit-count=3, leader, peer data all units |
| P4 | F10 | secrets full lifecycle | DONE | leader app secret (monthly), get-secret-info has-content=true, `juju secrets` lists it; rotate/expire handlers unit-verified (live dispatch time-gated) |
| P4 | F11 | filesystem storage + dynamic attach/detach | DONE | check-storage marker+writable; fresh app: add-storage data/5 → detach → attach cycle ✓ |
| P4 | F11b | block storage | LIMITATION | LXD provider rejects block charm storage ("pool does not support charm storage block") — needs MAAS/cloud |
| P4 | F12 | networking: open-port/expose | DONE | open-port 8080/tcp; `juju expose` → status exposed=True; bindings via test-networking |
| P5 | F14 | subordinate mode (juju-info, container scope) | DONE | separate subordinate charm; integrate juju-info → 3 subs colocated 1-per-principal-machine; get-principal → juju-norma/2 |
| P5 | F15 | lxd-profile.yaml | PARTIAL | artifact shipped + charmcraft packaging bug fixed (now in .charm); application NOT verifiable on localhost LXD — top-level machines don't get the charm profile (unique linux.kernel_modules marker absent), and `--to lxd:N` nested containers fail to provision (agent:lost). Needs real LXD host / MAAS → ROADMAP |
| P5 | F16 | cos-agent push observability | DONE | COSAgentProvider; cos-agent relation to grafana-agent subordinate; provider databag (2862B) carries metrics scrape jobs + NormaWorkload alert rules (KB2) + dashboards. grafana-agent blocked-missing-COS = expected (push model) |
| P5 | F5b | test-workload-ops action (systemd/file/subprocess) | DONE | live 7/7: file-write/read/exists/remove + service-status/restart + binary-check |
| P6 | F12s | spaces / bindings | PARTIAL | `juju spaces` shows only `alpha` (multiple subnets) on LXD; multi-space `juju bind` needs MAAS/EC2 → ROADMAP. open-port/expose done in P4 |
| P6 | F13 | machine constraints + placement | DONE (LXD subset) | constraints cores=2/mem=2G/root-disk=8G applied (juju constraints + show-machine); virt-type=virtual-machine → real LXD VM (machine 10 = VIRTUAL-MACHINE) active. cloud-only constraints (instance-type/tags/image-id/spaces) = ROADMAP; --to lxd:N nested = LXD limitation (see F15) |
| —  | F21 | juju-resolve error path (folded into F20) | DONE | live: bad-behavior-mode=hook-error → workload error; set none + `juju resolve` → idle/active |
| —  | F22 | stop/remove idempotent teardown | DONE | stop/remove handlers stop+disable+remove unit file (idempotent); unit-tested; clean live removals |
| P7 | — | CI workflow + README + FINDINGS report + dashboards/alert-rules | DONE | .github/workflows/ci.yaml (lint/unit/pack/build/integration-LXD); README.md; docs/FINDINGS.md; alert rules + dashboard shipped (F16) |

## Findings / Juju + environment issues
_(appended as discovered; feeds the final report)_

- **[env] `juju ssh` needs ssh keys on fresh model** — `add-model` does not seed the
  user's ssh key; `juju ssh` → `Permission denied (publickey)`. Use `juju exec
  --unit` for in-unit diagnostics (runs via the agent as root, no ssh key). Not a bug.
- **[note] dual workload-version source** — `get-version` charm-version comes from
  charmcraft `git describe` (e.g. `0262427`); the binary's `/version` reports the
  `-X main.version` ldflag (`0.1.0`). Two sources; align in a later pass.
- **[note] `re-emitted:true` ledger flag is Scenario-only** — live juju re-emission of
  a deferred event does not expose `event.deferred`, so the re-emitted marker only
  appears under ops.testing. Deferral itself (F18) is verified live. Cosmetic.
- **[juju/env] LXD provider does not support block charm storage** — `juju add-storage
  juju-norma/0 blk=lxd,1G` → "storage pool ... does not support charm storage block"
  across lxd/lxd-zfs pools. Block storage (F11b) needs MAAS/EC2/OpenStack. Not a charm
  bug; ROADMAP. Filesystem + dynamic attach/detach work fully on LXD.
- **[juju] storage-count metadata is bound at deploy, not refresh** — widening `data`
  to `multiple: range 1-5` and `juju refresh` succeeded (rev 6) but `add-storage data`
  on the EXISTING units failed: "storage name data not supported by charm" / min-count
  enforced. A FRESH deploy with the new metadata works (add/detach/attach data/5 cycle
  passed). So storage multiplicity changes require redeploy, not refresh. Worth a doc note.
- **[transient] systemctl restart failure on multi-unit refresh** — one unit logged
  `systemctl restart norma failed: Job for norma.service failed` during a refresh, then
  auto-recovered (idempotent apply `reset-failed`+retry); unit reached active. Watch for
  reproducibility; currently self-healing, not blocking.
- **[charmcraft] lxd-profile.yaml not auto-primed with the uv plugin** — charmcraft
  4.2.1 did NOT include the charm-root `lxd-profile.yaml` in the packed `.charm` when
  the part uses the `uv` plugin + a custom `override-build`. Confirmed via `unzip -l`.
  Fix: explicitly `cp $CRAFT_PART_SRC/lxd-profile.yaml $CRAFT_PART_INSTALL/` in
  override-build (same pattern the k8s sibling uses for icon.svg). Worth a charmcraft
  docs/bug note — auto-inclusion of recognized charm files appears plugin-dependent.
- **[juju/env] charm lxd-profile not applied on localhost LXD; nested lxd:N fails** —
  with lxd-profile.yaml correctly packaged, a fresh top-level deploy did NOT apply the
  charm profile (no `juju-<model>-<app>-<rev>` profile; unique `linux.kernel_modules`
  marker absent — though baseline `security.nesting=true` is present model-wide). The
  PLAN-prescribed `--to lxd:N` nested placement never provisioned the nested machine
  (`6/lxd/0` never registered; unit `agent:lost`). So F15 application needs a real LXD
  host (non-nested) or MAAS. Charm-side (the shipped, valid lxd-profile.yaml) is complete.
- **[juju] lxd-profile / storage-count bind at deploy, not refresh** — a refresh that
  adds lxd-profile.yaml or widens storage count does NOT apply to already-running units;
  a fresh deploy is required. Consistent across both features.
- **[note] secret rotate/expire live dispatch is time-gated** — rotation policy is
  monthly (Juju minimum is coarse); the rotate/expired/remove HANDLERS are unit-verified
  (Scenario) and hardened to never crash teardown. Live hook dispatch needs elapsed time.

## Charm bugs found + fixed (calibration value — these are the kind of defect
## this charm exists to surface)

- **relation-departed/broken crashed reconcile (`ModelError: permission denied`)** —
  removing a *related* app (juju-norma-b) fired `calibration-provider-relation-departed`
  on all juju-norma units; `_update_relation_data` read `rel.data[self.unit]` and Juju
  denied relation-get on the departing relation → uncaught → all 3 units to `error`.
  FIX: guard the calibration relation-data writes + secret grant/revoke with
  `try/except ops.ModelError` (Constitution VII — reconcile must not crash on a normal
  teardown event). Regression tests added (test_relations TestRelationTeardown). This is
  a realistic machine-charm trap (relation-get is restricted during teardown).
  **Live-proven fixed:** a fresh provider (nfix) stayed active/idle through the
  removal of its requirer (nreq) — the exact scenario that crashed all units pre-fix.

## Limitations (juju-bug, env, or cloud-only; feature partial/skipped on LXD)

- **F11b block storage** — LXD provider rejects block charm storage. ROADMAP (MAAS/cloud).
- **F15 lxd-profile application** — not applied on localhost LXD; nested lxd:N fails. ROADMAP.
- **F12 multi-space bindings** — LXD = `alpha` only. ROADMAP (MAAS/EC2).
- **F13 cloud-only constraints** — instance-type/tags/image-id/etc. ROADMAP.
- **F13 `root-disk` constraint on localhost LXD** — `juju deploy --constraints root-disk=8G`
  was accepted by Juju (shown in `juju constraints`) but the LXD machine failed to create:
  `machine N down: Failed loading storage pool: Storage pool not found`. So root-disk
  needs a configured LXD storage pool for root volumes; cores/mem/virt-type work fine.
- **secret rotate/expire live dispatch** — time-gated (monthly); handlers unit-verified.
