All grafana-agent claims confirmed directly from its charmcraft.yaml. I now have everything needed. Let me compile the verification table.

---

# VERIFICATION REPORT — Machine-Charm Capability Catalog

**CRITICAL CONTEXT FINDING:** The source tree at `/data/dev/juju` is **Juju 3.6.24** (branch `3.6`, `version/version.go:const version = "3.6.24"`), **NOT Juju 4.0** as the catalog's prose repeatedly implies ("the target Juju 4.0.x build", "not dispatched in 4.0"). Every `/data/dev/juju/...:line` citation in the catalog is against 3.6 code. This invalidates several "verified in 4.0" framings and is the single most important correction — see TOP CORRECTIONS.

## Verification Table

| Capability | Verdict | Source / Correction |
|---|---|---|
| **No Pebble/containers on machines; workload runs directly on host** | CONFIRMED | Workload hooks gated on `WorkloadName` (`hook/hook.go:106-117`); `containers:` is a separate map (`meta.go:291`). `IsWorkload()` only matches the 4 Pebble kinds (`hooks.go:152-159`). Conceptually correct. |
| Pebble hooks (`pebble-ready`, `-custom-notice`, `-check-failed`, `-check-recovered`) K8s-only | CONFIRMED | `hooks.go:63-66`, `WorkloadHooks()` 120-132; validation requires `WorkloadName` `hook.go:98-117`. |
| `install`, `start`, `config-changed`, `upgrade-charm`, `update-status`, `stop`, `remove` | CONFIRMED | `hooks.go:15-27`; `unitHooks` 69-84. **Line numbers in catalog are off** (catalog says install=18; actual=15). |
| `leader-elected` / `leader-deposed` / `leader-settings-changed` | CONFIRMED (exist) / PARTIALLY WRONG framing | `hooks.go:24-26`. But official hook docs **omit** `leader-elected`/`leader-settings-changed` from the public list. `leader-deposed` exists as a Kind but is **not dispatched to charms** (no hook file is run) — catalog lists it as a usable hook without caveat. |
| `collect-metrics`, `meter-status-changed` "legacy" | CONFIRMED legacy | `hooks.go:22-23`. Not in `unitHooks` dispatch list framing in docs. Catalog's "avoid" guidance correct. |
| **`pre-series-upgrade` / `post-series-upgrade`** [MACHINE-ONLY] | CONFIRMED in 3.6, **WRONG for Juju 4** | `hooks.go:28-29`; validation `hook.go:119-122` (`MachineUpgradeTarget`). BUT official docs: *"Juju 3.6 or earlier only. To be removed in Juju 4."* A Juju-4 charm spec must NOT rely on these. Catalog presents them as current. |
| `juju upgrade-machine prepare/complete` (replaces `upgrade-series`) | CONFIRMED | `cmd/juju/machine/upgrademachine.go:168 Name:"upgrade-machine"`; registered `commands/main.go:437`. (3.6 source.) |
| `hook.Info.MachineUpgradeTarget` carries target base | CONFIRMED | `hook/hook.go:58-61` (YAML key is `series-upgrade-target`). |
| Relation hooks `relation-created/joined/changed/departed/broken` | CONFIRMED | `hooks.go:40-49`; `relationHooks` 93-99. |
| Storage hooks: only `storage-attached`, `storage-detaching` | CONFIRMED | `hooks.go:55-56`, `storageHooks` 108-111; validation needs valid storage ID `hook.go:130-132`. |
| Storage hook filenames prefixed by storage name | CONFIRMED | `hooks.go:51-53` doc comment ("shared-fs-storage-attached"). |
| Storage types `block`, `filesystem` | CONFIRMED | `meta.go:55-56` (`StorageBlock`/`StorageFilesystem`). |
| `dummy-storage` declares block+fs permutations | CONFIRMED with **CORRECTION** | `testcharms/charms/dummy-storage/metadata.yaml`. Catalog claims stores named `single-fs/multi-fs/single-blk/multi-blk` with "block range 0-2" and `location: /srv/...`. Actual: fs stores have `location:` (`/srv/single-fs`, `/srv/multi-fs`), block stores have **no location**; ranges are `single-*: 0-1`, `multi-*: 0-2` (not "single-blk range 0-2"). `minimum-size: 10M` (not "10M" on single-fs only — all four). |
| Hook tools `storage-add/get/list` | CONFIRMED | `jujuc/server.go:90-92`. |
| Subordinate via `subordinate: true` + container scope | CONFIRMED | `meta.go:274` (`Subordinate bool`); `ScopeContainer="container"` `meta.go:38-39`; scope-compat `meta.go:233-235`. |
| `juju-info` interface, `scope: container` universal subordinate hook | CONFIRMED | `testcharms/charms/lxd-profile-subordinate/metadata.yaml` and `network-health/metadata.yaml:19-21` both show `juju-info`/`interface: juju-info`/`scope: container`. |
| Container-scoped relation only matches same-machine units | CONFIRMED | `meta.go:233-235` scope compatibility logic. |
| `lxd-profile.yaml` whitelist `unix-char, unix-block, gpu, usb`; blacklist `boot*, limits*, migration*` | CONFIRMED with **CITATION CORRECTION** | Whitelist/blacklist documented in `core/lxdprofile/validate.go:28-31` (comment). Actual validation runs in `core/lxdprofile/profile.go:42 ValidateConfigDevices()`. Catalog cited `validate.go:30-33` + `profile.go:42` — close; the whitelist/blacklist text is a doc comment, enforcement in profile.go. |
| Example lxd-profile.yaml (`security.nesting`, `security.privileged`, `linux.kernel_modules`) | CONFIRMED | `testcharms/charms/lxd-profile/lxd-profile.yaml` (also has `environment.http_proxy`). |
| Constraint names (arch, cores/cpu-cores, cpu-power, mem, root-disk, root-disk-source, tags, instance-type, spaces, virt-type, zones, allocate-public-ip, image-id) | CONFIRMED | `core/constraints/constraints.go:23-39`. Catalog also should note `container` is a constraint (`constraints.go:24`). |
| Placement scopes incl. machine ID + container types (`lxd`, `kvm`) | CONFIRMED | `core/instance/placement.go:42 isContainerType`→`ParseContainerType`; `container.go:16-17` (`LXD="lxd"`, `KVM="kvm"`). `--to lxd:0` valid. |
| `extra-bindings` (relation-independent space binding) | CONFIRMED | `meta.go:278`; parser `extra_bindings.go:30-39` (`ExtraBinding{Name}`). |
| Port tools `open-port/close-port/opened-ports`; `network-get`; `unit-get` | CONFIRMED | `jujuc/server.go:38,41,42,48,52`. |
| Relation tools `relation-get/set/ids/list/model-get` | CONFIRMED | `jujuc/server.go:43-47`. |
| Leadership tools `is-leader/leader-get/leader-set` | CONFIRMED | `jujuc/server.go:96-98`. |
| Secret hooks `secret-changed/expired/remove/rotate` + tools | CONFIRMED | `hooks.go:30-33`; tools `jujuc/server.go:79-86`; validation needs URI `hook.go:137-146`. |
| `goal-state`, `state-get/set/delete`, `credential-get`, `application-version-set`, `status-set/get` | CONFIRMED | `jujuc/server.go:62,65-67,63,53,50-51`. |
| **Payload tools** `payload-register/unregister/status-set` | CONFIRMED | `jujuc/server.go:106-108`. |
| **`juju payloads` CLI** [catalog marked UNVERIFIED in 4.0] | RESOLVED for 3.6; UNVERIFIED for 4.0 | Registered `commands/main.go:592 payload.NewListCommand()` — present in **3.6.24**. Catalog's "unverified in Juju 4.0" is honest; I could not check a 4.0 tree (none on disk). Caveat stands for 4.0. |
| File resources via `resource-get` + `juju attach-resource` | CONFIRMED | `jujuc/server.go:102`; `meta.Resources` `meta.go:286`. |
| Multi-base: supported Ubuntu 20.04/22.04/24.04 (focal/jammy/noble) | CONFIRMED | `core/base/base.go:130-132`; `core/base/supported.go:241-249,340,363,386`. |
| Modern `base:` + `platforms:` vs legacy `bases:`; `series:` is v1 legacy | CONFIRMED (charmcraft) / CONFIRMED | `meta.go:281 Series` marked "serialised for backward compatibility" (`meta.go:264-266`). charmcraft `base`/`platforms` vs `bases` is charmcraft-doc behavior (not in juju core). |
| **grafana-agent: subordinate, `cos-agent` (iface `cos_agent`, scope container)** | CONFIRMED | Verified directly in `canonical/grafana-agent-operator/charmcraft.yaml`: `subordinate: true`; requires `cos-agent`/`cos_agent`/container, `juju-info`/`juju-info`/container, `send-remote-write`/`prometheus_remote_write`, `logging-consumer`/`loki_push_api`; provides `grafana-dashboards-provider`/`grafana_dashboard`. |
| COS-on-machine = push (remote_write/loki_push_api) via subordinate agent | CONFIRMED | Same charmcraft.yaml — agent requires the push endpoints. Conceptually correct. |
| `juju expose` on K8s needs `juju-external-hostname`; machine `expose` direct | CONFIRMED (per JUJU.md) | Matches JUJU.md "Juju 3.6: `juju expose` on K8s requires `juju-external-hostname`". |
| Proxy model-config keys `juju-http-proxy`, `apt-http-proxy` | CONFIRMED (keys exist) | `environs/config/config.go:147 JujuHTTPProxyKey`, `:164 AptHTTPProxyKey` (also `snap-http-proxy:179`, `http-proxy:130`). Catalog marked [UNVERIFIED] — now confirmed as model-config keys. (Exact injected unit env-var names not separately verified.) |
| `Action` deprecated as a hook kind | CONFIRMED | `hook/hook.go:128-129` returns "hooks.Kind Action is deprecated". |
| systemd/apt/snap as charm-authoring patterns (not Juju mechanisms) | CONFIRMED (honest) | No Juju hook tool mediates these; correctly self-labelled [UNVERIFIED]/[MACHINE-ONLY] author patterns. |

