"""F14 subordinate mode (juju-info, container scope) (jubilant, LXD).

Skipped unless the subordinate charm has been packed (subordinate/*.charm).
"""

import jubilant
import pytest

from .conftest import APP, SUBORDINATE_APP


@pytest.fixture(scope="module")
def _require_subordinate(subordinate_charm_path):
    if subordinate_charm_path is None:
        pytest.skip("subordinate charm not packed (cd subordinate && charmcraft pack)")
    return subordinate_charm_path


class TestSubordinate:
    def test_colocated_via_juju_info(self, juju: jubilant.Juju, _require_subordinate):
        """F14: subordinate deploys onto the principal's machine via juju-info."""
        try:
            juju.deploy(str(_require_subordinate), app=SUBORDINATE_APP)
            juju.integrate(f"{APP}:juju-info", f"{SUBORDINATE_APP}:juju-info")

            def _sub_active(s) -> bool:
                app = s.apps.get(SUBORDINATE_APP)
                return bool(app and app.units and all(u.is_active for u in app.units.values()))

            juju.wait(_sub_active, timeout=600)

            # get-principal reports the colocated principal unit.
            sub_unit = next(iter(juju.status().apps[SUBORDINATE_APP].units))
            task = juju.run(sub_unit, "get-principal")
            assert task.results["related"] == "true"
            assert task.results["principal"].startswith(f"{APP}/")
        finally:
            juju.cli("remove-application", SUBORDINATE_APP, "--no-prompt", include_model=True)
            juju.wait(lambda s: SUBORDINATE_APP not in s.apps, timeout=300)
