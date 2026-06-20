"""F22 teardown — clean teardown + stuck-dying trap (jubilant, LXD).

Roadmap P3-6. Calibrates the machine-distinct teardown path: the charm's stop/remove
handlers stop+disable the systemd service and unlink the unit file (idempotent), and
the ``stuck-dying`` bad-behavior mode makes a unit RESIST normal removal (the teardown
hook raises) so it must be force-removed — a trap the k8s sibling cannot exercise the
same way (no systemd, scale-down drops the highest ordinal). Dedicated throwaway
models (``isolated_juju``): these tests remove apps. Full-suite only.
"""

import json
import time

import jubilant
import pytest

from .conftest import RESOURCE_NAME

APP = "norma-tdown"


@pytest.mark.mutates
class TestTeardown:
    def test_clean_teardown_removes_workload(
        self, isolated_juju: jubilant.Juju, charm_path, workload_bin
    ):
        """Happy path: the workload is fully up, then remove-application is clean."""
        juju = isolated_juju
        juju.deploy(str(charm_path), app=APP, resources={RESOURCE_NAME: str(workload_bin)})
        juju.wait(jubilant.all_active, timeout=900)

        # Baseline: the systemd workload is laid down + running (the three teardown
        # observables the systemd collector reports).
        svc = json.loads(
            juju.run(
                f"{APP}/leader", "introspect", params={"sections": "systemd-service"}
            ).results["systemd-service"]
        )
        assert svc["binary-present"] is True, f"binary not present pre-teardown: {svc}"
        assert svc["service-running"] is True, f"service not running pre-teardown: {svc}"
        assert svc["unit-file"] is True, f"unit file absent pre-teardown: {svc}"

        # A well-behaved unit removes cleanly (stop+remove handlers tear down).
        juju.cli("remove-application", APP, "--no-prompt", include_model=True)
        juju.wait(lambda s: APP not in s.apps, timeout=300)

    def test_stuck_dying_resists_then_force(
        self, isolated_juju: jubilant.Juju, charm_path, workload_bin
    ):
        """Trap: a stuck-dying unit's teardown hook raises, so normal removal wedges
        in dying/error; --force is required to reclaim it."""
        juju = isolated_juju
        juju.deploy(str(charm_path), app=APP, resources={RESOURCE_NAME: str(workload_bin)})
        juju.wait(jubilant.all_active, timeout=900)

        # Arm the teardown trap. stuck-dying acts only on the teardown path, so the
        # unit stays active until removal is attempted.
        juju.config(APP, {"bad-behavior-mode": "stuck-dying"})
        juju.wait(jubilant.all_active, timeout=180)

        # Normal removal must NOT complete — the raising remove hook wedges it.
        juju.cli("remove-application", APP, "--no-prompt", include_model=True)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if APP not in juju.status().apps:
                pytest.fail("stuck-dying app removed cleanly — teardown trap did not fire")
            time.sleep(15)
        assert APP in juju.status().apps, "stuck-dying app vanished — trap did not hold"

        # --force reclaims the wedged unit.
        juju.cli("remove-application", APP, "--no-prompt", "--force", include_model=True)
        juju.wait(lambda s: APP not in s.apps, timeout=300)
