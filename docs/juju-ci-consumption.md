# Juju-CI consumption contract (juju-norma)

How Juju's own CI (`juju/juju`) should consume **juju-norma** — the **machine
(IAAS / LXD)** calibration charm — when it stands in for the upstream *machine*
test charms. This is the machine-substrate analogue of the k8s sibling's
`juju-norma-k8s/docs/juju-ci-consumption.md`. For every charm it can replace or
augment, it states *how* to obtain juju-norma and the *exact* deploy args.

> Verified against `/data/dev/juju` @ branch `4.0` (`v4.0.10-289`) + live
> `juju 4.0.6/4.0.10` on an LXD controller, 2026-06-08. Concrete CharmHub
> revisions drift — confirm current numbers with `juju info juju-norma` or
> `charmcraft status juju-norma`. Live-substrate caveats are grounded in
> `docs/FINDINGS.md`.

## Purpose (what this charm guarantees to Juju CI as a regression guard)

juju-norma is a **sterile machine-charm fixture**: it deliberately exercises the
machine (IAAS / VM) feature surface of the Juju engine so that Juju CI can catch
engine regressions on the machine path. It is the machine sibling of
juju-norma-k8s — **a different animal**: no Pebble, no workload container, no OCI
image. The Norma Go workload ships as a charm **file resource** (`norma-bin`) and
is supervised on the host by a **charm-managed systemd unit**.

Concretely, juju-norma is a single charm that re-implements the *capabilities*
of a spread of upstream machine test charms, plus closes gaps Juju's current
machine CI does not cover. What it guarantees to a CI consumer:

- **Lifecycle + host supervision** — `install → start → config-changed → stop →
  remove`, with the workload laid down via `resource-get` and run under
  `systemd` (the machine analogue of the k8s Pebble layer). `resource-get`
  returns a filesystem path (`internal/worker/uniter/runner/jujuc/resource-get.go`),
  which the charm copies into place.
- **Relations** — `provides`/`requires` self-relate over the `calibration`
  interface (provider `calibration-provider`, requirer `calibration-requirer`),
  a `norma-peers` peer relation, app-databag propagation, and the five relation
  hooks including departing-unit semantics.
- **Leadership** — `leader-elected`, `is-leader`, `leader-get`/`-set`,
  re-election on leader removal (a machine-only behaviour: K8s rejects named-unit
  removal).
- **Typed config** convergence (string / int / float / bool / secret) and the
  **collect-status priority contract** (Blocked > Maintenance > Waiting > Active;
  `ActiveStatus()` carries **no** message).
- **Storage** — filesystem storage (`data`) plus dynamic
  `add-storage`/`detach-storage`/`attach-storage`, which are machine-only
  (`cmd/juju/storage/{add,attach,detach}.go` all embed `IAASOnlyCommand`).
- **Networking** — `open-port` / `juju expose` directly (no
  `juju-external-hostname`), endpoint bindings; spaces/`bind` is real-cloud-only.
- **Subordinate** colocation over `juju-info` (`scope: container`).
- **Secrets**, **actions** (typed params, action-fail, progress log),
  **upgrade-charm + workload version**, **cos-agent push observability**, and a
  deliberate **bad-behavior test-bed** for status/error/teardown calibration.

It is a **fixture, not a production application**: it embeds test-only behaviour
(`bad-behavior-mode` config, an event-deferral test gate) and intentionally omits
production pillars (TLS, profiling). Do not cargo-cult it. See `docs/FINDINGS.md`
§0 for the intentional affordances (unauthenticated `POST /toggle-health`, root
systemd unit with no sandboxing, broadly-scoped app secret) that are safe only
because the workload is a throwaway echo.

## Upstream machine test charms juju-norma replaces / augments

Grounded in `juju/juju` `testcharms/charms/` + the bash suites under
`tests/suites/`. The upstream machine charms are **v1** (`metadata.yaml` with
`series:`); juju-norma is a **v2** (`charmcraft.yaml`, `base: ubuntu@24.04`)
*capability re-implementation*, **not a byte-for-byte drop-in** — `series:` is a
FormatV1 key that conflicts with v2 keys (`domain/deployment/charm/meta.go`
format detection), so suites pinned to a specific upstream charm's wire
behaviour must be adapted, not merely re-pointed. juju-norma covers the
*capability*; per-suite assertions (endpoint names, ports, uids, storage names)
must be rewritten.

