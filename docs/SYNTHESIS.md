# 002 machine-charm :: research synthesis

meta:
  mission: build Juju MACHINE (IAAS/VM) calibration charm; sibling to juju-norma-k8s; CI regression guard for Juju engine machine path.
  target_juju: "4.0+"
  source_verified: "/data/dev/juju @ branch 4.0 = v4.0.10-242-g41eec5d033 + live ~/go/bin/juju 4.0.6. §1 deltas re-grepped on 4.0 directly. research/ dossiers were 3.6.24-sourced (workflow ran pre-branch-switch) — for ANY version-sensitive claim, §1/§1.5 here override the dossiers."
  dev_cloud: lxd (local + CI)
  bases: "ubuntu@24.04 only for now; design for easy multi-base expansion (26.04/28.04 later)"
  inputs: research/{01-machine-capabilities,02-k8s-baseline,03-code-reuse,04-architecture-packaging,05-capability-verification}.md + CI-coverage agent
  status: research-complete; pending user decisions (see §9); next = /speckit.specify 002

## 1. CRITICAL corrections (verifier + direct 4.0 re-verification)
- C1 [REMOVED-4.0 CONFIRMED] `pre-series-upgrade`/`post-series-upgrade` + `juju upgrade-machine`/`upgrade-series`: NOT in 4.0 hook-kind enum (domain/deployment/charm/hooks/hooks.go has no series-upgrade Kind); no upgrade-machine/upgrade-series file in cmd/juju; absent from `juju help commands` (4.0.6). Only residual `MachineUpgradeTarget` struct field in internal/worker/uniter/hook/hook.go:60-63 (dead — no Kind dispatches it). DO NOT target.
- C2 [RESOLVED] research dossiers were 3.6.24-sourced; §1 deltas now re-verified on 4.0 source (v4.0.10-242) + 4.0.6 CLI. Remaining live-only checks deferred to Constitution-VIII CLI acceptance on user's 4.0.6 controller.
- C3 `dummy-storage` metadata: block stores have NO `location:` (fs only); ranges single=0-1 multi=0-2; `minimum-size:10M` on all. Re-read before copying.
- C4 `leader-deposed` = Kind in 4.0 (hooks.go:45) but NOT dispatched as runnable hook; `leader-elected` works. Don't rely on `leader-deposed`/`leader-settings-changed` firing.
- C5 dossier-01 file:line cites are 3.6 paths; 4.0 relocated charm meta/hooks to `domain/deployment/charm/` (see §1.5). Re-cite at impl.
- C7 [REMOVED-4.0 CONFIRMED] payloads: NO payload hook tools in 4.0 jujuc/server.go, NO cmd/juju/payload dir, absent from CLI. DO NOT target (was a draft target via 3.6).

## 1.5 4.0-verified facts (authoritative; re-grepped on v4.0.10-242)
- hook kinds (domain/deployment/charm/hooks/hooks.go:18-104): install,start,config-changed,upgrade-charm,stop,remove,leader-elected,leader-deposed,update-status,secret-{changed,expired,remove,rotate},relation-{created,joined,changed,departed,broken},storage-{attached,detaching},pebble-{custom-notice,ready,check-failed,check-recovered}. NO series-upgrade, NO collect-metrics/meter-status (gone vs 3.6).
- charm meta relocated → domain/deployment/charm/meta.go: ScopeContainer="container" (:38), Subordinate (:263), subordinate-must-have-container-relation validation (:744). subordinate feature INTACT in 4.0.
- constraints (core/constraints/constraints.go:23-39) UNCHANGED: arch,container,cores,cpu-power,mem,root-disk,tags,instance-type,spaces,virt-type,zones,allocate-public-ip,image-id.
- bases (core/base/supportedbases.go): 20.04,22.04,24.04,**26.04** all supported in 4.0 (26.04 is live NOW, not "eventually"; 28.04 absent). => single-base 24.04 now, trivially expandable to 26.04.
- jujuc hook tools server.go present at same path; payload-* tools ABSENT (C7).
- live CLI (4.0.6) present: bind, expose, unexpose, integrate(relate), add-storage, attach-storage. absent: upgrade-machine, upgrade-series, payloads.

## 2. machine capability catalog (corrected, tagged)
legend: MO=machine-only(no k8s equiv) · X=shared-with-k8s · [REMOVED-4.0]=exclude

### lifecycle hooks (charm/v12 hooks.go; uniter/hook/hook.go)
- install,start,config-changed,upgrade-charm,update-status,stop,remove :: X (start re-fires on k8s pod churn; once on machine)
- leader-elected :: X (leader-deposed not dispatched — C4)
- pre/post-series-upgrade :: [REMOVED-4.0] EXCLUDE
- collect-metrics,meter-status-changed :: legacy, EXCLUDE

