"""F18 event deferral (defer-gate) + re-emission (jubilant, LXD)."""

import json

import jubilant
import pytest

from .conftest import APP

# The defer-gate exercise is a single config change on the shared deployment —
# container-safe and fast; part of the per-PR smoke acceptance subset.
pytestmark = pytest.mark.smoke


def _config_changed_ledger(juju: jubilant.Juju) -> list[dict]:
    return json.loads(
        juju.run(
            f"{APP}/leader", "get-event-log", params={"event-filter": "config-changed"}
        ).results["events"]
    )


def _arm_and_defer_config(juju: jubilant.Juju, prefix: str) -> list[dict]:
    """Arm the gate and drive a config-changed that actually gets DEFERRED.

    The defer-gate is one-shot: it defers the NEXT eligible event. Right after a
    fresh deploy, a settling event (leader-elected, secret-changed, a peer/relation
    hook) can consume the arm before our config-changed, so a single arm+set is
    racy. We retry until a config-changed is the event that got deferred. Returns the
    config-changed ledger once a deferred entry is present.
    """
    for attempt in range(6):
        juju.run(f"{APP}/leader", "test-defer", params={"arm": True})
        juju.config(APP, {"calibration-string": f"{prefix}-{attempt}"})
        juju.wait(jubilant.all_active, timeout=180)
        events = _config_changed_ledger(juju)
        if any(e.get("extra", {}).get("deferred") == "true" for e in events):
            return events
    raise AssertionError(f"config-changed never got deferred after {attempt + 1} arm attempts")


class TestDefer:
    def test_arm_defer_then_event_is_deferred(self, juju: jubilant.Juju):
        """F18: an armed gate defers a config-changed (recorded deferred:true)."""
        events = _arm_and_defer_config(juju, "defer-probe")
        assert any(e.get("extra", {}).get("deferred") == "true" for e in events), (
            "expected a deferred config-changed entry in the ledger"
        )
        juju.config(APP, reset=["calibration-string"])
        juju.wait(jubilant.all_active, timeout=120)

    def test_deferred_event_is_reemitted(self, juju: jubilant.Juju):
        """F18: a deferred event is RE-EMITTED later, tagged re-emitted:true AFTER the
        deferred entry. ops resets event.deferred before re-running the handler, so the
        charm revives the marker via a persisted pending-reemit flag (was dead code)."""
        # Robustly defer a config-changed (past any settling events).
        _arm_and_defer_config(juju, "reemit-probe")
        # One more config-changed triggers ops to re-emit the deferred one (at the
        # start of the next dispatch) before reconciling the new value.
        juju.config(APP, {"calibration-string": "reemit-trigger"})
        juju.wait(jubilant.all_active, timeout=180)

        events = _config_changed_ledger(juju)

        def _last_idx(flag):
            idxs = [i for i, e in enumerate(events) if e.get("extra", {}).get(flag) == "true"]
            return idxs[-1] if idxs else None

        deferred_idx = _last_idx("deferred")
        reemit_idx = _last_idx("re-emitted")
        assert deferred_idx is not None, f"no deferred config-changed entry: {events}"
        assert reemit_idx is not None, f"no re-emitted entry (marker dead?): {events}"
        assert deferred_idx < reemit_idx, f"re-emission not after the deferral: {events}"

        juju.config(APP, reset=["calibration-string"])
        juju.wait(jubilant.all_active, timeout=120)
