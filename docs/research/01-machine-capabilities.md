Confirmed: grafana-agent is `subordinate: true`, uses `cos-agent` (interface `cos_agent`, scope `container`) to attach to a principal, and forwards via `prometheus_remote_write` + `loki_push_api` (push model). I now have all source-confirmed facts to produce the dossier.

---

# Machine (IAAS / VM) Charm Capability Dossier

Foundation catalog for a "machine calibration" charm that deliberately exercises every machine-charm feature so Juju CI can verify the IAAS path. Every claim is cited to Juju source (`/data/dev/juju`), the vendored charm metadata library (`github.com/juju/charm/v12@v12.1.1`), or an official doc. Tags: **[MACHINE-ONLY]** = no K8s equivalent; **[UNVERIFIED]** = could not confirm in a source.

## 0. The Core Difference: No Pebble, No Containers

On a machine charm the workload runs **directly on the host OS** (a bare-metal box, VM, or system container), managed by the charm hooks via systemd/processes/packages. There is **no `charm` container, no Pebble, no workload sidecar**. The four Pebble-driven hooks (`pebble-ready`, `pebble-custom-notice`, `pebble-check-failed`, `pebble-check-recovered`) and the `containers:` metadata stanza are K8s-only — they fire only for charms with a `Containers` map (`charm/v12/meta.go:291`, `hook.go` workload-hook validation requires `WorkloadName`). The agent inside a machine unit is `jujud` (machine/unit agent), not `containeragent`; hook tools are served by `cmd/jujuc` over a local socket.

Consequence for the calibration charm: the install/start/config-changed handlers must **lay down and start the workload themselves** (snap install, apt install, file-resource binary + systemd unit, or build-from-source), where a K8s charm would just push a Pebble layer.

---

## 1. Lifecycle & Unit Hooks

Authoritative kind list: `github.com/juju/charm/v12@v12.1.1/hooks/hooks.go:15-66`; per-hook semantics in `/data/dev/juju.my/internal/charm/hooks/hooks.go:14-105` (doc comments); validation in `/data/dev/juju/internal/worker/uniter/hook/hook.go:79-150`.

Unit hooks (`hooks.go` `unitHooks`):

| Hook | What it does | Notes |
|------|--------------|-------|
| `install` | Runs once, before any other hook. Lay down workload. | hooks.go:18 |
| `start` | Runs once, right after first `config-changed`. **On K8s also re-fires on pod churn; on machines it runs once** (hooks.go:20-23). | start the service |
| `config-changed` | After install, after upgrade-charm, on every config change, and on recovery from agent error (hooks.go:25-28) | reconcile point |
| `upgrade-charm` | After charm dir contents change on unforced upgrade (hooks.go:30-35) | data migration |
| `update-status` | Periodic (default ~5 min, `update-status-hook-interval` model config) | health poll |
| `leader-elected` / `leader-deposed` | Leadership transitions (hooks.go:44-45) | see §10 |
| `leader-settings-changed` | Non-leaders notified leader wrote leader-settings (charm/v12 hooks.go:26; legacy mechanism, superseded by app-databag) | |
| `stop` | Last hook before unit destroyed (hooks.go:37-39) | stop service |
| `remove` | Final teardown (hooks.go:41) | |
| `collect-metrics` | **Legacy/deprecated** metrics collection (charm/v12 hooks.go:22) | avoid |
| `meter-status-changed` | **Legacy** (commercial metering) (charm/v12 hooks.go:23) | avoid |

### [MACHINE-ONLY] Series / base upgrade hooks
`pre-series-upgrade` and `post-series-upgrade` (charm/v12 hooks.go:28-29; validation `hook.go:119-126` requiring `MachineUpgradeTarget`; resolver in `/data/dev/juju/internal/worker/uniter/upgradeseries/`). Fired by `juju upgrade-machine <machine> prepare <base>` / `complete` (historically `juju upgrade-series`). The charm quiesces the workload before the OS is upgraded (e.g. focal→jammy) and restores it after. **There is no K8s analogue** — K8s "base" is the OCI image and is changed by `juju refresh`, never an in-place OS upgrade. The pre-hook carries the target base (`hook.Info.MachineUpgradeTarget`, `hook.go:58-61`).

