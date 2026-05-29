# research/ provenance

source: dynamic-workflow wf_462baba9-f6f (5 agents) + CI-coverage agent, 2026-05-28.
juju_tree_at_capture: 3.6.24 (branch 3.6) — workflow ran BEFORE branch switch to 4.0.

WARNING: dossiers 01 (machine-capabilities) and 05 (verification) cite 3.6 paths
and one 3.6-only feature framing. For any VERSION-SENSITIVE claim, the
4.0-verified facts in ../SYNTHESIS.md §1 + §1.5 OVERRIDE these files.

4.0 deltas already reconciled into SYNTHESIS (re-grepped on v4.0.10-242 + 4.0.6 CLI):
- series-upgrade hooks/CLI: REMOVED in 4.0 (C1).
- payloads tools/CLI: REMOVED in 4.0 (C7).
- charm meta/hooks relocated to domain/deployment/charm/ in 4.0 (C5).
- bases: 26.04 live in 4.0.
- everything else (subordinate, constraints, hook-tools, cos-agent, storage, spaces) CONFIRMED unchanged 3.6->4.0.

files:
- 01-machine-capabilities.md  — machine feature catalog (3.6-cited; see SYNTHESIS §1.5 for 4.0)
- 02-k8s-baseline.md          — existing juju-norma-k8s feature inventory (version-neutral)
- 03-code-reuse.md            — k8s->machine reuse map + WorkloadDriver seam (version-neutral)
- 04-architecture-packaging.md— mono-repo / no-ROCK / workload-delivery (version-neutral)
- 05-capability-verification.md — adversarial verify pass that caught the 3.6-vs-4.0 issue
