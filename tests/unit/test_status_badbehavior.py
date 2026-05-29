"""Unit tests for F4 (status) and F20 (bad-behavior test-bed).

Two layers are exercised:

* Charm-level status decisioning via ops.testing (Scenario): fire
  ``collect_unit_status``/``config_changed`` against an immutable input State and
  assert the resulting unit status. The charm builds a real ``SystemdDriver``
  (no host side effects thanks to the autouse conftest fixture); readiness and
  service-running are monkeypatched so the bad-status branch is reached.
* Pure-module level: ``norma_common.badbehavior`` is ops-free and plain-pytest
  testable — ``normalize_mode`` and ``status_for_mode`` are asserted directly.
"""

import ops
import ops.testing
import pytest

import workload_driver
from charm import NormaCharm
from norma_common import badbehavior


def _make_ready(monkeypatch, *, running=True):
    """Make the SystemdDriver report ready (+running) with a no-op apply.

    Short-circuits is_ready -> True so the reconciler never fetches the
    norma-bin resource, and reaches the bad-behavior / running status branches.
    """
    monkeypatch.setattr(workload_driver.SystemdDriver, "is_ready", lambda self: True)
    monkeypatch.setattr(workload_driver.SystemdDriver, "apply", lambda self, **kw: None)
    monkeypatch.setattr(workload_driver.SystemdDriver, "service_running", lambda self: running)


# ---------------------------------------------------------------------------- #
#  F4 — baseline status (well-behaved, bad-behavior-mode=none)                  #
# ---------------------------------------------------------------------------- #


class TestBaselineStatus:
    def test_waiting_when_not_ready(self):
        # Default driver: binary absent on the test host => is_ready() False.
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(ctx.on.collect_unit_status(), ops.testing.State())
        assert out.unit_status == ops.WaitingStatus("Waiting for workload binary")

    def test_active_when_ready_and_running(self, monkeypatch):
        _make_ready(monkeypatch, running=True)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(ctx.on.collect_unit_status(), ops.testing.State())
        assert out.unit_status == ops.ActiveStatus()
        assert out.unit_status.message == ""

    def test_maintenance_when_ready_not_running(self, monkeypatch):
        _make_ready(monkeypatch, running=False)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(ctx.on.collect_unit_status(), ops.testing.State())
        assert isinstance(out.unit_status, ops.MaintenanceStatus)

    def test_blocked_on_invalid_config(self):
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(ctx.on.config_changed(), ops.testing.State(config={"calibration-int": 0}))
        assert isinstance(out.unit_status, ops.BlockedStatus)
        assert "calibration-int" in out.unit_status.message

    def test_blocked_takes_priority_over_ready(self, monkeypatch):
        # Even with a ready+running driver, invalid config forces Blocked
        # (forced_status set in reconcile short-circuits the collect handler).
        _make_ready(monkeypatch, running=True)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.config_changed(), ops.testing.State(config={"calibration-float": 0.0})
        )
        assert isinstance(out.unit_status, ops.BlockedStatus)
        assert "calibration-float" in out.unit_status.message


# ---------------------------------------------------------------------------- #
#  F20 — bad-behavior unit-status overrides (charm level)                       #
# ---------------------------------------------------------------------------- #