### MO surfaces (the machine deltas to calibrate)
- MO workload delivery: snap | apt/dpkg | charm FILE resource(`resource-get` tool, `juju attach-resource`) | build-from-source. (k8s=OCI image+Pebble). no Juju hook tool except resource-get.
- MO systemd service mgmt: charm writes /etc/systemd/system/*.service + systemctl. replaces Pebble layer. not Juju-mediated (only status-set/application-version-set surface it).
- MO pkg mgmt idioms: apt sources / snap channel+confinement. proxy via model-config keys `juju-http-proxy`,`apt-http-proxy`,`snap-http-proxy` (CONFIRMED environs/config/config.go).
- MO storage block: real /dev block device; charm formats+mounts. (k8s=no raw block). + filesystem (juju mounts at location:). dynamic add/detach/attach-storage WORKS on machine (blocked on k8s).
- MO networking: spaces + endpoint bindings (`juju bind`), `extra-bindings` (space bind not tied to relation), `juju expose` direct (k8s needs juju-external-hostname).
- MO constraints: virt-type,instance-type,root-disk,root-disk-source,zones,tags,allocate-public-ip,image-id,container,spaces,cores,cpu-power,mem,arch (core/constraints/constraints.go). placement `--to N`,`--to lxd:N`,`--to zone=` (lxd/kvm container scopes).
- MO subordinate charms: `subordinate:true` + container-scoped relation (`scope:container`), universal `juju-info` iface. BIGGEST machine-only feature. principal+subordinate colocate on one machine. (k8s subordinate placement broken in 4.0 per JUJU.md)
- MO lxd-profile.yaml: charm-root file applied to hosting LXD container. whitelist devices unix-char/unix-block/gpu/usb; blacklist config boot*/limits*/migration* (core/lxdprofile/profile.go:42 ValidateConfigDevices). exercise via `--to lxd:0`.
- MO payloads: [REMOVED-4.0 CONFIRMED — C7] gone in 4.0 (no tools, no CLI). EXCLUDE.
- MO debug ergonomics: real shell for `juju ssh`/`juju debug-hooks` (vs chiselled k8s ROCK). since 3.6.19 `juju ssh unit cmd` needs `--pty=true`.

### X surfaces (shared; exercise on machine too)
- relations provides/requires/peers + 5 relation hooks + relation-get/set/ids/list/model-get.
- leadership is-leader/leader-get/leader-set.
- secrets: secret-changed/expired/remove/rotate hooks + add/set/remove/get/info-get/grant/revoke/ids tools. vault backend.
- actions (key=value in 4.0, not --params). goal-state, state-get/set/delete, application-version-set, status-set/get.

### COS observability (MAJOR structural diff)
- k8s=PULL: prometheus_scrape + grafana_dashboard + loki_push_api (3 relations, COS scrapes charm).
- MO machine=PUSH: deploy `grafana-agent` SUBORDINATE; principal provides ONE relation `cos-agent` (iface `cos_agent`, scope container) carrying metrics-jobs+dashboards+alert-rules+log-targets; agent pushes via prometheus_remote_write + loki_push_api. CONFIRMED from grafana-agent-operator charmcraft.yaml.
- => machine charm exposes `cos-agent` provider, NOT the 3 k8s COS relations.

### multi-base
- supported in 4.0: 20.04/22.04/24.04/26.04 (core/base/supportedbases.go; 26.04 live now). modern: `base: ubuntu@24.04` + `platforms:`. legacy `bases:` multi-entry (deprecated). `series:` = v1 legacy. design single-base-24.04-now/expandable-to-26.04.
- machine charm must NOT `assumes: k8s-api`.

## 3. k8s<->machine comparison (point 2a)
| feature | k8s mechanism | machine mechanism | reuse |
|---|---|---|---|
| workload run | Pebble layer in OCI container | systemd unit on host | adapt(driver) |
| workload deliver | oci-image resource | file-resource binary / snap | rewrite |
| health | Pebble checks http/exec/tcp + pebble-check events | systemd + own health probe | rewrite |
| service ops | container start/stop/restart/replan | systemctl | adapt(driver) |
| file ops | container.push/pull/exec | os/pathlib/subprocess | adapt(driver) |
| storage | PV mount via container.exists/push | host path direct os ops; +block; +dynamic attach | adapt+extend |
| ready gate | container.can_connect | systemctl is-active / process | adapt |
| scaling verb | scale-application | add-unit/-n | n/a(cli) |
| observability | 3 pull relations | 1 cos-agent push relation | rewrite-lib |
| privilege | charm-user non-root/sudoer (k8s-only) | runs as root on host | drop |
| events extra | pebble-ready/-custom-notice/-check-* | none (install/start/update-status) | drop |
| MO-new | — | subordinate, lxd-profile, spaces/bindings, machine-constraints, placement, payloads, block-storage, pkg-mgmt | new |