Relation, storage, and secret hooks are shared with K8s (covered in §5, §6, §10).

---

## 2. [MACHINE-ONLY] Workload Delivery on a VM

K8s charms get the workload as the OCI image declared in `containers:`. Machine charms must deliver it themselves. Four idioms (no single source enumerates these as a Juju feature — they are charm-authoring patterns; mechanism citations below where applicable):

| Method | Mechanism | When to use | Calibration coverage |
|--------|-----------|-------------|----------------------|
| **snap** | charm runs `snap install <name> --channel=...` in `install`; manages via `snap` CLI / `snapd` | most common modern idiom; auto-updates, confinement | exercise `snap install`, channel pinning, `snap set` config |
| **apt/dpkg** | charm runs `apt-get install` / `dpkg -i`; may add a PPA/apt source | classic deb workloads | exercise apt source + install + hold |
| **charm FILE resource** | OCI **or file** resource declared in `resources:` (`charm/v12/meta.go` Resources); fetched with the `resource-get` hook tool (`jujuc/server.go:102`); `juju attach-resource app name=./binary` | ship a binary/tarball with the charm | **[MACHINE-ONLY] file resources** — attach a binary, drop it on disk, make a systemd unit. (OCI resources work on both substrates.) |
| **build-from-source** | `install` hook clones/compiles (apt build-deps + `make`) | rare; reproducibility tests | exercise build toolchain in install |

`resource-get` is the only one with a first-class hook tool. The others are the charm shelling out. A thorough calibration charm should exercise **at least snap + file-resource + a systemd unit** to cover the two most distinct paths.

---

## 3. [MACHINE-ONLY] systemd Service Management

No Juju API — the charm directly writes a unit file to `/etc/systemd/system/<svc>.service` and runs `systemctl daemon-reload`, `systemctl enable --now`, `systemctl restart`, `systemctl stop` from hook code. This is the machine equivalent of pushing/replanning a Pebble layer. **[UNVERIFIED]** as a named Juju "feature" — it is a host-level operation Juju does not mediate; the only Juju surface is `status-set`/`status-get` (`jujuc/server.go:50-51`) to reflect service health and `application-version-set` (`jujuc/server.go:53`) to publish the workload version. The Operator framework's `systemd` helpers / `charmlibs` exist but are library conveniences, not Juju mechanisms.

---

## 4. [MACHINE-ONLY] Package Management Idioms

- **apt**: charm manages `/etc/apt/sources.list.d/`, runs `apt-get update`/`install`, optionally `apt-mark hold`. Proxy honored via model config `apt-http-proxy` / `juju-http-proxy` injected into the unit environment **[UNVERIFIED exact env-var names — confirm against model-config docs]**.
- **snap**: `snap install/refresh/set`, channel and revision pinning, `--classic` confinement.
- Neither has a Juju hook tool; both are host operations. On K8s these idioms are irrelevant because packages are baked into the image. This is a genuine machine-only surface worth exercising.

---

## 5. Storage on Machines

Schema: `charm/v12/meta.go` `Storage` struct, types `StorageBlock = "block"` and `StorageFilesystem = "filesystem"` (`charm/v12/meta.go:51-56,71-94`). Storage hooks: `storage-attached`, `storage-detaching` only (`hooks.go:55-56`; validation requires a valid storage ID, `hook.go:130-134`). Hook file names are prefixed by storage name, e.g. `data-storage-attached` (`hooks.go` doc, lines 91-93). Hook tools: `storage-add`, `storage-get`, `storage-list` (`jujuc/server.go:90-92`). Storage resolver: `/data/dev/juju/internal/worker/uniter/storage/`.

