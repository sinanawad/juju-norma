"""F14 subordinate mode (juju-info, container scope) (jubilant, LXD).

Skipped unless the subordinate charm has been packed (subordinate/*.charm).
"""

import contextlib

import jubilant
import pytest

from .conftest import APP, SUBORDINATE_APP


@pytest.fixture(scope="module")
def _require_subordinate(subordinate_charm_path):
    if subordinate_charm_path is None:
        pytest.skip("subordinate charm not packed (cd subordinate && charmcraft pack)")
    return subordinate_charm_path


def _subordinate_units(status) -> dict:
    """Subordinate units as jubilant exposes them.

    A subordinate application has NO top-level ``units`` in juju status — its
    units appear nested under each PRINCIPAL unit's ``subordinates``. (Reading
    ``status.apps[SUBORDINATE_APP].units`` always yields {} and never converges.)
    """
    out = {}
    for punit in status.apps[APP].units.values():
        for sname, sunit in (punit.subordinates or {}).items():
            if sname.startswith(SUBORDINATE_APP + "/"):
                out[sname] = sunit
    return out


@pytest.mark.mutates
class TestSubordinate:
    def test_colocated_via_juju_info(self, juju: jubilant.Juju, _require_subordinate):
        """F14: subordinate deploys onto the principal's machine via juju-info."""
        try:
            # A subordinate has no units until integrated with a principal.
            juju.deploy(str(_require_subordinate), app=SUBORDINATE_APP)
            juju.integrate(f"{APP}:juju-info", f"{SUBORDINATE_APP}:juju-info")

            def _sub_active(s) -> bool:
                subs = _subordinate_units(s)
                return bool(subs and all(u.is_active for u in subs.values()))

            juju.wait(_sub_active, timeout=600)

            # get-principal reports the colocated principal unit.
            sub_unit = next(iter(_subordinate_units(juju.status())))
            task = juju.run(sub_unit, "get-principal")
            assert task.results["related"] == "true"
            assert task.results["principal"].startswith(f"{APP}/")
        finally:
            # Best-effort cleanup — never let teardown mask the real failure.
            with contextlib.suppress(jubilant.CLIError):
                juju.cli(
                    "remove-application",
                    SUBORDINATE_APP,
                    "--no-prompt",
                    "--force",
                    include_model=True,
                )
            with contextlib.suppress(TimeoutError):
                juju.wait(lambda s: SUBORDINATE_APP not in s.apps, timeout=300)
