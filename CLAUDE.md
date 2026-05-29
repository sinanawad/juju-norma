# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

juju-norma is a **machine (VM / IAAS) Juju calibration charm** built with the `ops` framework (Python 3.10+). It is the machine-substrate sibling of [`juju-norma-k8s`](https://github.com/sinanawad/juju-norma-k8s): a sterile test harness that deliberately exercises every Juju machine-charm capability so Juju's own CI can guard against engine regressions.

**They are different animals.** A machine charm has NO Pebble, NO workload containers, NO OCI image. The workload runs directly on the host OS under systemd; the charm installs and supervises it itself. Do not copy K8s/Pebble patterns from the sibling into this charm.

All development is governed by the **constitution** at `.specify/memory/constitution.md` (platform-tagged). The constitution supersedes all other practices — never contradict it.

## Authoritative research (read before building)

- `docs/SYNTHESIS.md` — capability catalog, k8s↔machine comparison, code-reuse map, and locked decisions D1–D5. **4.0-verified** against Juju source `v4.0.10-242` + live `juju 4.0.6`. §1.5 holds the authoritative 4.0 facts.
- `docs/PLAN.md` — the strict, anti-hallucination development plan (feature list with source-traceability + testable `juju` CLI acceptance + facts-vs-assumptions tagging).
- `docs/research/` — the 5 research dossiers (3.6-sourced; superseded by SYNTHESIS §1 for version-sensitive claims).

## Locked decisions (docs/SYNTHESIS.md §10)

- **D1** Separate repo (this one), structured for a cheap future merge into a mono-repo with the k8s sibling. Reusable logic lives in a `norma_common/`-shaped module mirroring an eventual shared lib.
- **D2** Workload delivery: the Norma Go binary as a charm **file resource** + a charm-managed **systemd** unit. (No ROCK/OCI — that is k8s-only.)
- **D3** Shared, platform-tagged constitution.
- **D4** **Feature-coverage-first** scope; explicit replacement of upstream Juju machine test charms is roadmap, not v1.
- **D5** Target **Juju 4.0+**, base **ubuntu@24.04** (expandable to 26.04, which is live in 4.0). **EXCLUDE** `pre/post-series-upgrade` and `payloads` — both removed in Juju 4 (verified).

## Build & Development Commands

```bash
make lint              # ruff check + format check on src/ and tests/
make fmt               # ruff auto-fix + format
make unit              # pytest tests/unit with coverage
make integration       # pytest tests/integration (requires LXD Juju controller)
charmcraft pack        # build the .charm artifact
charmcraft fetch-libs  # pull declared charm libraries
```

Dependencies managed with `uv` (not pip/tox). `uv.lock` must be committed. Single config in `pyproject.toml`. Linting uses `ruff` exclusively (line-length 99, py310). Keep the shared workload module **3.10-safe** (`datetime.timezone.utc`, not `datetime.UTC`).

## Architecture

### Two-Module Separation (Constitution II)

- **`src/charm.py`** — Juju lifecycle, relations, status. Imports `ops`. All events route to a single `_reconcile()` (holistic reconciler).
- **`src/norma.py`** (+ `norma_common/`) — workload logic. **Zero `ops` dependency.** Plain-pytest testable. Builds the **systemd unit** (the machine analogue of the k8s Pebble layer) from primitives (port, version, env).

Event objects must never reach the workload module. Extract primitives in the charm, pass them down.

### WorkloadDriver seam (the reuse boundary)

The k8s charm drives its workload via Pebble (`container.add_layer/replan/push/pull/exec`); this charm drives it via systemd + filesystem + subprocess. Both sit behind an ops-free `WorkloadDriver` protocol (`is_ready`, `apply(port,version,env)`, `service_running`, `restart`, file ops, `workload_version`, `set_health`). This charm implements **`SystemdDriver`**. ~80–85% of the sibling's logic is reusable behind this seam.

### Workload delivery (Constitution Tech Stack)

- Compiled Go binary (`workload/`) attached as a charm **file resource**; fetched with `resource-get` in `install`.
- `install` lays the binary down + writes `/etc/systemd/system/<svc>.service`; `config-changed`/`start` reconcile it via `systemctl`.
- No Pebble, no `containers:`, no `resources: oci-image`, no `assumes: k8s-api`, no `charm-user` (no effect on machines).

### Holistic Reconciler (Constitution I)

Every event (install, start, config-changed, relation-*, storage-*, secret-*, update-status) → `_reconcile()`: read all inputs → compute desired state → write outputs (systemd + status). Dedicated handlers ONLY for `stop`, `remove`, actions, secret rotation/expiration.

### Status (Constitution VII)

`collect_unit_status` / `collect_app_status` exclusively. Priority Blocked > Maintenance > Waiting > Active. `ActiveStatus()` with **no message**.

## Constitutional Prohibitions (hard rules)

- `event.defer()` as control flow · `StoredState` · blocking ops (sleep/poll) · `ActiveStatus` with a message · `ErrorStatus` for recoverable issues · passing `ops` events to workload code · hardcoded values · legacy `Harness` (use `ops.testing`/Scenario) · `pytest-operator` (use `jubilant`) · `flake8`/`black`/`isort` (use `ruff`) · `tox.ini` (use `Makefile`).

## Machine-charm specifics (vs the k8s sibling)

- **No Pebble / containers / OCI / ROCK.** systemd-managed workload on the host.
- **Storage**: block + filesystem; dynamic `add-storage`/`detach-storage`/`attach-storage` WORK on machines (blocked on k8s).
- **Networking**: spaces, endpoint bindings, `extra-bindings`; `juju expose` works directly (no `juju-external-hostname` needed).
- **Subordinate charms** (machine-only): `juju-info` / container-scoped relations.
- **lxd-profile.yaml** (machine-only): exercise via `--to lxd:N`.
- **Observability**: `cos-agent` (interface `cos_agent`, scope container) PUSH model via a `grafana-agent` subordinate — NOT the k8s pull trio.
- **EXCLUDED (removed in Juju 4)**: series-upgrade hooks/CLI, payloads.

## Testing (Constitution VI)

- **Unit** (`ops.testing` / Scenario): machine `State` has **no containers** — assert on systemd/file side effects and the computed unit-file content from the ops-free module. `tests/unit/test_norma.py` is plain pytest, zero ops.
- **Integration** (`jubilant`, `temp_model()`): real deploys on an **LXD** controller.
- **CLI Acceptance (Constitution VIII)**: every feature verified on a live LXD deploy via `juju` CLI before it is "done". Unit tests alone never suffice.

## Naming Conventions

- Charm name: `juju-norma` (machine charms take **no** `-k8s` suffix).
- Event handlers: `_on_<event_name>`; handler order in `__init__` matches declaration order.
- Config options: dashes in YAML, underscores in Python.

## Juju Ecosystem Knowledge

@/data/dev/juju-brain/JUJU.md

### Project-specific Juju notes

- **Dev/CI cloud**: LXD (local + CI). User has LXD locally; `~/go/bin/juju` is 4.0.6.
- **jubilant quirks**: `j.cli()` auto-injects `--model` — use `include_model=False` for `destroy-model`, `add-model`. `temp_model()` doesn't accept `cli_binary`.
- **Verify against 4.0**: Juju source at `/data/dev/juju` (branch `4.0`). Re-confirm any capability before targeting it; charm meta/hooks live in `domain/deployment/charm/` in 4.0.
