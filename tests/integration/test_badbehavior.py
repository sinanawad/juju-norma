"""F20 bad-behavior test-bed + F21 juju resolve error path (jubilant, LXD)."""

import contextlib

import jubilant

from .conftest import APP


class TestStatusModes:
    def test_active_with_message(self, juju: jubilant.Juju):
        juju.config(APP, {"bad-behavior-mode": "active-with-message"})
        juju.wait(jubilant.all_active, timeout=120)
        unit = next(iter(juju.status().apps[APP].units.values()))
        assert unit.workload_status.message != ""  # violates "active carries no message"
        juju.config(APP, {"bad-behavior-mode": "none"})
        juju.wait(jubilant.all_active, timeout=120)

    def test_blocked_no_message(self, juju: jubilant.Juju):
        juju.config(APP, {"bad-behavior-mode": "blocked-no-message"})
        juju.wait(jubilant.any_blocked, timeout=120)
        unit = next(iter(juju.status().apps[APP].units.values()))
        assert unit.is_blocked
        juju.config(APP, {"bad-behavior-mode": "none"})
        juju.wait(jubilant.all_active, timeout=120)

    def test_stuck_maintenance(self, juju: jubilant.Juju):
        juju.config(APP, {"bad-behavior-mode": "stuck-maintenance"})
        juju.wait(lambda s: next(iter(s.apps[APP].units.values())).is_maintenance, timeout=120)
        juju.config(APP, {"bad-behavior-mode": "none"})
        juju.wait(jubilant.all_active, timeout=120)


class TestHookErrorAndResolve:
    def test_hook_error_then_resolve(self, juju: jubilant.Juju):
        """F20/F21: hook-error → error; reset + juju resolve → active."""
        juju.config(APP, {"bad-behavior-mode": "hook-error"})
        juju.wait(jubilant.any_error, timeout=180)

        # Recover: reset the mode, then resolve the errored unit(s).
        juju.config(APP, {"bad-behavior-mode": "none"})
        for unit in juju.status().apps[APP].units:
            with contextlib.suppress(jubilant.CLIError):
                juju.cli("resolve", unit, include_model=True)  # already resolved → ignore
        juju.wait(jubilant.all_active, timeout=300)