Reference test charm declaring all permutations: `/data/dev/juju/testcharms/charms/dummy-storage/metadata.yaml` — `single-fs`/`multi-fs` (filesystem, `location: /srv/...`, `minimum-size: 10M`, `multiple: range`) and `single-blk`/`multi-blk` (block, `range 0-2`).

| Concept | Machine | K8s contrast |
|---------|---------|--------------|
| **block** storage | A real **block device** (`/dev/...`) attached to the VM; charm formats/mounts it | K8s has no raw block to the charm in the same way |
| **filesystem** storage | Juju provisions + mounts at `location:` | K8s = PVC mounted into the pod |
| Provisioning | from a cloud storage provider/pool (`juju create-storage-pool`, `juju storage`, `juju add-storage`, `juju attach-storage`, `juju detach-storage`) | K8s = StatefulSet `volumeClaimTemplates`, **immutable after creation**; `attach-storage`/`detach-storage` blocked on K8s (per JUJU.md: `attach-storage` embeds `IAASOnlyCommand`) |
| Dynamic attach/detach | **[MACHINE-ONLY] fully supported** at runtime | **not** supported on K8s |

Multiple-instance ranges (`multiple: range: 0-2`) and `minimum-size`, `Shared`, `ReadOnly`, `CountMin/CountMax` (`meta.go:76-94`) are all expressible. The calibration charm should declare both a block and a filesystem store and exercise the attach/detach lifecycle (`juju add-storage app/0 data=10G`, `juju detach-storage`, `juju attach-storage`) — a path that is **machine-only**.

---

## 6. Networking: Spaces, Bindings, Extra-Bindings, Ports

- **Spaces & endpoint bindings**: bind charm endpoints to network spaces. `juju deploy app --bind "endpoint=space ..."`, `juju bind app endpoint=space`. Spaces: `juju add-space`, `juju move-to-space`, `juju spaces`.
- **extra-bindings** (`charm/v12/meta.go:278`, parser `extra_bindings.go:19-46`): named bindings **not tied to a relation endpoint** — for binding a charm's own listening interface to a space. metadata syntax:
  ```yaml
  extra-bindings:
    public:
  ```
- **open-port / close-port / opened-ports** hook tools (`jujuc/server.go:38,41,42`). `juju expose` / `juju unexpose` make opened ports reachable. Note (JUJU.md): on K8s `juju expose` additionally requires `juju-external-hostname`; on machines `expose` works directly — a behavioral difference worth testing.
- **network-get** hook tool (`jujuc/server.go:52`): returns bind/ingress/egress addresses for an endpoint, space-aware.
- **unit-get** (`jujuc/server.go:48`): legacy `private-address`/`public-address`.

Reference machine networking test charms: `/data/dev/juju/testcharms/charms/space-defender/` and `network-health/` (the latter is `subordinate: true`, `requires juju-info scope: container`).

K8s contrast: spaces map weakly to K8s; machine spaces/bindings against real NICs and subnets are the substantive machine surface.

---

## 7. Constraints & Placement

Constraint names (`/data/dev/juju/core/constraints/constraints.go:23-39`): `arch`, `cores` (alias `cpu-cores`), `cpu-power`, `mem`, `root-disk`, `root-disk-source`, `tags`, `instance-type`, `spaces`, `virt-type`, `zones`, `allocate-public-ip`, `image-id`.

```bash
juju deploy app --constraints "cores=4 mem=8G root-disk=50G arch=amd64 virt-type=virtual-machine zones=us-east-1a spaces=db tags=foo"
juju set-constraints app cores=2
juju add-machine --constraints "instance-type=m5.large"
```

**[MACHINE-ONLY] constraints** vs K8s: `virt-type`, `instance-type`, `root-disk`/`root-disk-source`, `allocate-public-ip`, `image-id`, `zones`, `tags` are IAAS-cloud concepts. (JUJU.md notes K8s maps only `mem`→ResourceMemory, `cpu-power`→millicores, `arch`/`zones`→nodeSelector, and **tags are rejected by OpenStack/Azure/EC2** but interpreted by MAAS+K8s — so `tags` semantics differ by provider.)

