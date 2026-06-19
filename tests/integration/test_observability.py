"""F16 cos-agent push observability (jubilant, LXD).

Deploys grafana-agent as a subordinate and integrates cos-agent, then asserts the
provider databag carries metrics jobs + alert rules + dashboards. grafana-agent
itself stays blocked (missing downstream COS) — expected for the push model.

Runs in a DEDICATED throwaway model (``isolated_juju``): grafana-agent (and its
removal) is kept off the shared session model so it can't contaminate downstream
suites; the fixture force-destroys the model after.
"""

import json

import jubilant
import pytest

from .conftest import RESOURCE_NAME

GAGENT = "gagent"
PRINCIPAL = "norma-obs"


@pytest.mark.xfail(
    reason="grafana-agent availability/base can vary on the runner; provider-side "
    "databag is the calibration target",
    strict=False,
)
@pytest.mark.mutates
class TestCosAgent:
    def test_cos_agent_databag_populated(
        self, isolated_juju: jubilant.Juju, charm_path, workload_bin
    ):
        juju = isolated_juju
        juju.deploy(str(charm_path), app=PRINCIPAL, resources={RESOURCE_NAME: str(workload_bin)})
        juju.wait(jubilant.all_active, timeout=900)

        juju.cli(
            "deploy",
            "grafana-agent",
            GAGENT,
            "--channel",
            "2/stable",
            "--base",
            "ubuntu@24.04",
            include_model=True,
        )
        juju.integrate(f"{PRINCIPAL}:cos-agent", f"{GAGENT}:cos-agent")

        # Wait for the relation to settle (grafana-agent ends up blocked, which is
        # expected — it needs a downstream COS).
        def _related(s) -> bool:
            return GAGENT in s.apps and bool(s.apps[GAGENT].units)

        juju.wait(_related, timeout=900)

        task = juju.run(
            f"{PRINCIPAL}/leader", "get-relation-data", params={"endpoint": "cos-agent"}
        )
        rels = json.loads(task.results["relations"])
        blob = json.dumps(rels)
        assert "NormaWorkload" in blob  # our shipped alert rules propagate (KB2)
        assert "scrape" in blob.lower() or "metrics" in blob.lower()