| Upstream charm(s) | Suite(s) (`tests/suites/…`) | What it exercises | juju-norma surface that covers it | Caveat |
|---|---|---|---|---|
| `dummy-source` + `dummy-sink` (CharmHub `juju-qa-dummy-source`/`-sink`) | `cmr`, `deploy`, `relations`, `firewall` | provides+requires self-relate over an interface; relation data | `calibration-provider` (provides) ↔ `calibration-requirer` (requires), interface `calibration`; `get-relation-data` | endpoint/interface names differ (`calibration`, not `dummy-token`) — rewrite assertions |
| `appdata-source` + `appdata-sink` (`juju-qa-appdata-source`/`-sink`) | `appdata` | **application-level** relation databag send/receive across units | app-bag propagation on `calibration-*`; `get-relation-data` shows app-scope bag | rewrite endpoint names |
| `departer` (in-tree `pack_charm`) | `relations` (`relation_departing_unit.sh`) | peer relation + departing-unit in `*-relation-departed` | `norma-peers` peer relation; `get-peer-data`; departed-unit handling | the charm guards `relation-get` during teardown (a `ModelError` there once crashed units) — consumers reading relation data on departed/broken should do the same |
| `dummy-storage`, `dummy-storage-{fs,mp,np,tp}` (CharmHub `juju-qa-dummy-storage`; the `-fs/-lp/-mp/-np/-tp` variants `pack_charm`-ed in-tree) | `storage`, `hooktools` (`storage_tools.sh`) | filesystem storage; storage hooks; dynamic add/attach/detach | `data` filesystem storage (range 1-5); `check-storage`; `storage-attached`/`-detaching` hooks; dynamic `add/detach/attach-storage` | filesystem only on LXD; multi-pool matrix needs a real cloud |
| `dummy-storage-lp` (in-tree; `--storage disks=loop,1G`) | `storage` (`model_storage_block`) | the `loop`/block storage-pool path; `single-blk`/`multi-blk` on a cloud | `blk` (type `block`, range 0-1) **declared** | **ROADMAP** — LXD rejects charm `block` storage (`internal/provider/lxd/storage.go` `Supports` returns true only for `StorageKindFilesystem`; FINDINGS A.2). Upstream block path is exercised on **EBS/AWS** (`storage/persistent_storage.sh`), not LXD. Augments, does not yet replace |
| `ubuntu-plus` (in-tree `pack_charm`) | `deploy` (`deploy_charms.sh`), `hooks` (`dispatch.sh`) | dispatch entrypoint + actions | full v2 dispatch; the 18 calibration actions (`get-event-log`, `run-check`, `introspect`, …) | — |
| `simple-resolve` (in-tree `pack_charm`) | `deploy` (`deploy_charms.sh`) | opt-in install error → `juju resolve` recovery | `bad-behavior-mode=hook-error`/`stuck-dying` → unit error → `juju resolve` (recovers cleanly; exercised by `tests/integration/test_badbehavior.py`) | — |
| `refresher` (CharmHub `juju-qa-refresher --revision=1`) | `refresh` | refresh across charm revisions; workload version | `juju refresh` across local revs / CharmHub channel; `get-version`; `upgrade-charm` | refresh re-binds **resource bytes**, not topology (see Cross-cutting) |
| `space-defender` (in-tree `pack_charm`; `--bind "defend-a=alpha defend-b=isolated"`) | `spaces_ec2` | multiple `http` providers bound across **spaces** | `calibration-provider` + `cos-agent` endpoints + `juju bind`; `test-networking` | space-defender has **no** `extra-bindings` (only the `lxd-profile*` charms do); spaces/`bind` across >1 space is **real-cloud-only** (LXD exposes only `alpha`; FINDINGS D) — **augments** |
| `lxd-profile`, `lxd-profile-without-devices` (CharmHub `juju-qa-lxd-profile-without-devices`; also `pack_charm`-ed) | `deploy` (`deploy_charms.sh`, `deploy_bundles.sh`) | charm-root `lxd-profile.yaml` applied to the hosting LXD container | ships `lxd-profile.yaml` (devices whitelist `unix-char`/`unix-block`/`gpu`/`usb`; config blacklist `boot*`/`limits*`/`migration*` per `domain/deployment/charm/lxdprofile.go`) | **ROADMAP** on localhost LXD — profile not applied to top-level instances; nested `--to lxd:N` did not provision (FINDINGS A.3). Needs a non-nested LXD host / MAAS |
| `lxd-profile-subordinate`, `network-health` (CharmHub `juju-qa-network-health`) | `network` | **subordinate** colocation over `juju-info` (`scope: container`) | `juju-info` provider (`scope: container`); subordinate build variant | network-health is itself a **subordinate** principal-side test; only a `juju-norma-subordinate` build (`subordinate: true`) replaces the subordinate role — the principal build provides `juju-info`, it does not *consume* it |

