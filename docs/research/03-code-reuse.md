I have everything I need from the three files. Here is the analysis.

# Norma calibration charm: K8s → Machine reuse analysis

## Component-by-component classification

| Component (location) | Class | Justification |
|---|---|---|
| **Event ledger** — `_log_event`, `read/write_event_ledger`, `_event_to_kebab` (`charm.py:26-33,1323-1334`; `norma.py:29-44`) | **A** | Pure logic over primitives. Only `LEDGER_FILE` path (`/tmp/...`) is a constant; works identically on a VM. The charm-container-filesystem semantics differ (no pod restart), but the code is unchanged. |
| **Defer gate / arming flag** — `_on_defer_gate`, `read/write_defer_armed` (`charm.py:170-189`; `norma.py:47-59`) | **A** | Tests Juju's defer/re-emit mechanism, which is platform-independent. File-backed flag uses local disk; identical on a machine. |
| **Holistic reconciler skeleton** — `_reconcile` event-classification/logging block (`charm.py:195-244`) | **A** | The dispatch, ledger logging, `RelationDepartedEvent`/`RelationEvent`/deferred detection is platform-agnostic. Only the `PebbleCheck*`/`PebbleCustomNotice`/`PebbleReady` branches (C) and the container body (B) are K8s-specific. |
| **Workload-driving body of reconcile** — container `can_connect`/`add_layer`/`replan`/`exec`/`push` (`charm.py:270-366`) | **B** | The *intent* (validate config → resolve secret → apply desired workload state → set version → open port → write storage markers) is reusable; the *mechanism* (Pebble layer + `container.*`) becomes systemd unit + filesystem + subprocess on a machine. |
| **Config reading + validation** — `validate_config` (`norma.py:62-79`), config dict assembly (`charm.py:276-281`) | **A** | `validate_config` is ops-free and entirely portable. Config keys/types are platform-neutral. |
| **Peer relation data** — `_update_relation_data`, `_collect_relations`, peer actions (`charm.py:669-690,802-822,1336-1396`) | **A** | Relations, leadership, databags, `planned_units()` behave identically on machine and K8s. |
| **Juju Secrets handling** — create/grant/revoke/rotate/expire/remove (`charm.py:255-263,382-395,727-753,1359-1381`) | **A** | Secrets are a controller-backed Juju primitive, identical across substrates. |
| **`collect_unit_status` / `collect_app_status`** (`charm.py:401-429`) | **B** | Logic and priority ordering are portable, but the readiness gate `container.can_connect()` → "Waiting for Pebble" must become a machine readiness check (e.g. `systemctl is-active` / process check). |
| **Actions framework** — observe wiring + simple actions (`get-event-log`, `get-config`, `set-status`, `fail-action`, `get-peer-data`, `get-relation-data`, `get-cluster-info`, `get-version`, `test-defer`) | **A** | These read model/config/ledger state only; no workload mechanism. Fully reusable. |
| **`run-check` action** (`charm.py:692-725`) | **B** | Concept (validate a capability, pass/fail) reusable; `pebble` check via `container.get_service().is_running()` becomes a systemd/process check. |
| **Introspect action + section collectors** (`charm.py:1149-1317`) | **Mostly A, partly B** | Orchestration, truncation, and collectors for identity/version/leadership/config/event-ledger/relations/secrets/goal-state are **A**. `_collect_containers` (C — Pebble plans) and `_collect_storage`'s `container.exists` marker probe (B) need adaptation. |
| **`goal-state` collector** (`charm.py:1279-1285`) | **A** | Uses the `goal-state` hook tool directly; substrate-independent. |
| **Bad-behavior-mode test-bed** — `BAD_BEHAVIOR_MODES`, `_bad_mode`, `_bad_behavior_unit_status`, `_maybe_trigger_hook_error`, `_maybe_trigger_stuck_dying`, `_inject_bad_relation_data` (`charm.py:438-576`) | **A** | All operate on status/exceptions/relation data — pure Juju-level misbehavior with no workload coupling. The whole calibration mission transfers as-is. |
| **Version handling** — `_get_charm_version`, `get-version` action, `_collect_version` (`charm.py:1001-1027,1207-1211,1398-1403`) | **B** | `_get_charm_version` (reads `version` file) is **A**. The *workload* version read via `container.exec([BINARY, "--check"])` + Pebble plan env is **B** → subprocess on the machine. |
| **`check-storage` action** (`charm.py:755-800`) | **B** | Storage *concept* (attached?, mount-point, marker, writability) reusable; `container.exists/pull/push/remove_path` → direct `os`/`pathlib` filesystem ops (no remote container; storage is mounted on the unit machine). |
| **`toggle-health` action + health flag file** (`charm.py:824-842`; `norma.py:14`) | **B** | Health-flag-file mechanism is sound; `container.push/exists/remove_path` → local filesystem writes on the VM. |
| **`check-security` action** (`charm.py:1063-1130`) | **B** | `os.getuid/getgid` and `get_cloud_spec` (trust) are **A**. Workload uid/gid via `container.exec(["id"])` → subprocess. The K8s-API-reachability probe (`/api/v1/namespaces` with bearer token) is **C** — replace with the machine cloud's credential check (or drop). |
| **`test-networking` action** (`charm.py:1029-1061`) | **A** | `opened_ports`, `get_binding().network` are core Juju model APIs on both substrates. (`set_ports`/`Port` likewise portable.) |
| **Pebble layer construction** — `build_pebble_layer`, `build_secondary_layer` (`norma.py:82-174`) | **C** | Pebble layers + Pebble checks (http/exec/tcp) have no machine equivalent. On a VM this becomes a systemd unit file (or a snap) + a separate health-check mechanism. The *parameters* (port, version, env, health URL) carry over into the new driver. |
| **`test-pebble-ops` action** (`charm.py:844-968`) | **C** | Entire suite exercises the Pebble API surface (push/pull/exec/services/signals/plan). A machine analogue would test filesystem + subprocess + systemd, i.e. a rewritten action, not a port. |
| **`trigger-notice` action + pebble-custom-notice** (`charm.py:970-999,150-152,218-219`) | **C** | `pebble notify` and the `PebbleCustomNotice` event are Pebble-only; no machine equivalent. |
| **pebble-ready / pebble-check-failed / pebble-check-recovered events** (`charm.py:106-109,213-236`) | **C** | These events do not exist on machine charms (replaced by `install`/`start`/`update-status` driving the reconcile). |
| **OCI resource / secondary container** (`charm.py:355-366,110-111`; `norma.py:11,132-174`) | **C** | Multiple containers per pod and OCI image resources are K8s-only. A machine charm installs the binary directly (resource file, apt, or snap). |
| **COS observability wiring** — `MetricsEndpointProvider`, `GrafanaDashboardProvider`, `LogForwarder` (`charm.py:154-164`) | **A (relations) / B (libs)** | The integration *pattern* and dashboards/alert rules are reusable; the `*_k8s` charm libs would be swapped for machine-COS equivalents (cos-agent / grafana-agent), so adaptation is at the import/lib level, not the logic level. |