## 4. k8s-charm improvement backlog (point 2b — what we missed, future)
- KB1 `juju-info` provides endpoint is bare (no consumer code) — could exercise as subordinate-info provider.
- KB2 prometheus alert_rules dir present-but-empty (per CLAUDE.md) — ship a real alert rule to calibrate alert propagation.
- KB3 Ex-2 profiling/tracing still unintegrated — revisit when libs stable (already tracked as exception).
- KB4 event ledger persisted to /tmp in charm container = ephemeral (resets on pod restart); semantics differ from machine (survives). document/contrast; consider peer-data ledger option.
- KB5 Juju CI gaps the k8s charm could ALSO close (from CI-agent): typed-config convergence assertion, collect-status priority contract guard, action-fail/typed-output/progress-log. (some already covered by US3/US4/US5 — cross-check.)

## 5. architecture decision (point 3) — recommended defaults
- D-REPO: MONO-REPO, per-substrate subdirs. charmcraft packs 1 charmcraft.yaml/dir; shared code via symlinks. layout:
  ```
  <repo>/ workload/(shared Go) shared/(ops-free norma.py + reusable bits)
    k8s/charmcraft.yaml src/charm.py src/norma.py->../../shared
    machine/charmcraft.yaml src/charm.py src/norma.py->../../shared
    rockcraft.yaml(k8s-only) pyproject.toml uv.lock Makefile(shared)
    tests/unit/{shared,k8s,machine} tests/integration/{k8s,machine}
  ```
  generalizes the existing sudoer charmcraft-swap trick. Canonical norm is separate-repos(-k8s suffix) BUT mono-repo right for a calibration charm needing cross-substrate lockstep.
- D-ROCK: machine charm needs NO ROCK/OCI. drop containers/resources:oci-image/uid-gid/charm-user/assumes:k8s-api. rockcraft.yaml + rock.yaml + publish-rock.yaml stay k8s-only.
- D-WORKLOAD: deliver Norma Go binary as charm FILE resource (reuse existing workload/ build) + charm-managed systemd unit. most hermetic/air-gap/reproducible; one build feeds both charms; keeps reconciler symmetric (k8s writes Pebble layer / machine writes systemd unit, both from shared norma.py params). ALT: snap-as-resource if want snapd supervision (+snapcraft.yaml).
- D-TOOLING: single pyproject/uv.lock/Makefile; CI gains {k8s,machine} matrix; machine integration on LXD; publish-edge/release-tag/promote gain 2nd CharmHub charm-name target; machine publish triggers on push-to-main directly (NOT via ROCK workflow_run gate). norma.py stays 3.10-safe (datetime.timezone.utc not datetime.UTC).

## 6. code reuse (point 4)
- ~55-60% A reusable-as-is: event ledger, defer-gate, config validate, peer/relation data, secrets, WHOLE bad-behavior test-bed, version-file, networking action, most introspect collectors, ~9 read-only actions.
- ~25-30% B adapt-behind-driver: reconcile workload body, status ready-gate, storage/health/security/run-check/version-workload actions.
- ~12-18% C k8s-only-drop: Pebble layers, test-pebble-ops, trigger-notice, secondary-container/OCI, pebble-* events, k8s-API cred probe.
- NET ~80-85% of LOGIC reusable. seam already exists: norma.py is ops-free.
- SEAM: `WorkloadDriver` protocol (ops-free, over primitives): is_ready/apply(port,version,env)/workload_version/open_port/file ops/service_running/restart/set_health/workload_ids. PebbleDriver(k8s) + SystemdDriver(machine). add `build_systemd_unit(port,version,env)` mirroring `build_pebble_layer` signature.