**Honesty note (D4):** the synthesis floated "1 charm replaces ~12." In practice
juju-norma is a *capability* re-implementation under D4 (feature-coverage-first);
explicit drop-in replacement of each upstream charm is **roadmap**. The
block-storage, lxd-profile-application, and multi-space cases are *augmented*
(declared + unit-verified) but not yet live-replaceable on LXD CI — they need a
real cloud / non-nested host. Everything else above is LXD-verifiable today.

## How to consume it

Juju CI obtains machine test charms two ways, and juju-norma supports both:

| Mode | What it is | Use when |
|------|-----------|----------|
| **In-tree testcharm** | Vendor this repo into `juju/juju` `testcharms/charms/`, `pack_charm` it, deploy by local path with `--resource norma-bin=<path>`. | Replacing charms Juju CI builds in-tree (`departer`, `space-defender`, `ubuntu-plus`, `simple-resolve`, the `dummy-storage-*` variants), or when CI needs a hermetic, per-commit charm. |
| **CharmHub** | `juju deploy juju-norma --channel=… [--revision N] --resource norma-bin=<rev>`. | Replacing charms Juju CI already pulls from CharmHub (the `juju-qa-*` family), and **mandatory** for any test that refreshes between *numbered resource revisions* (a local pack uploads only one). |

The upstream split is real: `tests/includes/charmcraft.sh` defines `pack_charm()`
(`charmcraft pack -p ./testcharms/charms/<name>` → echoes a local `.charm` path),
used for `departer`/`space-defender`/`ubuntu-plus`/`simple-resolve`/`dummy-storage-*`;
the `juju-qa-*` charms (`dummy-source`, `dummy-storage`, `network-health`,
`refresher`, …) are pulled from CharmHub.

### Mode A — in-tree testcharm (vendored into juju/juju)

For the in-tree targets. Pack from source and deploy by path, attaching the Go
binary as the `norma-bin` **file** resource.

```bash
# 1. Vendor this repo at juju/juju testcharms/charms/juju-norma/
#    (the charm root: charmcraft.yaml, src/, workload/, lxd-profile.yaml, …)

# 2. Build the workload binary (statically linked, CGO_ENABLED=0).
make build-workload                 # produces ./norma (the file resource)

# 3. Pack the charm. Mirrors tests/includes/charmcraft.sh pack_charm():
charmcraft pack -p ./testcharms/charms/juju-norma
#   -> ./juju-norma_amd64.charm   (and/or _arm64)

# 4. Deploy by path, attaching the file resource (NOT --resource <name>=<oci-ref>):
juju deploy ./juju-norma_amd64.charm --resource norma-bin=./norma

# In a suite, the pack_charm helper returns the path:
juju deploy "$(pack_charm ./testcharms/charms/juju-norma)" --resource norma-bin=./norma
```

Variants:

```bash
# Self-relate (replaces dummy-source/-sink, appdata-source/-sink):
juju deploy ./juju-norma_amd64.charm a --resource norma-bin=./norma
juju deploy ./juju-norma_amd64.charm b --resource norma-bin=./norma
juju integrate a:calibration-provider b:calibration-requirer

# Storage (replaces dummy-storage filesystem path), 'data' is range 1-5:
juju deploy ./juju-norma_amd64.charm --resource norma-bin=./norma --storage data=1G

# Subordinate role (replaces network-health / lxd-profile-subordinate):
#   built from the subordinate variant (subordinate: true; requires juju-info container scope)
juju deploy ./juju-norma-subordinate_amd64.charm --resource norma-bin=./norma
juju integrate juju-norma-subordinate:juju-info juju-norma:juju-info
```

