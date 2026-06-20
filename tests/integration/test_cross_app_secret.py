"""F10 cross-app secret read (jubilant, LXD).

Roadmap P3-9. The provider charm grants its app-secret to calibration-provider
relations AND propagates the granted id into the relation app databag; the requirer
resolves it with model.get_secret().get_content(). Calibrates the grant END-TO-END —
the grant alone is invisible to a consumer that never learns the secret id.

Dedicated throwaway model (two apps + an app that owns a secret is removed at
teardown → would trip FINDINGS#1 on the shared model). Full-suite only.
"""

import time

import jubilant
import pytest

from .conftest import RESOURCE_NAME

PROV = "norma-sprov"
REQ = "norma-sreq"


@pytest.mark.mutates
class TestCrossAppSecret:
    def test_requirer_reads_granted_secret(
        self, isolated_juju: jubilant.Juju, charm_path, workload_bin
    ):
        juju = isolated_juju
        res = {RESOURCE_NAME: str(workload_bin)}
        juju.deploy(str(charm_path), app=PROV, resources=res)
        juju.deploy(str(charm_path), app=REQ, resources=res)
        juju.wait(jubilant.all_active, timeout=900)

        juju.integrate(f"{PROV}:calibration-provider", f"{REQ}:calibration-requirer")
        juju.wait(jubilant.all_active, timeout=300)

        # The provider grants + propagates the id on a reconcile after the relation
        # forms; poll the requirer reading it (grant + databag propagation settle).
        deadline = time.monotonic() + 180
        result = {}
        while time.monotonic() < deadline:
            result = juju.run(f"{REQ}/leader", "read-shared-secret").results
            if result.get("readable") == "true":
                break
            time.sleep(15)
        assert result.get("readable") == "true", (
            f"requirer could not read the granted secret: {result}"
        )
        assert result.get("secret-id", "").startswith("secret:"), f"no shared secret id: {result}"
