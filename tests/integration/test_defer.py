"""F18 event deferral (defer-gate) (jubilant, LXD)."""

import json

import jubilant

from .conftest import APP


class TestDefer:
    def test_arm_defer_then_event_is_deferred(self, juju: jubilant.Juju):
        """F18: arm the defer-gate, fire config-changed, see it deferred in the ledger."""
        armed = juju.run(f"{APP}/leader", "test-defer", params={"arm": True})
        assert armed.results["deferral-armed"] == "true"

        # Fire a gated event; the next one is deferred (and later re-emitted).
        juju.config(APP, {"calibration-string": "defer-probe"})
        juju.wait(jubilant.all_active, timeout=180)

        log = juju.run(f"{APP}/leader", "get-event-log", params={"event-filter": "config-changed"})
        events = json.loads(log.results["events"])
        assert any(e.get("extra", {}).get("deferred") == "true" for e in events), (
            "expected a deferred config-changed entry in the ledger"
        )
        juju.config(APP, reset=["calibration-string"])
        juju.wait(jubilant.all_active, timeout=120)
