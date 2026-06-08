"""F20 bad-behavior test-bed + F21 juju resolve error path (jubilant, LXD).

These deliberately drive non-baseline status; marked ``mutates`` so the
no-cascade guard doesn't second-guess mid-test states.
"""

import contextlib
import time

import jubilant
import pytest

from .conftest import APP


@pytest.mark.mutates
class TestStatusModes:
    def test_active_with_message(self, juju: jubilant.Juju):
        juju.config(APP, {"bad-behavior-mode": "active-with-message"})
        # The unit stays active, so all_active returns immediately — poll until
        # collect_unit_status surfaces the (violating) non-empty message.
        deadline = time.monotonic() + 120
        msg = ""
        while time.monotonic() < deadline:
            unit = next(iter(juju.status().apps[APP].units.values()))
            msg = unit.workload_status.message
            if unit.is_active and msg:
                break
            time.sleep(5)
        assert msg != "", "active-with-message did not surface a workload message"
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

        # Recover: reset the mode, then resolve the errored unit(s) with
        # --no-retry. A plain `resolve` RE-RUNS the failed config-changed hook; on
        # a slow runner it can re-run before bad-behavior-mode=none has propagated,
        # so the old hook-error config fires again → Juju "resolver loop error"
        # (observed ~20 min to clear in CI). --no-retry marks the failed hook
        # resolved and lets the queued (mode=none) config-changed run cleanly.
        juju.config(APP, {"bad-behavior-mode": "none"})
        for unit in juju.status().apps[APP].units:
            with contextlib.suppress(jubilant.CLIError):
                juju.cli("resolve", unit, "--no-retry", include_model=True)
        juju.wait(jubilant.all_active, timeout=300)
