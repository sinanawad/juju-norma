# juju-norma :: development plan (strict)

meta:
  charm: juju-norma (machine/IAAS calibration); sibling of juju-norma-k8s
  target_juju: "4.0+" (verified vs /data/dev/juju@4.0 v4.0.10-242 + juju 4.0.6 CLI)
  base: ubuntu@24.04 (expandable→26.04)
  cloud: LXD (dev+CI)
  scope: FEATURE-COVERAGE-FIRST (D4). upstream charm consolidation = roadmap, not v1.
  decisions: docs/SYNTHESIS.md §10 (D1-D5). capability source of truth: SYNTHESIS §1.5/§2.
  governance: .specify/memory/constitution.md (platform-tagged).

## GUARDRAILS (anti-hallucination — enforced per feature/task)
- G1 TRACEABILITY: every feature cites a SYNTHESIS line tagged CONFIRMED-4.0. no feature without source-verified capability. (this rule killed series-upgrade + payloads.)
- G2 TESTABLE-AC: every feature has an exact `juju` CLI command proving it on a live LXD deploy (Constitution VIII). unit tests never close a feature alone.
- G3 FACTS-vs-ASSUMPTIONS: claims not yet source/CLI-confirmed tagged `ASSUMPTION:` → must verify before the owning task is "done". assumptions never silently become facts. ledger §A.
- G4 TASK-RIGOR: dependency-ordered; each task names exact files/mechanisms; build one feature → `make lint && make unit` → live CLI → review → next. never batch.
- G5 CONSTITUTION-GATE: §G must stay PASS; new exceptions go to constitution Complexity Tracking with justification.

## ARCHITECTURE
repo layout (mirror-structured for cheap future mono-repo merge — D1):
```
juju-norma/
  charmcraft.yaml          # type:charm; NO containers/oci/charm-user/k8s-api
  pyproject.toml uv.lock Makefile
  workload/                # Go binary source (port of juju-norma-k8s/workload/)
  src/
    charm.py               # ops lifecycle, reconciler, actions, status
    norma.py               # ops-free: systemd-unit build, config, ports, constants
    norma_common/          # reusable bits (mirror of eventual shared lib): ledger, defer, config-validate, badbehavior, relations, secrets, introspect collectors
    workload_driver.py     # WorkloadDriver protocol + SystemdDriver impl
    prometheus_alert_rules/ grafana_dashboards/
  lib/charms/              # fetched (cos_agent etc.)
  tests/unit/{test_charm.py,test_norma.py}
  tests/integration/
  docs/                    # SYNTHESIS, research/, this PLAN
```
modules:
- norma.py [ops-free]: `build_systemd_unit(port,version,env)->str` (analogue of k8s build_pebble_layer), validate_config, constants, ledger/defer file helpers. 3.10-safe.
- workload_driver.py: `WorkloadDriver` protocol + `SystemdDriver` impl (subprocess+systemctl+pathlib). **NET-NEW design (M2)** — this seam does NOT exist in the k8s sibling: the sibling charm calls Pebble directly, there is no `*Driver` class there. So defining the protocol + SystemdDriver is new abstraction work, not a port. A future PebbleDriver in the sibling is hypothetical.
- charm.py: holistic _reconcile drives SystemdDriver; collect_unit_status; dedicated handlers stop/remove/secret-rotate/expire/remove + actions.
workload delivery (D2): Go binary → charm `file` resource; install hook `resource-get`→ /usr/local/bin/norma + write systemd unit; reconcile via systemctl.

## FEATURE CATALOG (F-IDs; feature-coverage-first)
legend reuse: A=logic ported (copy/adapt from sibling SOURCE — NOT imported; no shared lib exists yet) · B=adapt (mechanism differs, Pebble→systemd) · NEW=machine-only new code. NOTE(M2): "reuse" = copying/adapting logic into this repo's norma_common; the WorkloadDriver seam itself is NET-NEW. the ~80-85% figure is LOGIC-reuse-via-copy, not lifting an existing driver/lib.
cols: id · feature · src(SYNTHESIS) · reuse · acceptance(juju CLI on LXD) · tags

