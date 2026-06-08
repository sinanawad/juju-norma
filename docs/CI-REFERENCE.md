# juju-norma :: CI/CD reference & roadmap

How we want CI/CD to look, distilled from the k8s sibling
[`juju-norma-k8s`](https://github.com/sinanawad/juju-norma-k8s) `.github/workflows/`
(6 workflows), adapted to the machine substrate (no ROCK/OCI).

## Status (updated 2026-06-08) — CD is now LIVE

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
2. **`integration` (`workflow_dispatch`, `ci.yaml`)** — the full F1–F22 suite,
   including the KVM/nesting cases. This tier benefits from a self-hosted /
   nested-virt runner; until one exists, run it manually. Keep the
   container-safe cases unmarked-but-included here too.

Net: the only thing that still wants a richer runner is the **VM/nesting subset**,
not "LXD in CI" wholesale.

## Beyond-parity improvements — DONE (2026-06-08)

- **pytest `-m smoke` split** — done; 24 container-safe tests run per-PR, the full
  37 run nightly/dispatch.
- **SHA-pinned actions** — done; every `uses:` is a commit SHA (`# vX`).
- **Dependabot** — done; GitHub Actions + `uv` + the `workload/` gomod module.
- **lib-check job** — done; `canonical/charming-actions/check-libraries` fails on
  `cos_agent` drift (needs the read-only side of `CHARMHUB_TOKEN`).
- **Nightly** — done; `schedule:` cron runs the full suite with its OWN
  concurrency group so a push can't cancel it.

## Publishing (live)

`publish-edge` / `release-tag` / `promote` exist and work: charm `juju-norma` (no
`-k8s`), no ROCK/OCI, subordinate as the second artifact, and the FILE-resource
flow (`charmcraft upload` → `upload-resource norma-bin` → `release`) since
`charming-actions/upload-charm` only auto-handles oci-image. The provenance spine
is `build-resource.yaml` (SHA256SUMS + SLSA on the binary). `CHARMHUB_TOKEN` is set
(manage scope) and the name is registered. First edge release: rev 1 + norma-bin r2.
**Still ROADMAP:** a `4.0/edge` nightly leg (engine-under-test), a capable runner
for the KVM/nested-lxd tier, and the first `v*` tag to exercise `release-tag`.
