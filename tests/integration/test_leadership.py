"""F6 leader-removal re-election (machine-distinct) (jubilant, LXD).

Roadmap P3-2. Removing a unit BY NAME is IAAS/machine-only — K8s rejects it
("k8s models do not support removing named units; use --num-units",
removeunit.go:173-178), so removing the *leader* by name and observing a fresh
election among the survivors is a behaviour the k8s sibling structurally cannot
calibrate. Leadership is lease-based (deployer LeadershipGuarantee=30s → 60s
lease, reaped 1-5s), so re-election rides lease EXPIRY, not graceful handover —
poll ~60-90s for a new leader, not instantly.

Runs in a DEDICATED throwaway model (``isolated_juju``): removing the leader
churns topology, and tearing the app down would trip the model-wide secret
value-deletion bug (FINDINGS#1) — both would corrupt the shared session model.
The fixture force-destroys the model afterwards, so no in-test cleanup is needed.
Full-suite only (no smoke marker — provisions real machines).
"""

import time

import jubilant
import pytest

from .conftest import RESOURCE_NAME

LEAD_APP = "norma-lead"


def _units(juju: jubilant.Juju) -> dict:
    s = juju.status()
    return s.apps[LEAD_APP].units if LEAD_APP in s.apps else {}


def _leader_name(juju: jubilant.Juju) -> str | None:
    """The unit jubilant reports as leader, or None during a lease gap."""
    for name, u in _units(juju).items():
        if getattr(u, "leader", False):
            return name
    return None


def _poll(predicate, timeout: float, interval: float = 10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        val = predicate()
        if val:
            return val
        time.sleep(interval)
    return None


@pytest.mark.mutates
class TestLeaderReelection:
    def test_remove_leader_triggers_reelection(
        self, isolated_juju: jubilant.Juju, charm_path, workload_bin
    ):
        juju = isolated_juju
        juju.cli(
            "deploy",
            str(charm_path),
            LEAD_APP,
            "--resource",
            f"{RESOURCE_NAME}={workload_bin}",
            "-n",
            "2",
            include_model=True,
        )
        juju.wait(
            lambda s: (
                LEAD_APP in s.apps and len(s.apps[LEAD_APP].units) == 2 and jubilant.all_active(s)
            ),
            timeout=900,
        )

        # Initial leader (lease can lag units going active).
        leader = _poll(lambda: _leader_name(juju), timeout=180, interval=5)
        assert leader, f"no leader elected on the 2-unit {LEAD_APP}"

        # Remove the LEADER by name — machine-only (K8s rejects named removal).
        # This step also calibrates "named-unit removal is accepted on machine"
        # (the IAAS complement to the K8s rejection).
        juju.cli("remove-unit", leader, "--no-prompt", include_model=True)

        # A DIFFERENT survivor takes leadership once the old lease expires.
        def _reelected():
            units = _units(juju)
            if leader in units:  # old leader not yet gone
                return None
            return next((n for n, u in units.items() if getattr(u, "leader", False)), None)

        new_leader = _poll(_reelected, timeout=300, interval=10)
        assert leader not in _units(juju), f"removed leader {leader} still present"
        assert new_leader, "no new leader elected after leader removal"
        assert new_leader != leader, f"leader did not change (still {leader})"

        # Reconverges to the single survivor, active/idle.
        juju.wait(
            lambda s: len(s.apps[LEAD_APP].units) == 1 and jubilant.all_active(s),
            timeout=300,
        )

        # Leader handoff is functional: the NEW leader manages the app-owned secret
        # (it survived the old leader's removal). get-secret-info is leader-only.
        info = juju.run(f"{LEAD_APP}/leader", "get-secret-info").results
        assert info.get("secret-id"), f"new leader cannot resolve the app secret: {info}"