- F1 lifecycle+systemd workload · §1.5 hooks, §2 MO-systemd · B · `juju deploy ./juju-norma_amd64.charm --resource norma-bin=./norma; juju status →active/idle; juju ssh 0 systemctl is-active norma →active` · CONFIRMED-4.0
- F2 workload via file-resource · §2 MO-workload-delivery, D2 · NEW · `juju attach-resource juju-norma norma-bin=./norma; juju refresh; juju ssh 0 'norma --check'` · CONFIRMED-4.0(resource-get jujuc/server.go). ASSUMPTION(A1): file-resource refresh re-runs install/upgrade path on machine — verify live. ASSUMPTION: file-resource may race Deploy() like oci #21456 — verify; add deploy retry if so.
- F3 typed config (string/int/float/bool/secret) · §2 X, k8s US3 · A · `juju config juju-norma calibration-int=9090; juju run juju-norma/0 get-config` · CONFIRMED-4.0. secret-config resolved via model.get_secret(id).
- F4 status priority + Active-empty · §2 X, Constitution VII · A · `juju run juju-norma/0 set-status status=blocked message=x; juju status →blocked "x"; set-status status=active →no msg` · CONFIRMED-4.0
- F5 actions (class-A reused + machine workload-ops) · §6 reuse · A+NEW · `juju run juju-norma/0 get-event-log|introspect|...`; NEW `test-workload-ops` (systemd/file/subprocess, replaces test-pebble-ops) · CONFIRMED-4.0
- F6 peers + leadership · §2 X, §1.5 leader-elected · A · `juju add-unit -n2; juju run juju-norma/leader get-cluster-info →is-leader,planned-units; get-peer-data` · CONFIRMED-4.0 (leader-deposed not relied on — C4)
- F7 provides/requires self-relate (calibration iface) · §7 consolidation(dummy-source/sink) · A · `juju deploy ... a; juju deploy ... b; juju integrate a:calibration-provider b:calibration-requirer; get-relation-data` · CONFIRMED-4.0
- F8 app-databag mode · §7 (appdata) · A · `juju run leader get-relation-data` shows app-scope bag propagation across units · CONFIRMED-4.0
- F9 scaling + cluster-info · §2 X (add-unit) · A · `juju add-unit juju-norma -n2; get-cluster-info →3 units`; (machine verb = add-unit, NOT scale-application) · CONFIRMED-4.0
- F10 secrets full lifecycle · §2 X secrets, k8s US9 · A · `juju run leader get-secret-info`; rotate→new rev; expire; `juju secrets` · CONFIRMED-4.0. exercise rotate/expire (a Juju-CI GAP per §7).
- F11 block + filesystem storage + dynamic attach/detach · §2 MO-storage · B+NEW · `juju deploy --storage data=10G --storage blk=1G,block; juju run check-storage; juju add-storage juju-norma/0 data=5G; juju detach-storage; juju attach-storage` · CONFIRMED-4.0. ASSUMPTION: LXD storage pool supports block + dynamic attach — verify live; block device format/mount is charm code (NEW).
- F12 networking: open-port/expose + bindings/extra-bindings · §2 MO-networking · B · `juju run test-networking →opened-ports; juju expose juju-norma; juju status →exposed` (machine expose direct, no juju-external-hostname) · CONFIRMED-4.0. ASSUMPTION/PARTIAL: spaces+`juju bind` only meaningfully testable on multi-space cloud (MAAS/EC2); plain LXD = `alpha` space only → spaces AC is WEAK on LXD, mark partial.
- F13 machine constraints + placement · §2 MO-constraints, §1.5 · B · LXD-verifiable: `juju deploy --constraints "arch=amd64 cores=2 mem=2G root-disk=8G virt-type=virtual-machine zones=<lxd-node>"`; placement `juju add-unit --to lxd:0` · CONFIRMED-4.0. LXD HONORS (M1, per LXD cloud docs): arch,cores,mem,root-disk,root-disk-source,virt-type,zones. ROADMAP(real-IAAS only): cpu-power,instance-type,instance-role,tags,allocate-public-ip,image-id,spaces.
- F14 subordinate mode (juju-info, container scope) · §2 MO-subordinate, §1.5 · NEW · separate subordinate build target; `juju integrate juju-norma-sub:juju-info juju-norma:juju-info`; co-located on same machine · CONFIRMED-4.0(meta.go:744). NOTE: `subordinate:true` is STATIC metadata — NOT config-gated (see A5). decide build approach in P5 before coding.
- F15 lxd-profile.yaml · §2 MO-lxd-profile (domain/deployment/charm/lxdprofile.go:54-70) · NEW · ship lxd-profile.yaml; `juju deploy ... --to lxd:0`; `juju show-machine 0` →profile applied · CONFIRMED-4.0. ASSUMPTION(A6): applies cleanly when host machine is itself nested LXD — verify (spike).
- F16 cos-agent push observability · §2 COS, §1.5 (grafana-agent) · NEW(lib) · provide `cos-agent` (cos_agent iface, scope container); AC1 `juju deploy grafana-agent; juju integrate juju-norma:cos-agent grafana-agent:cos-agent`; AC2 verify shipped src/prometheus_alert_rules/ PROPAGATE through cos_agent to the agent (KB2 — empty alert-rules was a k8s gap) · CONFIRMED-4.0(grafana-agent charmcraft.yaml). ASSUMPTION(A7): cos_agent charm lib name/version to fetch-libs — confirm.
- F17 upgrade-charm + version · §2 X, k8s US15 · A · `juju refresh juju-norma --path ./new.charm; juju run get-version`; unit.set_workload_version · CONFIRMED-4.0
- F18 event-deferral (defer-gate) · k8s US20, Constitution VII · A · `juju run test-defer arm=true; <fire event>; get-event-log →deferred+re-emitted` · CONFIRMED-4.0 (calibrates Juju defer/re-emit; quarantined in _on_defer_gate, never in _reconcile)
- F19 introspect · k8s US22 · A(minus container collector) · `juju run introspect sections=config,leadership,storage,relations,secrets,goal-state` · CONFIRMED-4.0. drop _collect_containers (k8s-only); add systemd-service collector.
- F20 bad-behavior test-bed · §6 (class A), k8s · A · `juju config juju-norma bad-behavior-mode=hook-error; juju resolve; ...` · CONFIRMED-4.0. PORT modes: active-with-message, blocked-no-message, stuck-maintenance, status-churn, hook-error, secret-in-relation, stuck-dying. (all status/exception/relation level — no Pebble coupling.)
- F21 juju-resolve error path · §7 (simple-resolve) · FOLDED INTO F20 · `bad-behavior-mode=hook-error`/`stuck-dying` already drive the unit to error + `juju resolve` recovery. NOT a separate feature; AC lives under F20.
- F22 stop/remove idempotent teardown · §7 [GAP], Constitution I/VII (dedicated stop/remove handlers permitted) · NEW · `juju remove-unit juju-norma/0; juju ssh <other> ... ` then on a fresh deploy `juju remove-application juju-norma; systemctl status norma →not-found; /etc/systemd/system/norma.service gone` · CONFIRMED-4.0. stop/remove handlers stop+disable service, remove unit file, `systemctl daemon-reload`; idempotent (safe if already gone). closes a real Juju-CI gap.

