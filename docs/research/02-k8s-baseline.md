# juju-norma-k8s — Complete Feature Inventory (Baseline for Machine-Charm Comparison)

All facts extracted from `charmcraft.yaml`, `src/charm.py`, `src/norma.py`, `rockcraft.yaml`, `docs/`, and `specs/001-calibration-charm/spec.md`. Authoritative file paths cited inline.

---

## 1. User Stories — Feature + Mechanism

| US | Feature | Mechanism (HOW) |
|---|---|---|
| US1 | Lifecycle events | Every lifecycle event observed via `framework.observe(...)` → `_on_defer_gate` → `_reconcile`. Each logged to in-memory event ledger (`_log_event`, `charm.py:1323`) persisted to charm-container disk `/tmp/norma-event-ledger.json` (`norma.py:25`, resets on pod restart). `get-event-log` action reads it. Event name derived via `_event_to_kebab` (CamelCase→kebab). |
| US2 | Pebble workload mgmt | `norma_pebble_ready` observed; `_reconcile` builds layer via `norma.build_pebble_layer`, `container.add_layer(combine=True)` + `container.replan()` (`charm.py:304-306`). Workload = Go binary `/bin/norma`. **K8S-SPECIFIC.** |
| US3 | Config (all types) | `config: options` declares string/int/float/boolean/secret + `bad-behavior-mode` (`charmcraft.yaml:60-127`). Read via `self.config.get(...)`; validated by `norma.validate_config`; secret config resolved via `self.model.get_secret(id=uri).get_content(refresh=True)` (`charm.py:288-298`). |
| US4 | Status reporting | `collect_unit_status` / `collect_app_status` only (`charm.py:401-429`). Priority Blocked>Maintenance>Waiting>Active. `_forced_status` set by `set-status` action. `ActiveStatus()` no message. |
| US5 | Actions | 18 dedicated `_on_*_action` handlers. `event.set_results()`, `event.fail()`, `event.log()`, `event.params.get()`. |
| US6 | Peer relations & leadership | `norma-peers` peer endpoint (`interface: norma_peers`). Leader writes `relation.data[self.app]` (cluster-size, leader-unit, secret-id); units write `relation.data[self.unit]` (`_update_relation_data`, `charm.py:1336`). `self.unit.is_leader()`. |
| US7 | Provides/Requires relations | `calibration-provider` (provides) + `calibration-requirer` (requires, limit:1), shared `calibration` interface (self-relatable across two app instances). Full relation lifecycle observed; `event.departing_unit` captured (`charm.py:209`); `remote-app` captured for CMR (`charm.py:222`). |
| US8 | Scaling | `self.app.planned_units()`, peer `relation.units` enumeration via `get-cluster-info` action (`charm.py:802`). K8s: `juju scale-application`. |
| US9 | Juju Secrets | Leader `self.app.add_secret({password}, rotate=MONTHLY)` (`charm.py:1361`). Secret ID stored in peer app data; granted to calibration-provider relations via `secret.grant(rel)`; revoked on relation-broken `secret.revoke()` (`charm.py:260`). `secrets.token_urlsafe(24)`. |
| US10 | Storage | `data` + `logs` filesystem storage; markers written via `container.push` (`charm.py:341`). `storage-attached`/`storage-detaching` observed. `check-storage` action tests writability. **PV/StatefulSet mechanism is K8S-SPECIFIC (mounts via container).** |
| US11 | Pebble health checks | Layer `checks`: HTTP (`/health`, level ready), exec (`/bin/norma --check`, level alive), TCP (port, level alive) (`norma.py:108-128`). `norma_pebble_check_failed`/`recovered` observed; `event.info.name` captured. **K8S-SPECIFIC.** |
| US12 | Pebble file ops & exec | `test-pebble-ops` action: push/pull/make-dir/list-files/exec/exec-fail/remove-path/exists + service stop/start/restart/get-services/get-plan + send-signal SIGHUP (`charm.py:844-968`). **K8S-SPECIFIC.** |
| US13 | Pebble custom notices | `trigger-notice` action execs `/charm/bin/pebble notify <key> k=v` inside container (`charm.py:992`). `norma_pebble_custom_notice` observed; `event.notice.key` captured. **K8S-SPECIFIC** (note: agent does not dispatch the event on Juju 3.6/4.0 — known limitation). |
| US14 | Networking & ports | `self.unit.set_ports(ops.Port("tcp", port))` (`charm.py:323`). `test-networking` action: `self.unit.opened_ports()`, `self.model.get_binding(endpoint).network` bind/ingress addresses (`charm.py:1029`). |
| US15 | Upgrade/refresh | `upgrade_charm` observed → reconcile. Version from `version` file written by charmcraft (`_get_charm_version`, `charm.py:1398`). `self.unit.set_workload_version()`. `get-version` action. |
| US16 | Multiple containers | `norma` + `norma-secondary` containers, same OCI image. Independent `pebble-ready` events; `norma.build_secondary_layer` runs binary on port 8081 (`charm.py:355-365`, `norma.py:132`). **K8S-SPECIFIC.** |
| US17 | Non-root security & trust | `charm-user: non-root`; container `uid/gid: 584792`. `check-security` action: `os.getuid/getgid`, `self.model.get_cloud_spec()` (trust), `credential-get` via cloud_spec.credential, hits K8s API `/api/v1/namespaces` with bearer token (`charm.py:1063-1130`). **K8s API reachability is K8S-SPECIFIC.** |
| US18 | COS observability | `MetricsEndpointProvider` (jobs target `*:8080`), `GrafanaDashboardProvider`, `LogForwarder(relation_name="log-proxy")` (`charm.py:155-164`). Endpoints: metrics-endpoint, grafana-dashboard, log-proxy. **LogForwarder uses Pebble native log forwarding — K8S-SPECIFIC.** |
| US19 | Cross-model relations | Same provides/requires endpoints; `remote-app` name captured in relation events (`charm.py:222`). No code difference vs same-model. |
| US20 | Event deferral | `_on_defer_gate` (`charm.py:170`) — `event.defer()` quarantined here, never in `_reconcile`. Armed via `test-defer` action; flag persisted to `/tmp/norma-defer-armed`. Skips defer for update-status & relation-broken. |
| US21 | OCI resource lifecycle | `pebble-ready` re-fire detected; ledger annotated `trigger=resource-refresh-or-restart` (`charm.py:226-236`). `juju attach-resource`. **K8S-SPECIFIC (OCI image resource).** |
| US22 | Introspection | `introspect` action aggregates 10 collectors (`REPORT_SECTIONS`, `charm.py:36`): identity, version, leadership, config, event-ledger, relations, storage, containers, secrets, goal-state. 250KB truncation. goal-state via `self.model._backend._run_tool("goal-state","--format","json")`. |
| US23 | Multi-arch OCI | `platforms: [amd64, arm64]` in both charmcraft.yaml & rockcraft.yaml; Go cross-compile `GOARCH=${CRAFT_ARCH_BUILD_FOR}` (`rockcraft.yaml:65`). **OCI image is K8S-SPECIFIC.** |
| US24 | Multiple storage defs | `data` (required) + `logs` (optional, `multiple-range: 0-1`). `STORAGE_CONFIG` (`norma.py:20`). `check-storage name=logs`. |
| ~~US25~~ | ~~Subordinate~~ | **REMOVED** — K8s subordinates unsupported. `juju-info` provides endpoint retained as bare interface. |
| US26 | Publication pipeline | `upstream-source: ghcr.io/sinanawad/juju-norma:latest`; GitHub Actions publish-oci.yaml + release.yaml + dependabot.yml (CI, not charm runtime). |

