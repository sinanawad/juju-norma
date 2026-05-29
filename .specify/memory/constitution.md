<!--
  Derived from juju-norma-k8s Constitution v1.1.0 (2026-02-12).
  This is the platform-tagged sibling for the machine (IAAS/VM) charm.
  Each principle is tagged [both] / [k8s-only] / [machine-only] so the two
  charms share one governance model. Shared (tagged [both]) principles MUST
  stay in sync with the k8s sibling; substrate-specific clauses diverge.
-->

# juju-norma (machine) Constitution

Platform-tagged. Tags: **[both]** applies to k8s + machine · **[machine-only]**
this charm only · **[k8s-only]** sibling only (listed for parity, not enforced here).

## Core Principles

### I. Holistic Reconciler Architecture **[both]**

All event handling MUST route to a single `_reconcile()` that reads all inputs
(config, relations, workload state), computes the complete desired state, and
writes outputs (workload + status), returning early with appropriate status
when preconditions are unmet. The event payload SHOULD be ignored.

Dedicated handlers permitted ONLY for: `stop`, `remove`, action events, and
secret rotation/expiration.

**Machine note**: the events that drive reconcile are `install`, `start`,
`config-changed`, `relation-*`, `storage-*`, `secret-*`, `update-status` —
there is **no `pebble-ready`** (that is k8s-only). The reconcile "write
outputs" step manages a **systemd unit**, not a Pebble layer.

**Rationale**: The delta pattern causes status overwriting, deferred-event
accumulation, and ordering bugs. Holistic reconcile is order-independent and
idempotent.

### II. Workload Abstraction **[both]**

Workload logic MUST live in a module with ZERO `ops`/Juju dependency.

- `src/charm.py` handles Juju lifecycle, relations, status.
- The ops-free module handles workload config: **systemd unit construction**
  (machine) — the analogue of the sibling's Pebble-layer construction (k8s) —
  plus port/version/env shaping.
- Independently testable with plain pytest.
- Event objects MUST NOT be passed to workload code; extract primitives first.
- Cross-substrate reuse rides a `WorkloadDriver` seam: this charm implements
  `SystemdDriver`; the sibling implements `PebbleDriver`.

**Rationale**: Decoupling enables pure-Python testing and ~80–85% logic reuse
between the machine and k8s charms.

### III. Stateless by Default **[both]**

Charms MUST NOT use `StoredState` for persistent data. When state is required:
(1) re-read from workload/environment, (2) peer relation data, (3) Juju
storage, (4) database relations. Peer-data rules: only the leader writes
`relation.data[self.app]`; values are strings (JSON for complex data); store
Juju secret **IDs**, never secret values.

**Rationale (per-substrate)**:
- *k8s*: pods are ephemeral; container-local state is lost on pod recreation.
- *machine*: local disk survives agent restarts, so StoredState is less
  immediately catastrophic — BUT it is still prohibited: it does not survive
  unit teardown, machine replacement, or model migration, and consistency with
  the sibling keeps the shared code substrate-neutral. Peer relations survive
  leader failover on both.

### IV. Security-First **[both, with substrate-specific clauses]**

Mandatory on both:
- Sensitive data MUST use Juju secrets (3.0+); NEVER hardcode credentials.
- Password generation MUST use `secrets.token_urlsafe()`.
- Sensitive data MUST NOT appear in logs, traces, or exceptions.
- TLS, when a networked endpoint needs it, via the `tls-certificates` relation.
  *(Currently a documented exception in both charms — see Complexity Tracking.)*

Substrate-specific:
- **[k8s-only]** `charm-user: non-root` + container `uid`/`gid`; chiselled ROCK
  (distroless OCI image). **These have no effect on machine charms** and are
  NOT used here.
- **[machine-only]** The workload runs on the host under systemd as delivered
  by a charm **file resource** (no OCI image, no Pebble). Minimal-surface
  principle is satisfied by shipping only the static Go binary + a tight
  systemd unit, not by image chiselling.

**Rationale**: Reduce blast radius. The mechanism differs by substrate; the
intent (no hardcoded secrets, least surface, no secret leakage) is shared.

### V. Observable by Design **[both, different mechanism]**

Every charm MUST be observable. Mechanism differs:
- **[k8s-only]** PULL: `prometheus_scrape` + `grafana_dashboard` +
  `loki_push_api` (COS scrapes the charm).
- **[machine-only]** PUSH: provide `cos-agent` (interface `cos_agent`, scope
  `container`) to a `grafana-agent` **subordinate**, which pushes via
  `prometheus_remote_write` + `loki_push_api`. ONE relation carries
  metrics-jobs + dashboards + alert-rules + log-targets.
- **[both]** Prometheus alert rules ship in `src/prometheus_alert_rules/`;
  dashboards as JSON; use stdlib `logging`; do not duplicate Juju topology labels.
- **[both]** `parca_scrape`/`tracing` profiling — currently a documented
  exception (see Complexity Tracking).

**Rationale**: Observability is not an afterthought; the machine substrate has
no in-cluster scraper, hence the subordinate push model.

### VI. Three-Tier Testing **[both]**

1. **Unit** (`ops.testing` / Scenario): one event → input `State` → assert
   output `State`. NEVER legacy Harness. *Machine note*: Scenario `State` has
   **no containers**; assert on systemd/file side effects and the computed
   unit-file content from the ops-free module.
