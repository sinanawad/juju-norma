# juju-norma :: implementation progress ledger

Durable, repo-native progress tracker for the feature-coverage build (PLAN
P0–P7, F1–F22). Replaces the (uninstalled) omc ultragoal ledger. Updated as
each feature passes its gates: `make lint && make unit` (unit) + live-LXD
`juju` CLI acceptance (Constitution VIII). Evidence is recorded inline.

- **Cloud:** LXD only. Controller `lxd` (juju 4.0.10), dev model `norma-dev`.
- **Branch:** `feat/feature-coverage`.
- **Juju-bug policy:** persistent abnormal behavior → check vs `/data/dev/juju@4.0`
  source; if a genuine engine bug, mark the feature LIMITATION and move on.

## Status legend
`TODO` · `WIP` · `UNIT` (unit-green) · `DONE` (unit + live-LXD CLI verified) ·
`LIMITATION` (blocked by env/juju issue, documented) · `ROADMAP` (cloud-only, not LXD-verifiable)

## Phases / features

| Phase | F | Feature | Status | Evidence |
|---|---|---|---|---|
| P0 | — | scaffold (charmcraft/pyproject/uv/Makefile/workload/norma.py/driver/charm skeleton/tests) | DONE | lint+unit green; `charmcraft pack` → juju-norma_amd64.charm |
| P1 | F1 | lifecycle + systemd workload | DONE | deploy → active/idle; `systemctl is-active norma`→active; `/health`→OK |
| P1 | F2 | workload via file resource | DONE | `--resource norma-bin=./norma` laid to /usr/local/bin/norma 0755; idempotent apply (no restart storm) |
| P2 | F3 | typed config (string/int/float/bool/secret) | TODO | |
| P2 | F4 | status priority + Active-empty | TODO | |
| P2 | F5 | actions (class-A + machine workload-ops) | TODO | |
| P2 | F6 | peers + leadership | TODO | |
| P2 | F18 | event-deferral (defer-gate) | TODO | |
| P2 | F19 | introspect (machine: drop containers, add systemd collector) | TODO | |
| P2 | F20 | bad-behavior test-bed | TODO | |
| P3 | F7 | provides/requires self-relate (calibration iface) | TODO | |
| P3 | F8 | app-databag mode | TODO | |
| P3 | F17 | upgrade-charm + version | TODO | |
| P4 | F9 | scaling + cluster-info | TODO | |
| P4 | F10 | secrets full lifecycle (rotate/expire) | TODO | |
| P4 | F11 | block + filesystem storage + dynamic attach/detach | TODO | |
| P4 | F12 | networking: open-port/expose | TODO | |
| P5 | F14 | subordinate mode (juju-info, container scope) | TODO | |
| P5 | F15 | lxd-profile.yaml | TODO | |
| P5 | F16 | cos-agent push observability | TODO | |
| P5 | F5b | test-workload-ops action (systemd/file/subprocess) | TODO | |
| P6 | F12s | spaces / bindings (LXD = alpha only) | TODO | likely PARTIAL/ROADMAP on LXD |
| P6 | F13 | machine constraints + placement | TODO | LXD honors arch,cores,mem,root-disk,virt-type,zones |
| P7 | — | CI (lint/unit/pack/integration-LXD), README, dashboards/alert-rules | TODO | |

## Findings / Juju + environment issues
_(appended as discovered; feeds the final report)_

- None yet.

## Limitations (juju-bug or cloud-only; feature skipped on LXD)
_(appended as discovered)_

- None yet.
