# juju-norma :: CI/CD reference & roadmap

How we want CI/CD to look, distilled from the k8s sibling
[`juju-norma-k8s`](https://github.com/sinanawad/juju-norma-k8s) `.github/workflows/`
(6 workflows), adapted to the machine substrate (no ROCK/OCI).

## Status (updated 2026-06-19) — CD LIVE; full suite promoted to the nightly (P2-9b)

The 2026-05-29 "CD deferred until we publish" scoping is **superseded**: the
`juju-norma` CharmHub name is registered, a manage-scoped `CHARMHUB_TOKEN` secret
is set, and **rev 1 + `norma-bin` (r2) are released to `latest/edge`**
(<https://charmhub.io/juju-norma>). Both the CI (validate) and CD (publish/
promote/release) halves are implemented.

| Sibling workflow | Machine-charm equivalent here | Status |
|---|---|---|
| `ci.yaml` | `ci.yaml` (lint+gofmt+vet / unit / lib-check / pack +subordinate / workload-build / smoke-integration / nightly+dispatch integration) | **done** — SHA-pinned, `uv lock --check`/`--frozen`, JUnit reports, nightly |
| `rock.yaml`, `publish-rock.yaml` | **N/A** — no ROCK/OCI; replaced by `build-resource.yaml` (deterministic `norma` binary + SHA256SUMS + SLSA provenance) | **done** |
| `publish-edge.yaml` | `publish-edge.yaml` — `workflow_run` off CI; `charmcraft upload`→`upload-resource norma-bin`→`release latest/edge` (FILE resource, with retry) | **done, publishing** |
| `release-tag.yaml` | `release-tag.yaml` — `v*` → promote edge→candidate, build **subordinate** + binary, SLSA-attested GH Release | **done (untriggered until first tag)** |
| `promote.yaml` | `promote.yaml` — manual candidate→stable | **done** |

Machine note: the sibling's "sudoer variant" swap (`charmcraft-sudoer.yaml`) is
k8s-only (charm-user). Our analogue is the **subordinate** charm (`subordinate/`),
built as the second release artifact.

## CI gaps vs the sibling — ALL ADOPTED (2026-06-08)

The list below is now **done** (`fetch-libs`, `enable-cache`, artifact uploads,
`setup-lxd` in pack, the 4.0 matrix, subordinate pack, `needs:` chaining), kept as
a record of what was closed:

1. **`charmcraft fetch-libs` before pack** — we now vendor `cos_agent` (committed
   under `lib/`), but a clean CI runner should `fetch-libs` so the pack matches a
   fresh checkout. Sibling does this in every pack/publish job. *(Our lib is
   committed, so pack works without it — but add `fetch-libs` for parity/robustness.)*
2. **`enable-cache: true` on `setup-uv`** — faster CI; sibling uses it everywhere.
3. **Upload artifacts** — `actions/upload-artifact` for the packed `.charm`(s) and
   the coverage report, so downstream jobs/humans can grab them. We upload nothing.
4. **`canonical/setup-lxd@main` in the pack job** — charmcraft packs in an LXD
   build instance; the sibling sets LXD up explicitly rather than relying on the
   runner default. More reliable.
5. **Integration matrix** — sibling runs `juju-channel: [3.6/stable, 4.0/stable]`.
   We target 4.0+ only (locked D5), so a single `4.0/stable` entry is correct —
   but structure it as a matrix so adding 26.04/4.1 later is a one-line change.
6. **Pack the subordinate in the same `pack` job** — already done (good).
7. **`needs:` chaining** — sibling gates unit on lint, integration on
   [unit, pack, build-rock]. We have needs but should keep integration gated on
   [unit, pack, workload-build].

## Per-PR live LXD acceptance — what actually runs where (corrected)

Earlier framing ("a GH runner can't run LXD") was an OVERSTATEMENT. Reality:

- **LXD *containers* run fine on a stock `ubuntu-24.04` GitHub-hosted runner.**
  `canonical/setup-lxd` (or `charmed-kubernetes/actions-operator` with
  `provider: lxd`) installs the LXD snap + `lxd init --auto` and bootstraps Juju.
  This covers the container-based majority of features (F1–F12, F14, F16–F22).
- Only two cases genuinely need more than a stock runner:
  - **`virt-type=virtual-machine` (F13)** — LXD VMs need `/dev/kvm`; GH runners
    have no nested virt.
  - **nested `--to lxd:N` (F15)** — needs container nesting (the localhost-LXD
    limitation we already documented in PROGRESS/FINDINGS).

So we run live acceptance at **two tiers** (this is the improvement over the
sibling, which is `workflow_dispatch`-only at one tier):

1. **`smoke-integration` (per-PR, `ci.yaml`)** — `make integration-smoke`
   (`pytest -m smoke`) on a stock runner with LXD containers via
   `actions-operator`. Currently the deploy→active/idle check; grow it with more
   `@pytest.mark.smoke` container-only cases (config, actions, relations, secrets,
   storage-filesystem, expose). This is the real per-PR regression guard and it
   needs **no** special runner or secrets.
2. **`integration` (nightly + `workflow_dispatch`, `ci.yaml`)** — the full F1–F22
   suite. PROMOTED to the nightly (P2-9b, 2026-06-19) once shared-model
   contamination was fixed (#33); it runs every night as `continue-on-error` +
   issue-on-failure (probation — daily flake-data, never reddens the badge) and as a
   HARD gate on `workflow_dispatch`. The KVM/nesting cases (F13 `virt-type`, F15
   nested-lxd) are xfail on a stock runner and benefit from a capable runner.

Net: the only thing that still wants a richer runner is the **VM/nesting subset**,
not "LXD in CI" wholesale.

## Beyond-parity improvements — DONE (2026-06-08)

- **pytest `-m smoke` split** — done; 24 container-safe tests run per-PR, the full
  37 run nightly/dispatch.
- **SHA-pinned actions** — done; every `uses:` is a commit SHA (`# vX`).
- **Dependabot** — done; GitHub Actions + `uv` + the `workload/` gomod module.
- **lib-check job** — done; `canonical/charming-actions/check-libraries` fails on
  `cos_agent` drift (needs the read-only side of `CHARMHUB_TOKEN`).
- **Nightly** — done; `schedule:` cron (04:19 UTC) runs TWO tiers across
  `4.0/stable` + `4.0/edge` (edge = engine-under-test). Each has its OWN concurrency
  group (keyed by event + run id) so a routine push can't cancel it.
  - **smoke** — the RELIABLE sentinel; always gives a clean engine-regression read.
  - **full F1-F22** — PROMOTED to the nightly (P2-9b, 2026-06-19) once shared-model
    contamination was fixed (#33, which isolated the FINDINGS#1 secret-wipe into
    dedicated throwaway models). It runs `continue-on-error` + issue-on-failure:
    daily flake-data on stock-runner infra reliability, never reddening the badge,
    while we bank the evidence to flip the nightly itself to a hard gate. The old
    weekly flake-data cron is retired (subsumed by the daily run).

## Publishing (live)

`publish-edge` / `release-tag` / `promote` exist and work: charm `juju-norma` (no
`-k8s`), no ROCK/OCI, subordinate as the second artifact, and the FILE-resource
flow (`charmcraft upload` → `upload-resource norma-bin` → `release`) since
`charming-actions/upload-charm` only auto-handles oci-image. The provenance spine
is `build-resource.yaml` (SHA256SUMS + SLSA on the binary). `CHARMHUB_TOKEN` is set
(manage scope) and the name is registered. First edge release: rev 1 + norma-bin r2.
**Still ROADMAP:** flip the nightly full suite from probation (`continue-on-error`)
to a HARD gate once daily flake-data confirms stock-runner reliability; and a
capable / self-hosted runner for the KVM/nested-lxd tier (F13 `virt-type`, F15
nested-lxd, currently xfail). The first `v*` tag already exercised `release-tag`
(v0.1.0 shipped to `latest/stable`).