2. **Integration** (`jubilant`, `temp_model()`): real deploys. NEVER
   pytest-operator. *Machine note*: **LXD** controller (k8s sibling uses microk8s).
3. **Lint** (`ruff` only). Coverage via `coverage[toml]`.

Organization: `tests/unit/test_charm.py`, `tests/unit/test_norma.py` (plain
pytest, zero ops), `tests/integration/`.

### VII. Simplicity & Idempotency **[both]**

Every handler MUST be idempotent and decide from current model state, not from
which event fired. PROHIBITED: `event.defer()` as control flow · `StoredState`
· blocking ops · setting status in handlers (use `collect_unit_status`) ·
`ActiveStatus` with a message · `ErrorStatus` for recoverable issues ·
hardcoded values · passing `ops` events to non-charm code.

**Rationale**: Events arrive in any order and may repeat; idempotency is the
only reliable strategy on both substrates.

### VIII. CLI Acceptance Verification **[both]**

Every feature MUST be verified against a live Juju deployment via the Juju CLI
before it is "done". Unit tests alone are insufficient. Workflow: `make unit`
passes → `juju deploy` (machine charm → **LXD** model) → exercise the feature
via `juju run`/`config`/`status`/`integrate`/`add-storage`/`expose`/etc. →
verify CLI output against the acceptance criterion. A feature that passes unit
tests but fails CLI verification is **not done**.

**Rationale**: This charm is a calibration standard for Juju CI. If a feature
cannot be proven through the same CLI that CI uses, it provides no value.

## Technology Stack & Tooling

**Language**: Python 3.10+ with `ops` (CharmBase). Keep the ops-free workload
module 3.10-safe (`datetime.timezone.utc`, not `datetime.UTC`).

**Build & Dependencies** **[both]**: `uv` (lock committed), single
`pyproject.toml`, `Makefile` (no tox.ini), `charmcraft fetch-libs` for libs.

**Charm Metadata** **[machine-specific where noted]**:
- `charmcraft.yaml` is the ONLY metadata file authors edit.
- **[machine-only]** Charm name takes **NO `-k8s` suffix** (`juju-norma`).
- **[machine-only]** `assumes` MUST declare minimum Juju version and **MUST
  NOT** declare `k8s-api`.
- **[machine-only]** NO `containers:`, NO `resources: oci-image`, NO
  `charm-user`. Workload via a `file`-type resource.
- All relation endpoints declare cardinality per charmcraft convention
  (`requires` → `optional`+`limit`; `provides` → `optional`; `peers` → neither).
- Target base `ubuntu@24.04`; designed to expand to 26.04 (live in Juju 4).

**Workload** **[machine-only]**:
- Deliver the compiled Go binary as a charm **file resource**; fetch with
  `resource-get` in `install`.
- Manage via a charm-written `/etc/systemd/system/<svc>.service`:
  `systemctl daemon-reload` + `enable --now` + `restart`/`stop`.
- Wrap host operations defensively; surface failures via status.

**Status Reporting** **[both]**: `collect_unit_status`/`collect_app_status`
only; priority Blocked > Maintenance > Waiting > Active; `ActiveStatus()` with
NO message; `BlockedStatus` for operator-fixable issues.

## Development Workflow & CI/CD

**Local**: `charmcraft fetch-libs` → `make lint` → `make unit` →
`charmcraft pack` → `make integration` (LXD).

**Naming**: handlers `_on_<event_name>` (order matches declaration); config
dashes in YAML / underscores in Python; libs `lib/charms/<charm>/v<N>/<lib>.py`.

**CI/CD (GitHub Actions)** **[machine-specific]**: PR = lint, unit, lib-check,
pack, integration (on **LXD**, not microk8s). NO ROCK/OCI workflow (machine
charm has no image). Release uploads to CharmHub (`juju-norma`, no `-k8s`).
`canonical/charming-actions`; Dependabot for Actions + uv + the `workload/`
Go module.

## Exclusions (Juju 4.0 — verified absent)

The following MUST NOT be targeted (removed in Juju 4; verified against source
`v4.0.10-242` + `juju 4.0.6` CLI):
- `pre-series-upgrade` / `post-series-upgrade` hooks; `juju upgrade-machine` /
  `upgrade-series` CLI.
- `payloads` hook tools and `juju payloads` CLI.
- `collect-metrics` / `meter-status-changed` (legacy, gone).

## Complexity Tracking (justified exceptions)

- **No `tls-certificates`**: the charm calibrates Juju primitives, not infra
  security patterns; TLS is a standard relation already exercised elsewhere.
  Adds deploy complexity without testing a new Juju mechanism.
- **No `parca_scrape`/`tracing`**: profiling libs not yet stable; the three
  established observability pillars (metrics/dashboards/logs) are covered via
  `cos-agent`.

## Governance

This constitution supersedes other development practices for juju-norma.
Shared (`[both]`) principles MUST stay in sync with the juju-norma-k8s
sibling; substrate-tagged clauses may diverge. Amendments via PR with rationale
and semver bump (MAJOR principle removal / MINOR addition / PATCH wording).
Constitution Check in the development plan (`docs/PLAN.md`) MUST pass before
implementation; violations documented in Complexity Tracking. CLAUDE.md
supplements (never contradicts) this constitution.

**Version**: 1.0.0 | **Derived from**: juju-norma-k8s v1.1.0 | **Ratified**: 2026-05-28