## 7. CI consolidation + gaps (mission: regression guard)
- CONSOLIDATION: 1 norma-machine charm replaces ~12 in-tree machine test charms (mirrors k8s 8->1):
  dummy-source+dummy-sink (self-relatable provides+requires, like calibration iface) · appdata-source+sink (app-bag mode) · departer (peer + departing-unit) · dummy-storage{,-fs,-lp,-mp,-np,-tp} (1 config-driven storage charm, 6->1) · ubuntu-plus+simple-resolve (dispatch+actions+opt-in install-error for `juju resolve`) · space-defender (multi http provides + extra-bindings).
  KEEP-SEPARATE: lxd-profile{,-subordinate,-without-devices} (the shipped lxd-profile.yaml IS the artifact — but norma-machine CAN absorb principal case w/ optional lxd-profile.yaml) · network-health (reactive subordinate; absorbable as subordinate mode) · refresher (absorbable via multi local revs).
- HIGH-VALUE GAPS in current Juju machine CI (calibration targets):
  [GAP] leadership transitions assertion · [GAP] secret rotation/expiry hook dispatch · [WEAK] typed/param actions + action-fail · [GAP] typed config (int/bool/float/secret) convergence · [GAP] collect-status priority + ActiveStatus-empty contract · [GAP] pebble-less long-running service supervision on VM · [GAP] stop/remove idempotent teardown · [WEAK] upgrade-charm hook + data-migration (cf LP#2068500) · [WEAK]/[GAP] machine COS (cos-agent) · [GAP] tracing on machine.

## 8. proposed machine-charm coverage (draft US targets; finalize in spec)
lifecycle(install/start/config/stop/remove + systemd) · workload-via-file-resource · typed config(all 5) · status priority · 18-ish actions(reuse + machine variants) · peers+leadership · provides/requires self-relate(calibration-style) · app-databag mode · scaling+cluster-info · secrets full lifecycle(rotate/expire) · block+filesystem storage + dynamic attach/detach · networking spaces/bindings/extra-bindings/expose · machine constraints + placement(--to,lxd:N,zone) · subordinate mode(juju-info container scope) · lxd-profile.yaml · cos-agent push observability · upgrade-charm/version · event-deferral · introspect · bad-behavior test-bed(port agnostic modes) · `juju resolve` error path.
EXCLUDE: series-upgrade(C1), payloads(C7), collect-metrics/meter-status(legacy, gone 4.0), pebble/oci/notices/sidecar/charm-user(k8s-only).

## 9. OPEN DECISIONS (need user sign-off before /speckit.specify)
- Q1 repo: mono-repo (recommended) => rename repo juju-norma-k8s -> neutral (e.g. juju-norma)? cost: CharmHub source links, ghcr paths, README badges, git remotes, existing CI. OR keep repo name + add machine/ subdir (mildly mis-named). OR separate repo.
- Q2 machine CharmHub charm name: `juju-norma` (machine) vs `juju-norma-k8s` (k8s)? (machine charms conventionally no -k8s suffix). reserve/register name.
- Q3 workload delivery: confirm FILE-resource+systemd (recommended) vs snap-as-resource.
- Q4 governance: shared constitution w/ platform-tagged principles vs sibling constitution. (k8s principles like "no StoredState: pods recreate" have different rationale on machines where local disk survives.)
- Q5 consolidation ambition: full ~12->1 replacement target (mission-aligned) vs minimal feature-coverage charm. (affects US count/scope.)
- Q6 confirm EXCLUDE series-upgrade (C1) for 4.0 target. (recommend exclude.)

## 10. DECISIONS (locked 2026-05-28)
- D1 repo: SEPARATE repo now (not mono). rationale: merge-later is bounded one-time effort (git subtree + subdir move + CI matrix) ≈ same work as now; k8s charm already shipped/stable so shared-code drift risk low; separate-first avoids destabilizing the live k8s pipeline. MITIGATION for cheap future merge: structure reusable logic as a `norma_common/`-shaped module mirroring eventual `shared/`; promote to a shared lib only if drift becomes real. CharmHub names: juju-norma-k8s (existing) + juju-norma (machine).
- D2 workload: FILE-resource (reuse existing workload/ Go build) + charm-managed systemd unit. snap-as-resource = documented alternative, not chosen.
- D3 governance: SHARED constitution, platform-tagged principles (mark k8s-only rules e.g. Pebble; per-substrate rationale e.g. StoredState). NOT a sibling constitution.
- D4 scope: FEATURE-COVERAGE-FIRST. exercise all machine features for confidence; do NOT (yet) target explicit replacement of the ~12 upstream charms. consolidation remains a later expansion (§7 retained as roadmap, not v1 scope).
- D5 (implied) target Juju 4.0+; EXCLUDE series-upgrade(C1) + payloads(C7); bases 24.04 now, expandable to 26.04.

next: create separate machine repo (mirror-structured) → /speckit.specify the machine calibration charm there (feature-coverage US set from §8, minus excludes). pending user go-ahead to scaffold.