## Why `src/norma.py`'s ops-free design helps

`norma.py` already has **zero `ops` dependency** and operates on primitives. That boundary is exactly the seam needed for cross-substrate reuse:
- `validate_config`, `read/write_event_ledger`, `read/write_defer_armed`, and all constants are **immediately shareable** with no change.
- `build_pebble_layer`/`build_secondary_layer` are the *only* K8s-specific functions, and they're pure functions returning a dict — so the abstraction point is obvious: add a sibling `build_systemd_unit(port, version, env)` that consumes the same parameters.
- Because event objects never cross into `norma.py`, the charm already extracts primitives (`port`, `version`, config dict) before driving the workload — meaning a machine charm can feed those same primitives to a different driver with no refactor of the extraction code.

## Recommended reuse architecture

Introduce a **`WorkloadDriver` interface** as the single abstraction boundary. Everything labelled (B) collapses to driver method calls; the charm body stops naming Pebble.

```
norma-charms/                      # shared repo or git submodule
├── norma_common/                  # PLATFORM-AGNOSTIC shared library (class A)
│   ├── ledger.py                  # read/write_event_ledger, _event_to_kebab
│   ├── defer.py                   # read/write_defer_armed, defer-gate helper
│   ├── config.py                  # validate_config + config dict shaping
│   ├── badbehavior.py             # BAD_BEHAVIOR_MODES, _bad_mode,
│   │                              #   _bad_behavior_unit_status, hook-error,
│   │                              #   stuck-dying, secret-in-relation
│   ├── reconcile_base.py          # event classification + logging skeleton
│   │                              #   (the substrate-neutral top of _reconcile)
│   ├── relations.py               # peer/calibration databag update + collectors
│   ├── secrets.py                 # create/grant/revoke/rotate/expire helpers
│   ├── status.py                  # collect_unit/app priority logic (driver-gated)
│   ├── introspect.py              # collectors + truncation (driver-gated probes)
│   └── actions.py                 # the class-A action bodies
│
├── norma_driver/                  # THE ABSTRACTION BOUNDARY
│   ├── base.py                    # WorkloadDriver protocol/ABC (ops-free)
│   ├── pebble_driver.py           # K8s impl: wraps ops.Container
│   └── systemd_driver.py          # machine impl: subprocess + systemd + fs
│
├── norma-k8s/                     # K8s charm: thin charm.py + PebbleDriver
└── norma-machine/                 # machine charm: thin charm.py + SystemdDriver
```