---

## 2. Config Options

| Option | Type | Default | Mechanism |
|---|---|---|---|
| `calibration-string` | string | "default" | `validate_config`: non-empty (`norma.py:67`) |
| `calibration-int` | int | 8080 | Also workload port; validated 1–65535; passed to Pebble layer env `PORT` |
| `calibration-float` | float | 1.0 | validated >0.0 |
| `calibration-bool` | boolean | true | passed through |
| `calibration-secret` | secret | (unset) | resolved via `model.get_secret(id=uri)` |
| `bad-behavior-mode` | string | "none" | test-bed dispatch, 8 modes (see §6) |

---

## 3. Actions (18)

`get-event-log`, `get-config`, `set-status`, `get-peer-data`, `get-relation-data`, `get-cluster-info`, `get-secret-info`, `check-storage`, `test-pebble-ops`, `trigger-notice`, `toggle-health`, `test-networking`, `check-security`, `get-version`, `fail-action`, `test-defer`, `run-check`, `introspect`.

Mechanism: each is a dedicated `_on_<name>_action` observer (`charm.py:131-148`); results are flat string maps; structured data JSON-encoded. **K8s-dependent actions:** `test-pebble-ops`, `trigger-notice`, `toggle-health`, `check-storage` (uses container.exists/push), `run-check` (pebble), `get-version` (container exec), `check-security` (workload uid/gid + K8s API).

---

## 4. Relations / Endpoints

| Endpoint | Role | Interface | Limit/Opt | Mechanism |
|---|---|---|---|---|
| `norma-peers` | peer | `norma_peers` | — | leader/unit databags, secret-id sharing |
| `calibration-provider` | provides | `calibration` | optional | data exchange + secret grant/revoke |
| `calibration-requirer` | requires | `calibration` | optional, limit:1 | self-relatable |
| `metrics-endpoint` | provides | `prometheus_scrape` | optional | MetricsEndpointProvider (lib) |
| `grafana-dashboard` | provides | `grafana_dashboard` | optional | GrafanaDashboardProvider (lib) |
| `juju-info` | provides | `juju-info` | optional | bare interface, no code |
| `log-proxy` | requires | `loki_push_api` | optional, limit:1 | LogForwarder (lib) |

**Charm libs:** prometheus_scrape v0, grafana_dashboard v0, loki_push_api v1.
**Documented exceptions (Ex-1, Ex-2):** NO `tls-certificates`, NO profiling (`parca_scrape`/`tracing`).

---

## 5. Storage / Containers / Resources / Image