**Placement** (`/data/dev/juju/core/instance/placement.go:42-84`): scopes include machine ID and container types.
```bash
juju deploy app --to 0            # existing machine 0
juju deploy app --to lxd:0        # LXD container on machine 0   [MACHINE-ONLY]
juju deploy app --to lxd          # new machine + LXD container
juju deploy app --to zone=us-east-1a
juju add-unit app --to 1
```
`isContainerType` (`placement.go:42`) recognizes `lxd`/`kvm` scopes — **machine-only**; K8s has no `--to machine`/`lxd:N` placement.

---

## 8. [MACHINE-ONLY] Principal vs Subordinate Charms

**The single biggest machine-only feature.** A subordinate (`subordinate: true`, `charm/v12/meta.go:274`) has no machine of its own — it co-locates on the **principal's** machine and attaches via a **container-scoped** relation (`ScopeContainer = "container"`, `meta.go:38-39`; scope-compat logic `meta.go:232-235`). The universal hook is `juju-info` (interface `juju-info`, `scope: container`).

Reference: `/data/dev/juju/testcharms/charms/lxd-profile-subordinate/metadata.yaml`:
```yaml
subordinate: true
requires:
  logging-directory: { interface: logging, scope: container }
  juju-info:         { interface: juju-info, scope: container }
```
Deploy/attach:
```bash
juju deploy ./my-subordinate
juju integrate my-subordinate:juju-info principal:juju-info
```
A `scope: container` relation **only** matches units on the same machine. K8s subordinate placement is broken in Juju 4 (per JUJU.md: "unit is not assigned to a machine"). The calibration charm should ship **both** a principal and exercise attaching a subordinate (or be relatable as principal via `juju-info`).

---

## 9. [MACHINE-ONLY] LXD Profiles

Charm-supplied `lxd-profile.yaml` at charm root, applied to the LXD container hosting the unit (validation `/data/dev/juju/core/lxdprofile/validate.go:43`, `profile.go:42`). **Whitelist** devices: `unix-char, unix-block, gpu, usb`. **Blacklist** config keys: `boot*, limits*, migration*` (`validate.go:30-33` doc). Example `/data/dev/juju/testcharms/charms/lxd-profile/lxd-profile.yaml`:
```yaml
description: lxd profile for testing
config:
  security.nesting: "true"
  security.privileged: "true"
  linux.kernel_modules: nbd,ip_tables,ip6_tables
devices: {}
```
Subordinates can also carry profiles (`lxd-profile-subordinate`). `juju show-machine`/`juju status` surface profile-application state. **No K8s equivalent** (K8s uses securityContext/RBAC instead). Exercise this only when deploying to LXD containers (`--to lxd:0`).

---

## 10. Cross-Cutting Charm Features (shared with K8s, but exercised on machines)

- **Relations**: `provides` / `requires` / `peers` (`meta.go:275-277`), roles provider/requirer/peer. Relation hooks `relation-created/joined/changed/departed/broken` (`hooks.go:40-49`). Hook tools: `relation-get/set/ids/list/model-get` (`jujuc/server.go:43-47`). `juju integrate a:ep b:ep`.
- **Peer relations**: declared under `peers:`; the constitutional substitute for StoredState. `juju` auto-creates the peer relation.
- **Leadership**: hooks `leader-elected`/`leader-deposed` (`hooks.go:44-45`); tools `is-leader`, `leader-get`, `leader-set` (`jujuc/server.go:96-98`). `juju run app/leader ...`.
- **Juju Secrets**: hooks `secret-changed/expired/remove/rotate` (`hooks.go:50-53`, validation needs a secret URI `hook.go:137-146`); tools `secret-add/set/remove/get/info-get/grant/revoke/ids` (`jujuc/server.go:79-86`). `juju add-secret`, `juju grant-secret`, `juju secrets`. Works identically on machines; backend can be Vault. (No `credential-get`-K8s nuance — but `credential-get` tool exists `jujuc/server.go:63`.)
- **Actions**: declared in `charmcraft.yaml` actions stanza. `juju run app/0 action-name key=value` (Juju 4 uses `key=value`, not `--params`). Hook kind `Action` is deprecated as a hook (`hook.go:128-129`) — actions dispatch through their own path.
- **goal-state** tool (`jujuc/server.go:62`); **state-get/set/delete** unit-state tools (`jujuc/server.go:65-67`) — the agent-side KV the ops framework uses (peer-data preferred per constitution).
- **application-version-set** (`jujuc/server.go:53`) and **status-set/get** (`jujuc/server.go:50-51`).

