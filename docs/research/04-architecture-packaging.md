I have all the verified facts I need. Compiling the recommendation.

---

# Adding a Juju Machine Charm Alongside the Existing K8s Charm

Research basis: existing repo at `/data/dev/juju-norma-k8s` (read directly), `/data/dev/juju-brain/JUJU.md`, charmcraft official docs, and real operator repos. Citations inline; `[UNVERIFIED]` marks anything I could not confirm against a primary source.

---

## 1. Mono-repo vs Separate Repo

### How charmcraft handles multiple charms
`charmcraft pack` operates on **exactly one `charmcraft.yaml` in the current working directory** — there is no top-level "charms" grouping key and no native multi-charm build. The docs are explicit: "charmcraft.yaml is ... the only yaml file in a charm project that a charm author should edit directly," and `pack` acts on the single project directory ([charmcraft.yaml reference](https://documentation.ubuntu.com/charmcraft/en/stable/reference/files/charmcraft-yaml-file/)). `charmcraftcache` corroborates that a charm is identified by "the same GitHub repository **and relative path to charmcraft.yaml**" ([charmcraftcache](https://github.com/canonical/charmcraftcache)) — i.e. the unit of building is a directory containing one `charmcraft.yaml`.

So a mono-repo CAN build both, but only by giving each charm its own subdirectory and packing each separately (a CI matrix or per-directory `cd && charmcraft pack`).

### Patterns real projects use
- **Canonical's dominant pattern today is SEPARATE REPOS** with the `-k8s` suffix convention. Grafana Agent split its machine and k8s charms into distinct repos (`grafana-agent-k8s-operator` vs the machine charm repo) ([grafana-agent-k8s-operator](https://github.com/canonical/grafana-agent-k8s-operator); [Grafana Agent machine charm](https://charmhub.io/topics/canonical-observability-stack/tutorials/instrumenting-machine-charms)). Almost every COS charm (`loki-k8s-operator`, `grafana-k8s-operator`, `tempo-worker-k8s-operator`) is a single-substrate repo.
- **The mono-repo-with-subdirectories pattern is real and is exactly what `juju/juju`'s own test charms use** — the layout the user's project is closest to. The canonical description (from the charmcraft monorepo guidance surfaced in search): top-level directories named by substrate (`k8s/`, `machine/`), each with **its own `charmcraft.yaml`** and `src/charm.py`, with **common code symlinked** in (e.g. `src/common.py` symlinked into each, a `library/` dir of symlinks), and symlinks resolved before `charmcraft pack` runs per directory ([Manage charms — charmcraft](https://documentation.ubuntu.com/charmcraft/stable/howto/manage-charms/)).

### Recommendation: MONO-REPO with per-substrate subdirectories
For a *calibration* charm whose entire purpose is to exercise Juju across substrates and stay in lockstep, a mono-repo is the right call — shared workload (`workload/`), shared reconciler logic, one place to keep US/spec parity. Concrete layout:

```
juju-norma-k8s/                 (repo root — consider renaming repo to juju-norma)
  workload/                     # shared Go source (already here)
  shared/                       # ops-free workload module (norma.py) + reusable charm bits
  k8s/
    charmcraft.yaml             # current file, moved here (type: charm, containers, oci-image)
    src/charm.py
    src/norma.py -> ../../shared/norma.py   (symlink)
  machine/
    charmcraft.yaml             # NEW: no containers, no oci-image, no k8s-api
    src/charm.py                # snap/systemd reconcile instead of Pebble
    src/norma.py -> ../../shared/norma.py   (symlink)
  rockcraft.yaml                # k8s-only (stays at root or moves under k8s/)
  snap/snapcraft.yaml           # NEW if snap delivery chosen (see Q3)
  pyproject.toml / uv.lock / Makefile   # shared (see Q4)
  tests/unit/{k8s,machine,shared}/
  tests/integration/{k8s,machine}/
```

**Tradeoff:** mono-repo means CI must matrix over `{k8s, machine}` and the symlink resolution adds one moving part; CharmHub publishing must target two charm names from one repo (doable — each `upload-charm` step points at a different `built-charm-path`). Separate repo would be simpler per-charm but loses the "single source of truth for calibration" property and doubles maintenance of shared workload/spec. **Default: mono-repo.**

Note: the existing CI already does a crude "two charms from one yaml" trick (the sudoer variant swaps `charmcraft.yaml` in/out in `pack`) — the subdirectory approach is the clean generalization of that.

---

## 2. The ROCK Question: does a machine charm need a ROCK / OCI image?

**No. A machine charm does not need a ROCK or any OCI image. Plainly: the ROCK is a k8s-only artifact and has no role on a machine.**

Why, mechanically:
- On **k8s**, the workload runs in a **separate container** in the pod; the charm talks to it over the **Pebble** socket. The OCI image (the chiselled ROCK) *is* the workload container's filesystem + Pebble. That is why `charmcraft.yaml` for k8s declares `containers:` + `resources: {type: oci-image}` — both are **"Required for Kubernetes charms ... Kubernetes charms must declare an oci-image resource for each container"** and are simply absent on machine charms ([charmcraft.yaml reference](https://documentation.ubuntu.com/charmcraft/en/stable/reference/files/charmcraft-yaml-file/)).
- On a **machine** (VM/LXD/metal), there is **no pod, no workload container, no Pebble-in-container**. The charm runs *directly on the machine OS* and installs/runs the workload **directly on that VM** — typically as a snap or apt package supervised by systemd. The `install`/`config-changed` hooks do the provisioning the ROCK+Pebble did on k8s ([Juju hook reference](https://documentation.ubuntu.com/juju/3.6/reference/hook/)).

So the ROCK is **replaced by the workload-delivery mechanism on the VM** (snap / apt / charm file-resource / build-from-source). The repo's `rockcraft.yaml`, `rock.yaml` workflow, and `publish-rock.yaml` remain **k8s-only** and the machine charm ignores them entirely.

(Consequence for `charmcraft.yaml`: the machine variant drops `containers`, `resources: oci-image`, the `uid/gid`, and `assumes: [k8s-api]`. `charm-user` "has no effect on machine charms" so drop it too. Storage `mounts` under containers go away; top-level `storage:` can stay if the workload needs persistent dirs.)

---

## 3. Workload Delivery for the Norma Go binary on a VM

The Norma workload is a single statically-linked Go binary (`CGO_ENABLED=0`, `osusergo,netgo`, built in `workload/`). Four options:

| Option | How | Pros | Cons |
|---|---|---|---|
| **snap (as charm resource)** | Build a snap of the binary; attach as a Juju resource; `install` hook does `snap install --dangerous`/`snap ack` or pulls from store; systemd-managed by snapd | Air-gap friendly (resource can be uploaded with the charm), confinement, automatic service supervision, atomic updates/rollback, **mirrors k8s story** (snap service ≈ Pebble service) | Need a `snapcraft.yaml` + snap build in CI; snap confinement quirks for a calibration charm that pokes the system |
| **apt / deb** | Publish a `.deb` to a PPA/archive; `install` hook `apt-get install` | Native, simple if you already have a deb | You don't have a deb; standing up a PPA for a calibration binary is heavyweight; less air-gap clean |
| **charm file-resource (raw binary)** | Attach the compiled `norma` binary directly as a `type: file` resource; `install` hook copies it to `/usr/local/bin`, writes a systemd unit | Dead simple, no extra packaging toolchain, perfectly air-gap, you already produce this exact binary in `workload/` | You hand-write/manage the systemd unit and lifecycle yourself; no built-in confinement; multi-arch handled by per-arch resource uploads |
| **build-from-source in install hook** | `install` hook fetches Go toolchain + `go build` | Always current | **Violates the constitution** (no network reliance/hardcoding, sterile/reproducible builds), slow, fragile, needs build deps on the unit. Reject. |

### Recommendation: **charm `file` resource (attach the compiled binary) + a charm-managed systemd unit**, as the default; snap as the documented alternative.

Rationale for a *sterile calibration* charm:
- The repo **already builds exactly this artifact** (`go build -o .../bin/norma` in `rockcraft.yaml`/`rock.yaml`). Reusing it as a `file` resource means **one build feeds both charms** (ROCK for k8s, raw binary resource for machine) — no second packaging toolchain, no PPA, no snap store dependency.
- It is the most **air-gapped/reproducible/hermetic** path, which aligns with constitutional prohibitions on network reliance and hardcoded fetches.
- It keeps the machine charm's reconciler symmetric with k8s: where k8s `_reconcile()` writes a Pebble layer, the machine `_reconcile()` writes a systemd unit and `systemctl` state — both derived from the same ops-free `norma.py` (ports/config/command), satisfying the two-module separation rule.

Pick **snap-as-resource instead** only if you want snapd to own service supervision/confinement and are willing to add a `snapcraft.yaml` + snap build job. (snap-via-resource for air-gap is the documented Canonical pattern — [layer-snap README](https://github.com/charmed-kubernetes/layer-snap/blob/main/README.md).) For multi-arch, the `file`-resource approach needs a per-arch binary uploaded to the matching charm platform revision.

---

## 4. Shared Tooling Implications (mono-repo)

### `pyproject.toml` / `uv` / `uv.lock`
Keep **one** `pyproject.toml` + `uv.lock` at the repo root. The machine charm adds **no new runtime deps** — it still uses `ops`, `cosl`, `pyyaml`; it just won't import Pebble container APIs. If you want machine-specific helpers (e.g. a snap lib like `charms.operator_libs_linux`), add them to the same `dev`/runtime list. Both charms' `charmcraft.yaml` use `plugin: uv` and the same lockfile — that's the intended uv-mono-repo flow. Note current `requires-python = ">=3.12"`, but MEMORY.md flags a 3.10 local-dev venv; the shared `norma.py` must stay 3.10-safe (e.g. `datetime.timezone.utc`, not `datetime.UTC`).

### `Makefile`
Extend, don't fork. Parameterize over the charm dir:
- `make lint`/`fmt`/`unit` already glob `src/ tests/` — point them at `k8s/src machine/src shared/ tests/` (ruff handles multiple paths).
- `make unit` should cover `tests/unit/{shared,k8s,machine}` — `shared` (the `norma.py` workload module) stays **plain pytest, zero ops** per the constitution.
- Add `make pack-k8s` (`cd k8s && charmcraft pack`) and `make pack-machine` (`cd machine && charmcraft pack`); keep `make integration` matrixable by substrate.

### CI workflows (existing: `ci`, `publish-edge`, `publish-rock`, `release-tag`, `promote`, `rock`)
- **`ci.yaml`**: add a `pack` matrix over `[k8s, machine]` (each `cd <dir> && charmcraft fetch-libs && charmcraft pack`). The machine charm's pack job is **independent of `build-rock`** (machine has no ROCK). Integration job gains a substrate matrix: k8s on **microk8s** (existing), machine on **LXD** — both already partly present (`canonical/setup-lxd` is used for packing; LXD bootstrap is needed for machine integration). Add a `build-machine-binary` step that compiles `workload/` and stages it as the charm `file` resource for the machine integration deploy.
- **`rock.yaml` / `publish-rock.yaml`**: **unchanged and k8s-only.** They stay gated on `workload/**` + `rockcraft.yaml`. The machine charm just consumes the same `workload/` binary via a different (non-OCI) packaging step — so the Go-test job in `rock.yaml` still protects both charms' workload.
- **`publish-edge.yaml`**: today it packs one charm and uploads to `latest/edge`. Add a parallel job (or matrix) that packs `machine/` and uploads to the **second CharmHub charm name** (e.g. `juju-norma` machine vs `juju-norma-k8s`). The `workflow_run` chain off "Publish ROCK" should gate **only the k8s** publish (the machine charm doesn't depend on the ROCK being fresh) — so the machine publish should trigger on push to `main` directly, not via the ROCK `workflow_run`.
- **`release-tag.yaml` / `promote.yaml`**: parameterize by charm name + `built-charm-path`. A single git tag can drive promotion of both charms, or use distinct tag prefixes (`k8s-vX` / `machine-vX`) if you want independent release cadence. `[UNVERIFIED]` whether the existing `release-tag.yaml` already supports a charm-name input — it should be matrixed.

### `tests/` layout
Split into `tests/unit/{shared,k8s,machine}` and `tests/integration/{k8s,machine}`. Unit tests use `ops.testing`/Scenario per substrate (Pebble-container state for k8s; the machine charm's Scenario `State` has **no containers** — you assert on systemd/snap side effects via mocked snap libs or by asserting the computed unit-file content from `shared/norma.py`). Integration uses `jubilant` with `temp_model()` — k8s model for k8s, **LXD/machine model** for machine. The 21 existing integration test files are k8s-centric (multi_container, oci_resource, pebble_ops, notices) and **do not all map to machine** — pebble/oci/notice tests are k8s-only; lifecycle/config/relations/secrets/status/networking/scaling/storage/upgrade tests should get machine equivalents.

---

## Summary of Default Recommendations
1. **Mono-repo**, per-substrate subdirectories (`k8s/`, `machine/`), each with its own `charmcraft.yaml`, sharing `workload/` and an ops-free `shared/norma.py` via symlinks. Pack each separately.
2. **No ROCK for the machine charm.** OCI image + `containers` + Pebble are strictly k8s. On a VM the workload runs directly under systemd; the ROCK's role is taken by the workload-delivery mechanism.
3. **Deliver the Go binary as a charm `file` resource** (reuse the existing `workload/` build) plus a charm-managed systemd unit — most hermetic/air-gapped and reuses the one build. Snap-as-resource is the documented alternative if you want snapd supervision.
4. **Single** `pyproject.toml`/`uv.lock`/`Makefile`; CI gains a `{k8s, machine}` matrix; ROCK workflows stay k8s-only; `publish-edge`/`release`/`promote` gain a second CharmHub charm-name target; tests split by substrate with k8s-only suites (pebble/oci/notices) excluded from the machine matrix.

### Relevant files (absolute)
- `/data/dev/juju-norma-k8s/charmcraft.yaml` — current k8s charm; would move to `k8s/charmcraft.yaml`
- `/data/dev/juju-norma-k8s/rockcraft.yaml` — k8s-only, no machine equivalent needed
- `/data/dev/juju-norma-k8s/workload/` — shared Go source, feeds both charms
- `/data/dev/juju-norma-k8s/Makefile`, `/data/dev/juju-norma-k8s/pyproject.toml` — extend, keep single shared copies
- `/data/dev/juju-norma-k8s/.github/workflows/{ci,publish-edge,publish-rock,release-tag,promote,rock}.yaml` — matrix/gate changes described above

### Sources
- [charmcraft.yaml file reference](https://documentation.ubuntu.com/charmcraft/en/stable/reference/files/charmcraft-yaml-file/) — containers/oci-image/charm-user are k8s-only; single yaml per pack
- [Manage charms — charmcraft](https://documentation.ubuntu.com/charmcraft/stable/howto/manage-charms/) — monorepo substrate-subdirectory + symlink pattern
- [grafana-agent-k8s-operator](https://github.com/canonical/grafana-agent-k8s-operator) and [Grafana Agent machine charm tutorial](https://charmhub.io/topics/canonical-observability-stack/tutorials/instrumenting-machine-charms) — real machine/k8s split (separate-repo pattern)
- [charmcraftcache](https://github.com/canonical/charmcraftcache) — charm identity = repo + relative path to charmcraft.yaml
- [Juju hook reference](https://documentation.ubuntu.com/juju/3.6/reference/hook/) — install hook installs packages / provisions the machine
- [layer-snap README](https://github.com/charmed-kubernetes/layer-snap/blob/main/README.md) and [layer-apt](https://github.com/stub42/layer-apt) — snap-as-resource (air-gap) vs apt workload delivery patterns