"""F14 subordinate mode (juju-info, container scope) (jubilant, LXD).

Skipped unless the subordinate charm has been packed (subordinate/*.charm).

Runs in a DEDICATED throwaway model (``isolated_juju``): it deploys a principal +
a subordinate and removes them, and keeping that churn off the shared session
model avoids topology contamination of downstream suites (see conftest). The
fixture force-destroys the model after, so no in-test cleanup is needed.
"""

import jubilant
import pytest

from .conftest import RESOURCE_NAME, SUBORDINATE_APP

PRINCIPAL = "norma-pri"


@pytest.fixture(scope="module")
def _require_subordinate(subordinate_charm_path):
    if subordinate_charm_path is None:
        pytest.skip("subordinate charm not packed (cd subordinate && charmcraft pack)")
    return subordinate_charm_path


def _subordinate_units(status, principal: str) -> dict:
    """Subordinate units as jubilant exposes them.

    A subordinate application has NO top-level ``units`` in juju status — its
    units appear nested under each PRINCIPAL unit's ``subordinates``. (Reading
    ``status.apps[SUBORDINATE_APP].units`` always yields {} and never converges.)
    """
    out = {}
    for punit in status.apps[principal].units.values():
        for sname, sunit in (punit.subordinates or {}).items():
            if sname.startswith(SUBORDINATE_APP + "/"):
                out[sname] = sunit
    return out


@pytest.mark.mutates
class TestSubordinate:
    def test_colocated_via_juju_info(
        self, isolated_juju: jubilant.Juju, charm_path, workload_bin, _require_subordinate
    ):
        """F14: subordinate deploys onto the principal's machine via juju-info."""
        juju = isolated_juju
        juju.deploy(str(charm_path), app=PRINCIPAL, resources={RESOURCE_NAME: str(workload_bin)})
        juju.wait(jubilant.all_active, timeout=900)

        # A subordinate has no units until integrated with a principal.
        juju.deploy(str(_require_subordinate), app=SUBORDINATE_APP)
        juju.integrate(f"{PRINCIPAL}:juju-info", f"{SUBORDINATE_APP}:juju-info")

        def _sub_active(s) -> bool:
            subs = _subordinate_units(s, PRINCIPAL)
            return bool(subs and all(u.is_active for u in subs.values()))

        juju.wait(_sub_active, timeout=600)

        # get-principal reports the colocated principal unit.
        sub_unit = next(iter(_subordinate_units(juju.status(), PRINCIPAL)))
        task = juju.run(sub_unit, "get-principal")
        assert task.results["related"] == "true"
        assert task.results["principal"].startswith(f"{PRINCIPAL}/")