class TestBadBehaviorStatus:
    def test_active_with_message(self, monkeypatch):
        _make_ready(monkeypatch, running=True)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.collect_unit_status(),
            ops.testing.State(config={"bad-behavior-mode": "active-with-message"}),
        )
        assert isinstance(out.unit_status, ops.ActiveStatus)
        # Deliberate convention violation: active carries a non-empty message.
        assert out.unit_status.message != ""

    def test_blocked_no_message(self, monkeypatch):
        _make_ready(monkeypatch, running=True)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.collect_unit_status(),
            ops.testing.State(config={"bad-behavior-mode": "blocked-no-message"}),
        )
        assert isinstance(out.unit_status, ops.BlockedStatus)
        # Deliberate convention violation: blocked carries no actionable message.
        assert out.unit_status.message == ""

    def test_stuck_maintenance(self, monkeypatch):
        _make_ready(monkeypatch, running=True)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.collect_unit_status(),
            ops.testing.State(config={"bad-behavior-mode": "stuck-maintenance"}),
        )
        assert isinstance(out.unit_status, ops.MaintenanceStatus)
        assert out.unit_status.message != ""

    def test_status_churn_even_ledger_is_active(self, monkeypatch):
        # collect_unit_status does NOT append to the ledger; with a fresh
        # isolated tmp ledger (len 0, even) churn yields ActiveStatus("").
        _make_ready(monkeypatch, running=True)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.collect_unit_status(),
            ops.testing.State(config={"bad-behavior-mode": "status-churn"}),
        )
        assert isinstance(out.unit_status, ops.ActiveStatus)

    def test_status_churn_odd_ledger_is_waiting(self, monkeypatch):
        # config_changed reconcile appends exactly one ledger entry (len 1, odd)
        # before collect_unit_status reads parity => WaitingStatus.
        _make_ready(monkeypatch, running=True)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.config_changed(),
            ops.testing.State(config={"bad-behavior-mode": "status-churn"}),
        )
        assert isinstance(out.unit_status, ops.WaitingStatus)

    def test_hook_error_mode_raises_uncaught(self, monkeypatch):
        # hook-error injects an uncaught RuntimeError inside _reconcile, which
        # Scenario surfaces as UncaughtCharmError.
        _make_ready(monkeypatch, running=True)
        ctx = ops.testing.Context(NormaCharm)
        with pytest.raises(ops.testing.errors.UncaughtCharmError):
            ctx.run(
                ctx.on.config_changed(),
                ops.testing.State(config={"bad-behavior-mode": "hook-error"}),
            )

    def test_unknown_mode_falls_back_to_baseline(self, monkeypatch):
        # Unknown mode normalises to 'none' => well-behaved Active (no message).
        _make_ready(monkeypatch, running=True)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.collect_unit_status(),
            ops.testing.State(config={"bad-behavior-mode": "does-not-exist"}),
        )
        assert out.unit_status == ops.ActiveStatus()

    def test_non_status_modes_leave_baseline_status(self, monkeypatch):
        # secret-in-relation acts on relation data, not unit status: baseline
        # Active applies when ready+running.
        _make_ready(monkeypatch, running=True)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.collect_unit_status(),
            ops.testing.State(config={"bad-behavior-mode": "secret-in-relation"}),
        )
        assert out.unit_status == ops.ActiveStatus()

    def test_bad_status_not_reached_when_not_ready(self):
        # When the binary isn't ready, Waiting wins over any bad-status mode
        # (the bad-behavior branch is gated behind is_ready()).
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.collect_unit_status(),
            ops.testing.State(config={"bad-behavior-mode": "active-with-message"}),
        )
        assert out.unit_status == ops.WaitingStatus("Waiting for workload binary")


# ---------------------------------------------------------------------------- #
#  F20 — pure module: norma_common.badbehavior (ops-free)                       #
# ---------------------------------------------------------------------------- #


class TestNormalizeMode:
    @pytest.mark.parametrize("mode", badbehavior.BAD_BEHAVIOR_MODES)
    def test_recognised_modes_passthrough(self, mode):
        assert badbehavior.normalize_mode(mode) == mode

    def test_unknown_maps_to_none(self):
        assert badbehavior.normalize_mode("totally-bogus") == "none"

    def test_none_input_maps_to_none(self):
        assert badbehavior.normalize_mode(None) == "none"

    def test_empty_string_maps_to_none(self):
        assert badbehavior.normalize_mode("") == "none"

    def test_whitespace_is_stripped(self):
        assert badbehavior.normalize_mode("  stuck-dying  ") == "stuck-dying"


class TestStatusForMode:
    def test_active_with_message(self):
        result = badbehavior.status_for_mode("active-with-message", 8080, 0)
        assert result == ("active", "serving on port 8080")

    def test_active_with_message_uses_port(self):
        kind, message = badbehavior.status_for_mode("active-with-message", 9090, 0)
        assert kind == "active"
        assert "9090" in message

    def test_blocked_no_message(self):
        assert badbehavior.status_for_mode("blocked-no-message", 8080, 0) == ("blocked", "")

    def test_stuck_maintenance(self):
        kind, message = badbehavior.status_for_mode("stuck-maintenance", 8080, 0)
        assert kind == "maintenance"
        assert message != ""

    def test_status_churn_even_is_active_no_message(self):
        assert badbehavior.status_for_mode("status-churn", 8080, 0) == ("active", "")
        assert badbehavior.status_for_mode("status-churn", 8080, 4) == ("active", "")

    def test_status_churn_odd_is_waiting_with_message(self):
        kind, message = badbehavior.status_for_mode("status-churn", 8080, 1)
        assert kind == "waiting"
        assert message != ""
        assert badbehavior.status_for_mode("status-churn", 8080, 3)[0] == "waiting"

    @pytest.mark.parametrize("mode", ["none", "hook-error", "secret-in-relation", "stuck-dying"])
    def test_non_status_modes_return_none(self, mode):
        assert badbehavior.status_for_mode(mode, 8080, 0) is None

    def test_status_override_modes_set_consistent(self):
        # Every mode in STATUS_OVERRIDE_MODES yields a non-None tuple, and every
        # other mode yields None — sanity-check the two are partitioned.
        for mode in badbehavior.BAD_BEHAVIOR_MODES:
            result = badbehavior.status_for_mode(mode, 8080, 0)
            if mode in badbehavior.STATUS_OVERRIDE_MODES:
                assert result is not None
            else:
                assert result is None