EXCLUDED (G1 — verified absent in 4.0): pre/post-series-upgrade, payloads, collect-metrics/meter-status, pebble-*, oci-image/containers, sidecar/secondary-container, charm-user non-root/sudoer, k8s-API credential probe, LogForwarder(pebble-native), scale-application(verb).

## LXD VERIFICATION CONSTRAINTS (honesty about what live-CLI can prove)
- FULLY verifiable on LXD: F1-F10, F14-F21, storage filesystem (F11 fs part), expose+open-port (F12 ports).
- PARTIAL on LXD (need real IAAS cloud for full matrix → roadmap): F12 spaces/bind (LXD ~single space), F13 cloud-constraints (virt-type/instance-type/zones/tags/image-id), F11 block-storage IF LXD pool lacks block (verify).
- => v1 acceptance gate = LXD-verifiable subset; cloud-only items tagged ROADMAP, not blockers. NO silent skips — each partial logged.

## PHASES (dependency-ordered; G4 one-at-a-time)
- P0 scaffold: charmcraft.yaml(machine), pyproject/uv/Makefile, workload/ Go port, norma.py + workload_driver.py skeleton, empty src/charm.py reconciler, unit-test harness. gate: make lint+unit green, `charmcraft pack` ok.
- P1 core lifecycle: F1,F2 (install→resource-get→systemd; reconcile). gate: deploy on LXD active/idle, service running.
- P2 substrate-neutral reuse (port class-A from k8s via norma_common): F3,F4,F5(class-A actions),F6,F18,F19,F20. gate: per-feature CLI.
- P3 relations: F7,F8,F17. gate: integrate + databag CLI.
- P4 machine-distinct: F9,F10,F11(fs+block),F12(ports+expose). gate: storage attach/detach + expose CLI.
- P5 machine-only-new: F14(subordinate),F15(lxd-profile),F16(cos-agent),F5(workload-ops action). gate: subordinate integrate, profile applied, grafana-agent relate.
- P6 partial/cloud: F12(spaces),F13(cloud-constraints) — LXD-subset now, full matrix ROADMAP. gate: document partial.
- P7 polish/CI: CI workflows (lint/unit/pack/integration-on-LXD), CharmHub publish (juju-norma), README, dashboards/alert-rules.

