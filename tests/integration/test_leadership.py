"""F6 leader-removal re-election (machine-distinct) (jubilant, LXD).

Roadmap P3-2. Removing a unit BY NAME is IAAS/machine-only — K8s rejects it
("k8s models do not support removing named units; use --num-units"), so removing
the *leader* by name and observing a fresh leader election among the survivors is
a behaviour the k8s sibling structurally cannot calibrate. Leadership is
lease-based, so the new leader appears only after the old lease is revoked/expires
— assertions poll with a generous deadline rather than expecting it instantly.

Mutates the unit count; marked ``mutates`` and returns to a single unit so later
suites run against a clean topology. Distinct from test_scaling, which only ever
removes the HIGHEST-ordinal (non-leader) unit; here we remove unit/0 (the leader).
"""

import json
import time

import jubilant
import pytest

from .conftest import APP


def _status(juju: jubilant.Juju) -> dict:
    return json.loads(juju.cli("status", "--format", "json", include_model=True))


def _units(juju: jubilant.Juju) -> dict:
    return _status(juju)["applications"][APP]["units"]


def _leader_name(juju: jubilant.Juju) -> str | None:
    """The unit juju currently reports as leader, or None during a gap."""
    for name, u in _units(juju).items():
        if u.get("leader"):
            return name
    return None


@pytest.mark.mutates
class TestLeaderReelection:
    def test_remove_leader_triggers_reelection(self, juju: jubilant.Juju):
        # 1) Ensure two units so a survivor exists to be elected.
        if len(_units(juju)) < 2:
            juju.cli("add-unit", APP, "-n", "1", include_model=True)
        juju.wait(
            lambda s: len(s.apps[APP].units) == 2 and jubilant.all_active(s),
            timeout=900,
        )

        # Leadership lease can lag units going active — poll for the initial leader.
        deadline = time.monotonic() + 180
        leader = None
        while time.monotonic() < deadline:
            leader = _leader_name(juju)
            if leader:
                break
            time.sleep(5)
        assert leader, "no leader elected on the 2-unit app"

        # 2) Remove the LEADER by name — machine-only (K8s rejects named removal).
        juju.cli("remove-unit", leader, "--no-prompt", include_model=True)

        # 3) The removed unit departs and a DIFFERENT survivor takes leadership
        #    once the old lease is gone. Poll generously for both conditions.
        deadline = time.monotonic() + 300
        new_leader = None
        while time.monotonic() < deadline:
            units = _units(juju)
            if leader not in units:  # old leader fully gone
                new_leader = next((n for n, u in units.items() if u.get("leader")), None)
                if new_leader:
                    break
            time.sleep(10)

        assert leader not in _units(juju), f"removed leader {leader} still present"
        assert new_leader, "no new leader elected after leader removal"
        assert new_leader != leader, f"leader did not change (still {leader})"

        # 4) Cluster reconverges to the single survivor, active/idle.
        juju.wait(
            lambda s: len(s.apps[APP].units) == 1 and jubilant.all_active(s),
            timeout=300,
        )

        # 5) Leader handoff is functional: the NEW leader manages the app-owned
        #    secret (machine-distinct — the secret's owner survived the old
        #    leader's removal). get-secret-info runs leader-only.
        info = juju.run(f"{APP}/leader", "get-secret-info").results
        assert info.get("secret-id"), f"new leader cannot resolve the app secret: {info}"

    def test_named_unit_removal_is_accepted(self, juju: jubilant.Juju):
        """The IAAS complement to the K8s 'named removal rejected' behaviour.

        On machine models removing a unit by name is accepted (it is the very
        mechanism exercised above); this records the positive case explicitly so
        the machine/K8s divergence is a documented, asserted calibration point.
        """
        # The session app is single-unit here; add then remove by name and assert
        # the named-removal path is honoured (no 'use --num-units' rejection).
        juju.cli("add-unit", APP, "-n", "1", include_model=True)
        juju.wait(lambda s: len(s.apps[APP].units) == 2 and jubilant.all_active(s), timeout=900)
        victim = max(_units(juju), key=lambda n: int(n.split("/")[1]))
        juju.cli("remove-unit", victim, "--no-prompt", include_model=True)
        juju.wait(
            lambda s: len(s.apps[APP].units) == 1 and jubilant.all_active(s),
            timeout=300,
        )
        assert victim not in _units(juju)