> A local pack uploads exactly **one** resource revision. Any test that refreshes
> *between numbered resource revisions* must use Mode B.

### Mode B — CharmHub

For the CharmHub (`juju-qa-*`) targets, and **mandatory** for refresh-across-
resource-revision tests. The `norma` binary ships as a **numbered CharmHub file
resource** named `norma-bin`, served by CharmHub — no external registry needed.

```bash
# Track the engine-under-test (edge is where regressions land first):
juju deploy juju-norma --channel=latest/edge

# Reproducible pin (byte-identical charm + the resource rev bound to that charm rev):
juju deploy juju-norma --channel=latest/edge --revision N

# Pin a specific numbered file-resource revision explicitly:
juju deploy juju-norma --channel=latest/edge --resource norma-bin=<resource-rev>

# Refresh across numbered resource revisions (replaces refresher; the in-tree
# pack path CANNOT do this — local packs hold only one resource rev):
juju refresh juju-norma --resource norma-bin=<N>
```

Reproducibility: pin a **`--revision N`** for byte-identical runs; use
**`--channel=latest/edge`** to track the engine. Do **not** assume
`latest/stable` is fresh — there is no automated promotion cadence. File
resources are hash-addressed (SHA-384, `domain/resource/state/resource.go`), so a
fixed charm revision resolves byte-identical forever.

## Cross-cutting contract

- **Resource name is `norma-bin`, type `file`** (filename `norma`) — **not**
  `oci-image`, not `ubuntu`, not `app-image`. Adjust every `--resource` flag
  accordingly. This is a machine charm: there is no OCI image, no `containers:`,
  no `assumes: k8s-api`, no `charm-user`. The workload is delivered as a file via
  `resource-get` (returns a filesystem path) and supervised under systemd.
- **Workload port is `8080`** (config `calibration-int`, default 8080). Suites
  asserting other ports must be updated.
- **Storages**: `data` (filesystem, range 1-5) is the LXD-verifiable one;
  supply `--storage data=1G` where a suite needs persistence, or accept the
  default. `blk` (block, range 0-1) is **declared** for capability coverage but
  does **not** provision on LXD (filesystem-only provider) — exercise it only on
  EBS/MAAS/OpenStack.
- **Subordinate is `juju-info`, `scope: container`.** The principal build
  *provides* `juju-info`; the subordinate role (`subordinate: true`, *requires*
  `juju-info` container-scoped) is a **separate build variant** — `subordinate`
  is static metadata, not config-toggleable.
- **Topology binds at DEPLOY, not REFRESH** (FINDINGS A.1): a `juju refresh` that
  adds a new endpoint (e.g. `cos-agent`), widens a storage count range, or adds
  `lxd-profile.yaml` does **not** apply to already-deployed units (`ERROR …
  endpoints "X" do not exist`; `storage name "X" not supported by charm`). A
  **fresh deploy** with the new metadata works. `juju refresh` re-binds the
  **resource bytes** (and runs `upgrade-charm`), not the unit's topology. Any
  refresh-based test that needs new topology must redeploy.
- **Numbered resource revisions exist only on CharmHub.** A local `charmcraft
  pack` uploads a single resource revision; tests that refresh *between* resource
  revisions must use Mode B.
- **This is a calibration fixture.** It deliberately embeds test-only behaviour
  (`bad-behavior-mode`: `none` | `active-with-message` | `blocked-no-message` |
  `stuck-maintenance` | `status-churn` | `hook-error` | `secret-in-relation` |
  `stuck-dying`; plus a defer test-gate) and intentionally omits production
  pillars (TLS, profiling). It is a *feature-exercise fixture*, not a production
  application. See `docs/FINDINGS.md` §0.
- **Teardown:** because `bad-behavior-mode` can drive a unit to `error`/`dying`,
  use `juju destroy-model --force --no-wait` for cleanup (a plain destroy hangs
  on an errored unit; FINDINGS execution note). On localhost LXD, run heavy
  machine-provisioning suites **one per `pytest` process** to avoid host
  contention.

