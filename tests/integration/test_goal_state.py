"""F4 goal-state — public goal-state API via introspect (jubilant, LXD).

The goal-state collector (P2-5, ops ``hookcmds.goal_state()``) is exposed as the
``goal-state`` section of the introspect action. Read-only — runs on the shared
session app (no mutation), so it joins the per-PR smoke subset. Calibrates that
goal-state reports the live unit with status/since (it degrades to
``{"status":"unavailable"}`` only under Scenario/unit tests, so this belongs in
integration).
"""

import json

import jubilant
import pytest

from .conftest import APP

pytestmark = pytest.mark.smoke


class TestGoalState:
    def test_goal_state_reports_units(self, juju: jubilant.Juju):
        gs = json.loads(
            juju.run(f"{APP}/leader", "introspect", params={"sections": "goal-state"}).results[
                "goal-state"
            ]
        )
        assert "units" in gs, f"goal-state has no units (got: {gs})"
        assert any(u.startswith(f"{APP}/") for u in gs["units"]), (
            f"deployed unit missing from goal-state units: {gs}"
        )
        # Each unit entry carries the live status + since (the goal-state contract).
        for unit, info in gs["units"].items():
            assert "status" in info and "since" in info, (
                f"goal-state unit {unit} missing status/since: {info}"
            )