### [MACHINE-ONLY] Payloads
`payload-register`, `payload-unregister`, `payload-status-set` hook tools (`jujuc/server.go:105-109`; client `cmd/juju/payload/`, hookctx `internal/worker/uniter/runner/context/payloads/`). Lets a charm tell Juju about workload processes/containers it started (e.g. a Docker container or process) so `juju payloads` can list them. This is a **machine-only** concept (the workload is opaque to Juju on a VM, unlike a K8s pod Juju already tracks). Worth exercising for full coverage; note it is a lightly-used, legacy-ish surface — verify CLI still present in target Juju version. **[UNVERIFIED]** whether `juju payloads` is fully wired in Juju 4.0.

---

## 11. Multi-Base / Series Support

Supported Ubuntu bases (`/data/dev/juju/core/base/base.go:130-132`): 20.04, 22.04, 24.04; series names focal/jammy/noble (`core/base/supported.go:241-249`).

**Modern (charmcraft 3.x/4.x, recommended)** — single base + platforms:
```yaml
type: charm
base: ubuntu@24.04
platforms:
  amd64:
  arm64:
```
**Legacy multi-base** (`bases:` key, deprecated):
```yaml
bases:
  - build-on: [{name: ubuntu, channel: '22.04'}]
    run-on:   [{name: ubuntu, channel: '22.04'}]
  - build-on: [{name: ubuntu, channel: '24.04'}]
    run-on:   [{name: ubuntu, channel: '24.04'}]
```
(Source: charmcraft docs, charmcraft-yaml-file reference.) Old `metadata.yaml` `series:` list (`meta.go:281`) is v1-charm legacy. `juju deploy app --base ubuntu@22.04`. Multi-base is more meaningful on machines (real OS series) than K8s (image-defined).

`assumes:` declares model requirements (e.g. `juju >= 3.x`); `k8s-api` is the K8s-only feature flag — a machine calibration charm should **not** assume `k8s-api`.

---

## 12. COS Observability — Push vs Pull (major difference)