## Verification (the juju CLI acceptance a consumer can run)

All commands below are LXD-verifiable and grounded in shipped capability. Each is
the acceptance check for a corresponding upstream-charm replacement.

```bash
# --- lifecycle + systemd workload (replaces ubuntu-plus dispatch path) ---
juju deploy ./juju-norma_amd64.charm --resource norma-bin=./norma
juju status                                        # -> active/idle
juju exec --unit juju-norma/0 -- systemctl is-active norma   # -> active
#   (juju exec runs as the agent/root; reliable without an ssh key — FINDINGS C.6)

# --- typed config convergence ---
juju config juju-norma calibration-int=9090
juju run juju-norma/0 get-config                   # -> reflects 9090

# --- status priority + Active-empty contract ---
juju run juju-norma/0 set-status status=blocked message=x
juju status                                        # -> blocked "x"
juju run juju-norma/0 set-status status=active
juju status                                        # -> active, NO message

# --- provides/requires self-relate (replaces dummy-source/-sink) ---
juju deploy ./juju-norma_amd64.charm a --resource norma-bin=./norma
juju deploy ./juju-norma_amd64.charm b --resource norma-bin=./norma
juju integrate a:calibration-provider b:calibration-requirer
juju run a/0 get-relation-data endpoint=calibration-provider

# --- app-databag mode (replaces appdata-source/-sink) ---
juju run a/leader get-relation-data endpoint=calibration-provider  # app-scope bag

# --- peers + leadership + departing-unit (replaces departer) ---
juju add-unit juju-norma -n2
juju run juju-norma/leader get-cluster-info        # -> is-leader, planned-units
juju run juju-norma/leader get-peer-data
juju remove-unit juju-norma/leader                 # -> re-election (machine-only)

# --- filesystem storage + dynamic attach/detach (replaces dummy-storage fs) ---
juju deploy ./juju-norma_amd64.charm --resource norma-bin=./norma --storage data=1G
juju run juju-norma/0 check-storage name=data
juju add-storage juju-norma/0 data=1G              # IAASOnly: works on machine
juju detach-storage <storage-id>
juju attach-storage juju-norma/0 <storage-id>

# --- networking: open-port + expose (machine = direct, no juju-external-hostname) ---
juju run juju-norma/0 test-networking              # -> opened-ports incl. 8080
juju expose juju-norma
juju status                                        # -> exposed

# --- subordinate over juju-info (replaces network-health / lxd-profile-subordinate) ---
juju deploy ./juju-norma-subordinate_amd64.charm --resource norma-bin=./norma
juju integrate juju-norma-subordinate:juju-info juju-norma:juju-info
juju status                                        # subordinate colocated on the same machine

# --- cos-agent push observability (machine PUSH model) ---
juju deploy grafana-agent --channel=2/stable       # latest/stable does not exist (FINDINGS C.7)
juju integrate juju-norma:cos-agent grafana-agent:cos-agent
#   the provider side pushes metrics jobs + alert rules + dashboards through the
#   single cos-agent relation (grafana-agent then needs a downstream COS)

# --- refresh + workload version (replaces refresher) ---
juju refresh juju-norma --path ./new.charm --resource norma-bin=./norma
juju run juju-norma/0 get-version
#   CharmHub path for cross-resource-revision refresh:
#   juju refresh juju-norma --resource norma-bin=<N>

# --- error path + juju resolve (replaces simple-resolve) ---
juju config juju-norma bad-behavior-mode=hook-error
juju resolve juju-norma/0                           # recovers cleanly

# --- idempotent teardown (Juju-CI gap this charm closes) ---
juju remove-application juju-norma
#   then on the host: systemctl status norma -> not-found;
#   /etc/systemd/system/norma.service removed

# --- read progress-emitting actions: stdout only (FINDINGS C.8) ---
juju run juju-norma/0 introspect --format=json 2>/dev/null
```

**Cloud-only (ROADMAP, not LXD-verifiable):** charm `block` storage (`blk`),
`lxd-profile.yaml` application, and multi-space `juju bind` need EBS / MAAS /
OpenStack or a non-nested LXD host. These are declared + unit-verified; live CLI
acceptance is deferred to a real cloud (FINDINGS §A.2, §A.3, §D).