| Item | Value | Mechanism |
|---|---|---|
| Storage `data` | filesystem, 1G, required | mount `/var/lib/norma`, marker `calibration-marker.json` |
| Storage `logs` | filesystem, 512M, `multiple-range: 0-1` | mount `/var/log/norma`, marker `logs-marker.json` |
| Container `norma` | resource `juju-norma-image`, uid/gid 584792 | Pebble layer, mounts data+logs |
| Container `norma-secondary` | same image, uid/gid 584792 | secondary layer, no storage mount |
| Resource `juju-norma-image` | oci-image, `ghcr.io/sinanawad/juju-norma:latest` | upstream-source pull |
| ROCK | `base: bare` + busybox-static `/bin/sh` (FR-038), `run_user: _daemon_` | Go binary CGO_ENABLED=0 static, Pebble service `norma`, `/bin/norma` |

---

## 6. Test-bed: `bad-behavior-mode` (8 modes)

`none` (compliant baseline), `active-with-message`, `blocked-no-message`, `stuck-maintenance`, `status-churn` (`_bad_behavior_unit_status`, `charm.py:465`), `hook-error` (`_maybe_trigger_hook_error` raises in reconcile), `secret-in-relation` (`_inject_bad_relation_data` writes plaintext password/api-key to provider databag), `stuck-dying` (`_maybe_trigger_stuck_dying` raises on teardown events: stop/remove/relation-broken/relation-departed). Unknown values → fallback to "none" with warning.

---

## 7. Dedicated (non-reconciler) Handlers

`_on_stop`, `_on_remove`, `_on_secret_rotate` (set_content fresh token), `_on_secret_expired` (remove_revision), `_on_secret_remove` (remove_revision), `_on_collect_unit_status`, `_on_collect_app_status`, `_on_defer_gate`, all 18 action handlers.

---

## 8. ⚠ INTRINSICALLY K8S-SPECIFIC FEATURES (blind spots when comparing to a machine charm)

These rely on the sidecar/Pebble/OCI K8s model and have **no direct machine-charm equivalent** (machine charms use systemd/snap/subordinate patterns, not Pebble-in-a-sidecar):

1. **Pebble layers & service control** — `build_pebble_layer`, `add_layer`, `replan`, `start/stop/restart`, `get_plan`, `get_services`, `send_signal` (US2, US12).
2. **Container file/exec ops** — `container.push/pull/exists/list_files/make_dir/remove_path/exec` (US12, check-storage, get-version, trigger-notice).
3. **OCI image resource** — `juju-norma-image` resource + `upstream-source` + `attach-resource` lifecycle (US21, US23). Machine charms use snap/apt, file resources, not OCI images.
4. **Sidecar / multiple containers** — `norma` + `norma-secondary` (US16). The container-per-workload model is K8s-only.
5. **`pebble-ready` event** — `norma_pebble_ready`, `norma_secondary_pebble_ready` (US2, US16, US21). No machine equivalent (machine charms use install/start directly on the host).
6. **`pebble-custom-notice` event** — `norma_pebble_custom_notice` + `pebble notify` exec (US13).
7. **`pebble-check-failed` / `pebble-check-recovered` events + Pebble checks** — HTTP/TCP/exec health checks mapping to K8s liveness/readiness probes (US11).
8. **Container uid/gid + chiselled bare ROCK + busybox `/bin/sh`** — distroless OCI packaging (US17, FR-038). Machine charms run on the host OS directly.
9. **`charm-user: non-root`/`sudoer`** — K8s sidecar charm-user privilege model (US17, Ex-4). Machine charms run as root on the host.
10. **K8s API direct access** via `credential-get` token hitting `/api/v1/namespaces` (US17/FR-039) — endpoint/auth specifics are CAAS cloud-spec shaped.
11. **Pebble-native log forwarding** via `LogForwarder` (US18) — uses the Pebble socket, not a host log agent.
12. **`assumes: [k8s-api]`** + **`platforms` multi-arch OCI manifest** + **`scale-application`** scaling verb (vs machine `add-unit`).
13. **Storage as PV/StatefulSet volume mounts accessed through the container** (`container.exists/push` against mount points) — machine storage is a host path the charm accesses directly, with no Pebble indirection.
14. **Event ledger & defer-flag persisted to `/tmp` in the charm container** (`norma.py:25-26`) — ephemeral-pod assumption; resets on pod restart (a machine charm's local disk survives agent restarts, changing the semantics of FR-001).

**Substrate-neutral features** (should port to a machine charm with mechanism changes): config types, status collection, peer relations & leadership, provides/requires relations, secrets lifecycle, actions, networking/ports (`set_ports`/`opened_ports`/bindings), event deferral, COS metrics/dashboard relations, cross-model relations, goal-state, introspection, the `bad-behavior-mode` test-bed (except the Pebble-dependent paths), upgrade-charm/version reporting, expose/unexpose.

**Notable omissions to flag** (not exercised, so cannot be baseline-compared): `tls-certificates` (Ex-1), profiling/tracing (Ex-2), Prometheus alert rules dir present-but-empty per CLAUDE.md, no `juju-info` consumer code (bare endpoint only).