## TOP CORRECTIONS

1. **The cited Juju source is 3.6.24, not 4.0.** `/data/dev/juju/version/version.go` = `3.6.24`, branch `3.6`. The catalog's prose ("target Juju 4.0.x build", "not dispatched in 4.0") is unsupported by the tree it grepped. Any line citation must be understood as 3.6, and 4.0-specific claims remain genuinely unverified.

2. **`pre-series-upgrade`/`post-series-upgrade` are removed in Juju 4.** Official docs: *"Juju 3.6 or earlier only. To be removed in Juju 4."* The catalog presents them (and `juju upgrade-machine`) as a current machine-only feature to exercise — for a Juju-4 calibration charm this is WRONG; it would be a no-op/unsupported. This is the highest-impact spec error.

3. **`dummy-storage` metadata details are wrong.** Block stores have **no `location:`** (only filesystem stores do), and the multiplicity ranges are `single-*: 0-1` / `multi-*: 0-2` — not the catalog's "block, range 0-2" / "single-blk range 0-2". Anyone copying the catalog's storage stanza description would mis-declare the charm.

4. **`leader-deposed` is a Kind but is not dispatched as a runnable charm hook**, and official docs omit `leader-elected`/`leader-settings-changed` from the public hook list. The catalog lists `leader-deposed`/`leader-elected` as usable hooks without this caveat.

5. **Minor line-number drift throughout** (e.g., catalog "hooks.go:18" for `install`; actual `:15`). Facts are right, offsets are ~3 lines stale — re-cite before use.

6. **lxd-profile citation:** the whitelist/blacklist is a *doc comment* at `validate.go:28-31`; enforcement is `profile.go:42 ValidateConfigDevices()`. The values themselves are CONFIRMED.

Everything else in the catalog — no-Pebble-on-machines, subordinate/container-scope/`juju-info`, constraint and placement names, `extra-bindings`, the full hook-tool inventory, secrets, payloads tooling, supported bases, and the entire grafana-agent `cos_agent` push-model observability story — is **CONFIRMED against source**.