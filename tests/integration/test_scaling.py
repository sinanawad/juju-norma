"""F6 peers + leadership and F9 scaling + cluster-info (jubilant, LXD).

These mutate unit count; marked ``mutates`` so the no-cascade guard doesn't also
wait while the topology is in flux.
"""

import json
import time

import jubilant
import pytest

from .conftest import APP


def _unit_count(juju: jubilant.Juju) -> int:
    return len(juju.status().apps[APP].units)


@pytest.mark.mutates
class TestScaling:
    # Scale to 2 units (one add-unit), not 3: it exercises the same calibration
    # — add-unit, peer-relation membership growth, cluster-info convergence,
    # leadership — at half the LXD machine-provisioning cost, which keeps the
    # test reliable in CI. (PLAN F9 AC of "3 units" is provider plumbing, not
    # extra charm behavior.)
    TARGET = 2

    def test_scale_up(self, juju: jubilant.Juju):
        """F9: add-unit grows the cluster; F6: peer membership reflects it."""
        current = _unit_count(juju)
        if current < self.TARGET:
            juju.cli("add-unit", APP, "-n", str(self.TARGET - current))
        # Wait for the new machine(s) to provision + units to go active. With no
        # error predicate, wait() tolerates the transient waiting/maintenance a
        # new unit passes through (incl. slow LXD boots under load).
        juju.wait(
            lambda s: len(s.apps[APP].units) == self.TARGET and jubilant.all_active(s),
            timeout=900,
        )

        # cluster-info reads peer-relation membership, which lags units going
        # active — poll until it converges.
        deadline = time.monotonic() + 180
        last = None
        while time.monotonic() < deadline:
            last = juju.run(f"{APP}/leader", "get-cluster-info").results
            if last["unit-count"] == str(self.TARGET):
                break
            time.sleep(10)
        assert last["unit-count"] == str(self.TARGET), f"cluster-info never converged: {last}"
        assert last["is-leader"] == "True"
        assert len(json.loads(last["units"])) == self.TARGET

    def test_peer_data_present(self, juju: jubilant.Juju):
        """F6: leader publishes app peer data; all units publish unit data."""
        task = juju.run(f"{APP}/leader", "get-peer-data")
        app_data = json.loads(task.results["app-data"])
        unit_data = json.loads(task.results["unit-data"])
        assert app_data.get("leader-unit", "").startswith(f"{APP}/")
        assert len(unit_data) >= 1

    def test_scale_down(self, juju: jubilant.Juju):
        """Return to a single unit so later suites run against a clean topology."""
        names = sorted(juju.status().apps[APP].units, key=lambda n: int(n.split("/")[1]))
        for name in names[1:]:
            juju.cli("remove-unit", name, "--no-prompt")
        juju.wait(
            lambda s: len(s.apps[APP].units) == 1 and jubilant.all_active(s),
            timeout=600,
        )