### `WorkloadDriver` interface (the seam, ops-free over primitives)

```python
class WorkloadDriver(Protocol):
    def is_ready(self) -> bool: ...                       # can_connect / unit active
    def apply(self, port: int, version: str, env: dict) -> bool: ...  # layer+replan / write+enable+restart unit
    def workload_version(self) -> str | None: ...         # exec --check / subprocess
    def open_port(self, port: int) -> None: ...
    def file_exists(self, path: str) -> bool: ...
    def write_file(self, path: str, content: str) -> None: ...
    def read_file(self, path: str) -> str: ...
    def remove_file(self, path: str) -> None: ...
    def is_writable(self, path: str) -> bool: ...
    def service_running(self) -> bool: ...                # get_service / systemctl is-active
    def restart(self) -> None: ...
    def set_health(self, healthy: bool) -> None: ...      # health-flag file
    def workload_ids(self) -> tuple[str, str]: ...        # uid/gid: exec id / os.stat
```

- **PebbleDriver** implements these via `container.add_layer/replan/push/pull/exec/get_service` using `build_pebble_layer`.
- **SystemdDriver** implements them via `pathlib`/`os`, `subprocess`/`systemctl`, and a new `build_systemd_unit()` in the shared library (mirrors `build_pebble_layer`'s signature).
- The `test-pebble-ops` action stays K8s-only; the machine charm adds a parallel `test-workload-ops` action exercising the SystemdDriver. `trigger-notice`, the secondary container, and pebble-* events simply don't exist in the machine charm.

## Rough reusability estimate

- **(A) Reusable as-is: ~55-60%** — event ledger, defer gate, config validation, peer/relation data, secrets, the whole bad-behavior test-bed, version-file handling, networking action, most introspect collectors, and ~9 read-only actions.
- **(B) Reusable with adaptation behind the driver: ~25-30%** — the reconcile workload body, status readiness gate, storage/health/security/run-check/version-workload actions. Logic survives; only the driver calls change.
- **(C) K8s-only, no machine port: ~12-18%** — Pebble layers, `test-pebble-ops`, custom notices, secondary container/OCI resource, pebble-* events, K8s-API credential probe.

**Net: roughly 80-85% of the charm's *logic* is reusable** (A fully + B's logic), with the ~15% genuinely K8s-specific isolated cleanly behind the `WorkloadDriver` seam that `norma.py`'s existing ops-free design already sets up.

Key files referenced: `/data/dev/juju-norma-k8s/src/charm.py`, `/data/dev/juju-norma-k8s/src/norma.py`, `/data/dev/juju-norma-k8s/docs/architecture-and-design.md`.