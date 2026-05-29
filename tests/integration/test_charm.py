"""Integration smoke test for juju-norma (jubilant, LXD controller).

P0 scaffold: this establishes the jubilant + temp_model harness. The full
deploy/active-idle acceptance (F1/F2) lands in P1. The test is skipped unless a
packed charm artifact is present, so `make unit` and bare CI never block on a
live LXD controller.

Run with: make integration  (requires an LXD Juju controller, juju 4.0+).
"""

import pathlib

import jubilant
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CHARM_GLOB = "juju-norma_*.charm"
WORKLOAD_BIN = REPO_ROOT / "norma"


def _charm_path() -> pathlib.Path | None:
    matches = sorted(REPO_ROOT.glob(CHARM_GLOB))
    return matches[0] if matches else None


pytestmark = pytest.mark.skipif(
    _charm_path() is None or not WORKLOAD_BIN.exists(),
    reason="needs a packed charm (charmcraft pack) and built workload (make build-workload)",
)


@pytest.mark.smoke
def test_deploy_active_idle():
    """Deploy the charm with its file resource and reach active/idle.

    Marked ``smoke``: container-only, so it runs per-PR on a stock LXD runner
    (`make integration-smoke`). The full F1-F22 suite (incl. cloud/VM-only cases)
    stays unmarked and runs via workflow_dispatch on a richer runner.
    """
    charm = _charm_path()
    with jubilant.temp_model() as juju:
        juju.deploy(charm, resources={"norma-bin": str(WORKLOAD_BIN)})
        juju.wait(jubilant.all_active, timeout=600)
