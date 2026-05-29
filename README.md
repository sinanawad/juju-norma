# juju-norma

A Juju **machine (VM / IAAS) calibration charm** — a sterile test harness that
deliberately exercises Juju's machine-charm features so Juju's own CI can guard
against engine regressions. It is the machine-substrate sibling of the
Kubernetes charm [`juju-norma-k8s`](https://github.com/sinanawad/juju-norma-k8s).

> **Status: early development (scaffolding).** Spec-driven build in progress.

## What is this?

Not a production workload. It systematically exercises every machine-charm
capability — lifecycle hooks, a systemd-managed workload, block + filesystem
storage with dynamic attach/detach, network spaces & bindings, machine
constraints & placement, subordinate charms, `lxd-profile.yaml`, secrets,
leadership, and `cos-agent` push-model observability — so a green CI run is
high-confidence evidence the Juju machine path has not regressed.

- Targets **Juju 4.0+**, base **ubuntu@24.04** (designed to expand to 26.04).
- Dev and CI on **LXD**.
- Workload (the Norma Go binary) delivered as a charm **file resource** managed
  by **systemd** — no ROCK/OCI image (that is k8s-only).

## Design & research

Capability analysis, the k8s↔machine comparison, the code-reuse map, and the
architecture decisions live in [`docs/`](docs/) — see
[`docs/SYNTHESIS.md`](docs/SYNTHESIS.md). The build follows the same
spec-driven workflow and a shared, platform-tagged constitution as the
`juju-norma-k8s` sibling.
