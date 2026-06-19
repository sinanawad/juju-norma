"""F7 self-relate (calibration) + F8 app-databag (jubilant, LXD).

Two instances of the SAME charm relate on the calibration provider/requirer
interface; the leader app databag must propagate across the relation.

Runs in a DEDICATED throwaway model (``isolated_juju``): this test removes a norma
app, and ``remove-application`` of a secret-owner trips the model-wide secret
VALUE-deletion bug (FINDINGS#1) — on the shared session model that silently wipes
the session app's secret and breaks the later test_secrets suite (alphabetical
order: relations → secrets). A separate model contains the blast radius; the
fixture force-destroys it, so no in-test cleanup is needed.
"""

import json

import jubilant
import pytest

from .conftest import RESOURCE_NAME

PROV = "norma-prov"
REQ = "norma-req"


@pytest.mark.mutates
class TestCalibrationSelfRelate:
    def test_provider_requirer_integration(
        self, isolated_juju: jubilant.Juju, charm_path, workload_bin
    ):
        """F7/F8: two apps (same charm) relate on the calibration interface."""
        juju = isolated_juju
        resources = {RESOURCE_NAME: str(workload_bin)}
        juju.deploy(str(charm_path), app=PROV, resources=resources)
        juju.deploy(str(charm_path), app=REQ, resources=resources)
        juju.wait(jubilant.all_active, timeout=900)

        juju.integrate(f"{PROV}:calibration-provider", f"{REQ}:calibration-requirer")
        juju.wait(jubilant.all_active, timeout=300)

        task = juju.run(
            f"{PROV}/leader", "get-relation-data", params={"endpoint": "calibration-provider"}
        )
        rels = json.loads(task.results["relations"])
        assert rels, "no calibration-provider relation data"
        # F8: leader app databag is populated.
        assert rels[0]["app-data"].get("app-name") == PROV
        # F7: both unit roles visible across the relation.
        roles = {u.get("role") for u in rels[0]["units"].values()}
        assert "provider" in roles
