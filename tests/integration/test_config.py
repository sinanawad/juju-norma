"""F3 typed config + F4 status priority (jubilant, LXD)."""

import jubilant
import pytest

from .conftest import APP

# Config + status-priority are container-safe and fast on the shared session
# deployment — part of the per-PR smoke acceptance subset.
pytestmark = pytest.mark.smoke


class TestTypedConfig:
    def test_get_config_defaults(self, juju: jubilant.Juju):
        task = juju.run(f"{APP}/leader", "get-config")
        assert task.results["calibration-string"] == "default"
        assert task.results["calibration-int"] == "8080"
        assert task.results["calibration-bool"] == "True"
        assert task.results["bad-behavior-mode"] == "none"

    def test_set_string_config(self, juju: jubilant.Juju):
        juju.config(APP, {"calibration-string": "integration-test"})
        juju.wait(jubilant.all_active, timeout=120)
        task = juju.run(f"{APP}/leader", "get-config")
        assert task.results["calibration-string"] == "integration-test"
        juju.config(APP, reset=["calibration-string"])
        juju.wait(jubilant.all_active, timeout=120)

    def test_set_int_config(self, juju: jubilant.Juju):
        juju.config(APP, {"calibration-int": "9090"})
        juju.wait(jubilant.all_active, timeout=120)
        task = juju.run(f"{APP}/leader", "get-config")
        assert task.results["calibration-int"] == "9090"
        juju.config(APP, reset=["calibration-int"])
        juju.wait(jubilant.all_active, timeout=120)


class TestStatusPriority:
    def test_invalid_config_blocks_then_recovers(self, juju: jubilant.Juju):
        """F4: out-of-range int → Blocked; valid value → Active.

        Recovery uses an EXPLICIT ``calibration-int=8080`` rather than
        ``--reset``: live, ``juju config --reset`` reconverges the unit-status
        non-deterministically (observed both ~20s and >200s), whereas an explicit
        set recovers reliably in ~20s. See docs/FINDINGS.md.
        """
        juju.config(APP, {"calibration-int": "0"})
        juju.wait(jubilant.any_blocked, timeout=120)
        status = juju.status()
        unit = next(iter(status.apps[APP].units.values()))
        assert unit.is_blocked
        assert "calibration-int" in unit.workload_status.message
        juju.config(APP, {"calibration-int": "8080"})
        juju.wait(jubilant.all_active, timeout=180)
        # Leave config clean for downstream tests sharing the session model.
        juju.config(APP, reset=["calibration-int"])