## CONSTITUTION GATE (must PASS before impl — Constitution Governance)
| principle | status | note |
|---|---|---|
| I holistic reconciler | PASS | single _reconcile; dedicated only stop/remove/secret/actions |
| II workload abstraction | PASS | norma.py ops-free; SystemdDriver seam |
| III stateless | PASS | peer data + secrets; no StoredState |
| IV security | PASS w/ EXCEPTION | secrets/token_urlsafe/no-leak; charm-user/ROCK n/a on machine; TLS exception (carried) |
| V observable | PASS w/ EXCEPTION | cos-agent push; alert-rules ship; parca/tracing exception (carried) |
| VI three-tier testing | PASS | ops.testing(no containers)+jubilant(LXD)+ruff |
| VII idempotency | PASS | prohibitions honored; defer quarantined in F18 gate (test-bed) |
| VIII CLI acceptance | PASS | every F has LXD juju-CLI AC; partials flagged |
result: PASS (2 carried exceptions: TLS, profiling — same as k8s sibling, in constitution Complexity Tracking).

## A. ASSUMPTIONS LEDGER (verify before owning task = done; G3)
- A1 [F2] file-resource refresh re-runs install/upgrade-charm path on machine. verify: live refresh + ledger.
- A2 [F11] LXD block storage: loop provider is "not officially supported" on LXD; needs an LXD storage pool (zfs/lvm/btrfs) pre-created. verify: create pool + `juju add-storage` block on LXD; if unprovisionable → F11-block ROADMAP, keep filesystem + dynamic attach/detach (IAASOnly-confirmed: add/attach/detach-storage all IAASOnlyCommand → work on LXD).
- A3 [F12] spaces/`juju bind` extent on LXD (expect alpha-only). verify: `juju spaces`; full spaces = ROADMAP.
- A4 [F13] RESOLVED — CODE-VERIFIED vs 4.0 (environ_policy.go:28-34 + container.go:53-98, 2026-06-17): LXD APPLIES arch,cores,mem,virt-type,**instance-type** (c.InstanceType, container.go:54-56) + root-disk,root-disk-source (pool-dependent: pool name defaults to literal "default", LXD rejects at create if absent). UNSUPPORTED = exactly 5, warned+dropped: cpu-power,tags,container,allocate-public-ip,image-id. No-op/stored-only on standalone LXD: instance-role (never read), zones (single-AZ), spaces (NIC wiring, not a resource spec). NOTE: earlier "instance-type is cloud-only ROADMAP" was WRONG — it is applied on LXD. Live calibration: tests/integration/test_constraints.py (P3-1).
- A5 [F14] `subordinate:true` is STATIC charm metadata (meta.go:744 requires a container-scoped requires relation) — NOT runtime/config-toggleable. => separate charmcraft/build target for the subordinate variant, OR principal-only exposing juju-info. decide in P5 BEFORE coding (blocks executor).
- A6 [F15] lxd-profile applies when controller machines are themselves LXD containers (`--to lxd:0` nesting). verify live.
- A7 [F16] exact cos_agent charm-lib name+version for `charmcraft fetch-libs`. confirm vs grafana-agent.
- A8 [P0] systemd mgmt: `charms.operator_libs_linux.v1.systemd` (NOTE: deprecated in favor of `charmlibs.systemd` v1.0 per Charmhub) vs raw subprocess. both work today; pick informed by deprecation.
- A9 workload/ Go binary: reuse juju-norma-k8s build as-is (CGO_ENABLED=0 static). confirm identical binary works headless on VM (it already runs in bare ROCK → high confidence).

## NEXT
P0 scaffold. await user go-ahead post critic-pass on this PLAN.