**K8s (pull):** COS Prometheus scrapes the charm via `prometheus_scrape`; charm integrates `grafana_dashboard`, `loki_push_api`, tracing directly. (Per this repo's constitution.)

**[MACHINE-ONLY] machine (push via subordinate agent):** there is no in-cluster Prometheus to reach a VM. Instead deploy **`grafana-agent`** as a **subordinate** charm (confirmed `subordinate: true`) alongside the principal. Confirmed endpoints from its `charmcraft.yaml`:
- `requires: cos-agent` — interface **`cos_agent`**, `scope: container` — the dedicated relation a principal machine charm uses to hand metrics/dashboards/alert-rules/logs to the agent. (Also `requires juju-info, scope: container` for bare compatibility.)
- The agent then **pushes** outward: `requires send-remote-write` (interface `prometheus_remote_write`) and `requires logging-consumer` (interface `loki_push_api`); `provides grafana-dashboards-provider` (`grafana_dashboard`). grafana-agent's own README confirms it "collect[s] telemetry … and _push_ it to the COS cluster (via `loki_push_api` and `prometheus_remote_write`)."

So a machine charm provides the **`cos_agent`** relation (one relation carrying metrics jobs + dashboards + alert rules + log targets) rather than the four separate COS relations a K8s charm uses, and the data flow is **push (remote_write) from the VM**, not pull. node-exporter-style host metrics are gathered by the agent. Integration:
```bash
juju deploy grafana-agent              # subordinate
juju integrate my-machine-charm:cos-agent grafana-agent:cos-agent
juju integrate grafana-agent:send-remote-write prometheus:receive-remote-write   # cross-model to COS
```
**Calibration charm should expose a `cos-agent` (interface `cos_agent`, scope `container`) provider endpoint** — that is the canonical machine observability surface and differs structurally from the K8s charm.

---

## 13. Debugging / Operator CLI (shared, but machine-relevant)

`juju ssh app/0` (interactive into the VM — on machines there's a real shell, unlike chiselled K8s ROCKs), `juju scp`, `juju run app/0 action`, `juju exec --unit app/0 -- <cmd>`, `juju debug-hooks app/0` (intercept hooks in a tmux session — **machine-only ergonomics**, works because there's a full shell + the jujuc tools on PATH), `juju debug-log`. Note JUJU.md: since 3.6.19 `juju ssh unit cmd` needs `--pty=true` for interactive commands.

---

## 14. Suggested Calibration Coverage Matrix (machine-only features to deliberately exercise)

1. install/start/config-changed/stop/remove + systemd-managed workload (no Pebble).
2. **pre/post-series-upgrade** via `juju upgrade-machine prepare/complete`.
3. **File resource** binary delivery + `resource-get`; plus a snap-install path.
4. **block + filesystem storage** with dynamic `add-storage`/`detach-storage`/`attach-storage`.
5. **Network spaces, endpoint bindings, extra-bindings**, open-port/expose.
6. Machine-only **constraints** (`virt-type`, `instance-type`, `root-disk`, `zones`, `tags`, `allocate-public-ip`, `image-id`) and **placement** (`--to N`, `--to lxd:N`, `--to zone=`).
7. **Principal + subordinate** via `juju-info` (`scope: container`) and a custom container-scoped relation.
8. **lxd-profile.yaml** (deploy `--to lxd:0`).
9. **payloads** (`payload-register`/`status-set`/`unregister` + `juju payloads`) — **[UNVERIFIED]** in Juju 4.
10. **cos-agent** (`cos_agent`) push-model observability via grafana-agent subordinate.
11. Multi-base build (`base: ubuntu@24.04` + legacy `bases:` for 22.04/24.04), `--base` deploy.
12. Shared: peers, leadership, secrets, actions, goal-state, application-version-set.

## Open / Unverified Items
- **[UNVERIFIED]** Exact apt/snap proxy env-var names injected into the unit environment (confirm against `juju model-config` keys `juju-http-proxy`/`apt-http-proxy`).
- **[UNVERIFIED]** Whether `juju payloads` and the payload hook tools are fully wired/usable in the target Juju 4.0.x build (hook tools are registered in source at `jujuc/server.go:105-109`; client command dir exists, but runtime status in 4.0 not confirmed).
- **[UNVERIFIED]** `collect-metrics` / `meter-status-changed` are present in the charm/v12 kind list but are legacy commercial-metering hooks — almost certainly not worth exercising; confirm they're not dispatched in 4.0.
- `series:` metadata key is v1/legacy; modern charms use charmcraft `base`/`platforms`.

Key source files: hook kinds `github.com/juju/charm/v12@v12.1.1/hooks/hooks.go`; hook validation `/data/dev/juju/internal/worker/uniter/hook/hook.go`; hook tools `/data/dev/juju/internal/worker/uniter/runner/jujuc/server.go`; constraints `/data/dev/juju/core/constraints/constraints.go`; placement `/data/dev/juju/core/instance/placement.go`; lxd profile `/data/dev/juju/core/lxdprofile/{validate,profile}.go`; metadata schema `github.com/juju/charm/v12@v12.1.1/meta.go`; bases `/data/dev/juju/core/base/{base,supported}.go`; reference test charms under `/data/dev/juju/testcharms/charms/` (`dummy-storage`, `lxd-profile`, `lxd-profile-subordinate`, `network-health`, `space-defender`).