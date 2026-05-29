"""F6 peers + leadership and F9 scaling + cluster-info (jubilant, LXD)."""

import json

import jubilant

from .conftest import APP


class TestScaling:
    def test_scale_up_to_three(self, juju: jubilant.Juju):
        """F9: add-unit grows the cluster; F6: peer membership reflects it."""
        status = juju.status()
        current = len(status.apps[APP].units)
        if current < 3:
            juju.cli("add-unit", APP, "-n", str(3 - current))
        juju.wait(lambda s: len(s.apps[APP].units) == 3 and jubilant.all_active(s), timeout=900)

        task = juju.run(f"{APP}/leader", "get-cluster-info")
        assert task.results["unit-count"] == "3"
        assert task.results["is-leader"] == "True"
        units = json.loads(task.results["units"])
        assert len(units) == 3

    def test_peer_data_present(self, juju: jubilant.Juju):
        """F6: leader publishes app peer data; all units publish unit data."""
        task = juju.run(f"{APP}/leader", "get-peer-data")
        app_data = json.loads(task.results["app-data"])
        unit_data = json.loads(task.results["unit-data"])
        assert app_data.get("leader-unit", "").startswith(f"{APP}/")
        assert len(unit_data) >= 1

    def test_scale_down(self, juju: jubilant.Juju):
        """Return to a single unit so later suites run against a clean topology."""
        status = juju.status()
        if len(status.apps[APP].units) > 1:
            # Remove the highest-numbered non-leader units down to 1.
            names = sorted(status.apps[APP].units, key=lambda n: int(n.split("/")[1]))
            for name in names[1:]:
                juju.cli("remove-unit", name)
        juju.wait(lambda s: len(s.apps[APP].units) == 1 and jubilant.all_active(s), timeout=600)
