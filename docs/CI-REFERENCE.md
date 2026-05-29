# juju-norma :: CI/CD reference & roadmap

How we want CI/CD to look, distilled from the k8s sibling
[`juju-norma-k8s`](https://github.com/sinanawad/juju-norma-k8s) `.github/workflows/`
(6 workflows), adapted to the machine substrate (no ROCK/OCI).

## Scoping decision (2026-05-29)

**We are pre-publication — no charm version has shipped to CharmHub yet.** So only
the **CI (validate)** half is in scope now. The entire **CD (publish/promote/
release)** half is **deferred until we decide to publish `juju-norma` to CharmHub**.
Building it now would be dead config gated on secrets we don't have.

| Sibling workflow | Purpose | Machine-charm relevance | Status here |
|---|---|---|---|
| `ci.yaml` | lint / unit / pack / build-rock / integration | **YES** (drop build-rock) | partially have it; gap list below |
| `rock.yaml` | build ROCK on PR | **N/A** — machine charm has no ROCK/OCI | never needed |
| `publish-rock.yaml` | push ROCK to ghcr.io | **N/A** — no ROCK | never needed |
| `publish-edge.yaml` | push charm to `latest/edge` on every main | CD — needs CHARMHUB_TOKEN | **deferred to publish** |
| `release-tag.yaml` | on `v*` tag: promote edge→candidate, GH Release | CD | **deferred to publish** |
| `promote.yaml` | manual channel promotion (candidate→stable) | CD | **deferred to publish** |

Machine note: the sibling's "sudoer variant" swap (`charmcraft-sudoer.yaml`) is
k8s-only (charm-user). Our analogue is the **subordinate** charm (`subordinate/`),
which we already pack as a second artifact.

## CI gaps vs the sibling (actionable now, no secrets needed)

Our current `ci.yaml` has lint / unit / workload-build / pack / integration. The
sibling's `ci.yaml` is the better model. Adopt:

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

## Suggestions for improvement (beyond parity)

- **Split integration with pytest markers** (`-m smoke`) so a cheap subset
  (deploy → active/idle + a couple of read-only actions) CAN run per-PR on a
  plain runner, while the full F1–F22 replay stays `workflow_dispatch`/self-hosted.
  The sibling's own comment suggests this; neither repo does it yet.
- **Pin actions to SHAs** (supply-chain) rather than `@main`
  (`canonical/setup-lxd@main`) — the sibling pins charming-actions to `@2.7.0`
  but uses `@main` for setup-lxd; pin both.
- **Dependabot** for GitHub Actions + uv + the `workload/` Go module
  (constitution CI/CD section calls for this; not present in either repo's CI).
- **lib-check job** — `charmcraft fetch-libs --format json` diff to fail when a
  vendored lib drifts from upstream (relevant now that we vendor `cos_agent`).

## When we DO publish

Lift `publish-edge` / `release-tag` / `promote` from the sibling almost verbatim,
substituting: charm name `juju-norma` (no `-k8s`), drop all ROCK/OCI steps, and
swap the "sudoer variant" build for the "subordinate" build. Requires a
`CHARMHUB_TOKEN` secret and a registered `juju-norma` CharmHub name.
