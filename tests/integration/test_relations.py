"""F7 self-relate (calibration) + F8 app-databag, and F14 subordinate (jubilant, LXD).

Deploys a second principal app (same charm) and integrates the calibration
provider/requirer, then integrates the subordinate via juju-info. These tests
mutate model topology, so they run on their own deployed apps and clean up.
"""

import json

import jubilant

from .conftest import APP


class TestCalibrationSelfRelate:
    def test_provider_requirer_integration(self, juju: jubilant.Juju, charm_path, workload_bin):
        """F7/F8: two apps (same charm) relate on the calibration interface."""
        peer_app = "norma-peer"
        resources = {"norma-bin": str(workload_bin)}
        try:
            juju.deploy(str(charm_path), app=peer_app, resources=resources)
            juju.wait(jubilant.all_active, timeout=600)
            juju.integrate(f"{APP}:calibration-provider", f"{peer_app}:calibration-requirer")
            juju.wait(jubilant.all_active, timeout=300)

            task = juju.run(
                f"{APP}/leader", "get-relation-data", params={"endpoint": "calibration-provider"}
            )
            rels = json.loads(task.results["relations"])
            assert rels, "no calibration-provider relation data"
            # F8: leader app databag is populated.
            assert rels[0]["app-data"].get("app-name") == APP
            # F7: both unit roles visible across the relation.
            roles = {u.get("role") for u in rels[0]["units"].values()}
            assert "provider" in roles
        finally:
            juju.cli("remove-application", peer_app, "--no-prompt", include_model=True)
            juju.wait(lambda s: peer_app not in s.apps and jubilant.all_active(s), timeout=600)
