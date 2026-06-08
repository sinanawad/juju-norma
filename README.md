# juju-norma

[![CI](https://github.com/sinanawad/juju-norma/actions/workflows/ci.yaml/badge.svg)](https://github.com/sinanawad/juju-norma/actions/workflows/ci.yaml)

A Juju **machine (VM / IAAS) calibration charm** — a sterile test harness that
deliberately exercises Juju's machine-charm features end to end, so Juju's own CI
can use a green run as high-confidence evidence the machine path has not
regressed. It is the machine-substrate sibling of the Kubernetes charm
[`juju-norma-k8s`](https://github.com/sinanawad/juju-norma-k8s).

> **Status:** feature-complete calibration harness (F1–F22), continuously
> verified on LXD. **Not** a production workload, and not yet published to
> CharmHub. Some HTTP endpoints are intentionally unauthenticated for testing —
> see [`SECURITY.md`](SECURITY.md) and [`docs/FINDINGS.md`](docs/FINDINGS.md) §0.

## What is this?

A machine charm has **no Pebble, no workload container, no OCI image**. The
workload — a small Go HTTP server (`norma`) — is shipped as a charm **file
resource** (`norma-bin`) and supervised directly on the host by a
**charm-managed systemd unit**. The charm drives it via systemd + the filesystem
+ subprocess, behind an ops-free `WorkloadDriver` seam (the machine analogue of
the k8s charm's Pebble driver).

- Targets **Juju 4.0+**, base **ubuntu@24.04** (designed to expand to 26.04).
- Dev and CI on **LXD**.
- Every event routes through a single holistic `_reconcile()`; status via
  `collect_unit_status` only (`ActiveStatus()` carries no message).

## Quickstart

```bash
# 1. Build the static workload binary (the file resource).
make build-workload                 # produces ./norma

# 2. Pack the charm.
charmcraft fetch-libs && charmcraft pack

# 3. Deploy on an LXD controller, attaching the binary as the norma-bin resource.
#    A machine deploy is meaningless without --resource norma-bin=.
juju deploy ./juju-norma_amd64.charm --resource norma-bin=./norma

juju status                          # juju-norma → active/idle
juju exec --unit juju-norma/0 -- systemctl is-active norma   # active
```

Optionally pair the subordinate (machine-only, via `juju-info`):

```bash
cd subordinate && charmcraft pack && cd ..
juju deploy ./subordinate/juju-norma-subordinate_amd64.charm
juju integrate juju-norma:juju-info juju-norma-subordinate:juju-info
```

## Capabilities exercised

| Area | What it calibrates |
|------|--------------------|
| Lifecycle & workload | install/start/config-changed/upgrade-charm; file-resource delivery; systemd-managed workload; version stamping |
| Config & status | typed config; status priority (Blocked > Maintenance > Waiting > Active); `ActiveStatus` no-message rule |
| Actions | read-only + machine workload-ops (systemd/file/subprocess); introspection; deliberate failure paths |
| Relations | self-relate calibration provider/requirer; app + unit databags; **subordinate** via `juju-info` (container scope) |
| Peers & scaling | peer data; `add-unit`/`remove-unit`; leadership & re-election; cluster-info convergence |
| Secrets | app-owned secret lifecycle + rotation policy |
| Storage | filesystem store + **dynamic** add/detach/attach (IAAS-only); block storage (needs MAAS/cloud) |
| Networking | open-port; `juju expose`; spaces/bindings (multi-space needs MAAS) |
| Placement | `lxd-profile.yaml`; `--to lxd:N`; `virt-type=virtual-machine` (needs KVM) |
| Observability | `cos-agent` **push** model via a `grafana-agent` subordinate |
| Engine edge-cases | event deferral (defer-gate); bad-behavior test-bed; `juju resolve` error path |

Several machine features are **not verifiable on LXD** (block storage,
lxd-profile application, multi-space binding, KVM placement) — these are covered
by `xfail`/dispatch-only tiers and need MAAS / a real cloud / a nested-virt
runner. See [`docs/FINDINGS.md`](docs/FINDINGS.md) and
[`docs/CI-REFERENCE.md`](docs/CI-REFERENCE.md).

## Develop & test

```bash
make lint              # ruff + gofmt + go vet
make unit              # pytest tests/unit (ops.testing / Scenario) + coverage
make build-workload    # static Go binary
make integration-smoke # container-safe per-PR subset on an LXD controller
make integration       # full F1–F22 suite (LXD; some tiers need KVM/nesting)
```

Dependencies are managed with **uv** (`uv.lock` is committed; CI runs
`uv lock --check` + `uv sync --frozen`). Unit tests use `ops.testing` (Scenario);
integration tests use **jubilant** on a real LXD controller. See the CI matrix in
[`.github/workflows/ci.yaml`](.github/workflows/ci.yaml).

## Design & docs

- [`docs/SYNTHESIS.md`](docs/SYNTHESIS.md) — capability catalog, k8s↔machine
  comparison, code-reuse map, locked decisions D1–D5 (4.0-verified).
- [`docs/PLAN.md`](docs/PLAN.md) — anti-hallucination development plan with
  source-traceability + testable `juju` CLI acceptance.
- [`docs/FINDINGS.md`](docs/FINDINGS.md) — live LXD limitations & engine
  observations. [`docs/CI-REFERENCE.md`](docs/CI-REFERENCE.md) — CI design.
- Development is governed by the platform-tagged constitution at
  [`.specify/memory/constitution.md`](.specify/memory/constitution.md).

## License

Apache-2.0 — see [`LICENSE`](LICENSE).
