"""Unit tests for F18 event deferral (the charm's defer-gate).

The charm quarantines its only ``event.defer()`` in ``_on_defer_gate`` (charm.py).
The ``test-defer`` action arms/disarms a persisted flag (``norma.read/write_defer_armed``).
When armed, the next *gated* event (config-changed, install, leader-elected, etc.) is
DEFERRED instead of reconciled: it is recorded in the ledger with ``extra={"deferred": "true"}``,
``event.defer()`` is called, and the flag is cleared (one-shot). ``update-status`` and
``relation-broken`` are NEVER deferred even when armed.

Machine substrate: no containers. The conftest autouse fixture stubs subprocess and
isolates the on-disk ledger/defer flag to a tmp dir, so these tests are host-safe.
Any gated event that reaches the reconciler calls ``model.resources.fetch("norma-bin")``,
so a (placeholder, empty) norma-bin resource is declared on those States.
"""

import ops
import ops.testing

import norma
from charm import NormaCharm

# ----------------------------------------------------------------------------- #
#  The test-defer action: arm / disarm + persistence                            #
# ----------------------------------------------------------------------------- #


class TestTestDeferAction:
    def test_arm_true_sets_results_and_persists(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("test-defer", params={"arm": True}), ops.testing.State())
        assert ctx.action_results["deferral-armed"] == "true"
        assert ctx.action_results["previous-state"] == "false"
        # Persisted to the (tmp-isolated) defer flag file.
        assert norma.read_defer_armed() is True

    def test_arm_default_is_true(self):
        # action param default is true; omitting arm still arms.
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("test-defer"), ops.testing.State())
        assert ctx.action_results["deferral-armed"] == "true"
        assert norma.read_defer_armed() is True

    def test_arm_false_disarms_and_persists(self, monkeypatch):
        # Pre-arm on disk, then disarm via the action.
        norma.write_defer_armed(True)
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("test-defer", params={"arm": False}), ops.testing.State())
        assert ctx.action_results["deferral-armed"] == "false"
        assert ctx.action_results["previous-state"] == "true"
        assert norma.read_defer_armed() is False

    def test_charm_reads_armed_state_on_init(self, monkeypatch):
        # __init__ seeds _defer_armed from norma.read_defer_armed().
        monkeypatch.setattr(norma, "read_defer_armed", lambda: True)
        ctx = ops.testing.Context(NormaCharm)
        with ctx(ctx.on.action("test-defer", params={"arm": True}), ops.testing.State()) as mgr:
            # previous-state in results reflects the init-time value (True).
            mgr.run()
            assert ctx.action_results["previous-state"] == "true"


# ----------------------------------------------------------------------------- #
#  Deferral of a gated event when armed                                         #
# ----------------------------------------------------------------------------- #


class TestDeferralWhenArmed:
    def test_config_changed_is_deferred_when_armed(self, monkeypatch, norma_bin):
        # Pre-arm via the persisted flag so __init__ reads _defer_armed=True.
        monkeypatch.setattr(norma, "read_defer_armed", lambda: True)
        ctx = ops.testing.Context(NormaCharm)
        with ctx(ctx.on.config_changed(), ops.testing.State(resources={norma_bin})) as mgr:
            out = mgr.run()
            # The one-shot flag was consumed: defer disarmed after one event.
            assert mgr.charm._defer_armed is False
        # A deferral was recorded by Scenario on the output state.
        assert len(out.deferred) == 1
        assert "config_changed" in out.deferred[0].handle_path

    def test_deferred_event_recorded_in_ledger_with_flag(self, monkeypatch, norma_bin):
        monkeypatch.setattr(norma, "read_defer_armed", lambda: True)
        ctx = ops.testing.Context(NormaCharm)
        with ctx(ctx.on.config_changed(), ops.testing.State(resources={norma_bin})) as mgr:
            mgr.run()
            ledger = mgr.charm._event_ledger
            # The gated event is logged ONCE with the deferred marker, and the
            # reconcile body did NOT run (no second log entry from _reconcile).
            cc_entries = [e for e in ledger if e["event_name"] == "config-changed"]
            assert len(cc_entries) == 1
            assert cc_entries[0]["extra"] == {"deferred": "true"}

    def test_defer_persisted_to_disk_after_deferral(self, norma_bin):
        # Arm via the REAL on-disk flag (tmp-isolated by conftest) so the
        # post-run read reflects what the charm actually persisted.
        norma.write_defer_armed(True)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(ctx.on.config_changed(), ops.testing.State(resources={norma_bin}))
        # write_defer_armed(False) persisted the disarm to the tmp flag file.
        assert norma.read_defer_armed() is False
        assert len(out.deferred) == 1

    def test_reconcile_skipped_when_deferred(self, monkeypatch, norma_bin):
        # If reconcile had run, the workload apply path would have been reached.
        # We make the driver "ready" so reconcile WOULD set a workload version /
        # open a port; since it's deferred, neither should happen.
        monkeypatch.setattr(norma, "read_defer_armed", lambda: True)
        import workload_driver

        monkeypatch.setattr(workload_driver.SystemdDriver, "is_ready", lambda self: True)
        monkeypatch.setattr(workload_driver.SystemdDriver, "apply", lambda self, **kw: None)
        monkeypatch.setattr(workload_driver.SystemdDriver, "service_running", lambda self: True)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.config_changed(),
            ops.testing.State(resources={norma_bin}, config={"calibration-int": 9090}),
        )
        # Reconcile was skipped → no port opened, no workload version set.
        assert out.opened_ports == frozenset()
        assert out.workload_version == ""
        assert len(out.deferred) == 1

    def test_install_is_deferred_when_armed(self, monkeypatch, norma_bin):
        # Any gated lifecycle event (not just config-changed) is deferrable.
        monkeypatch.setattr(norma, "read_defer_armed", lambda: True)
        ctx = ops.testing.Context(NormaCharm)
        with ctx(ctx.on.install(), ops.testing.State(resources={norma_bin})) as mgr:
            out = mgr.run()
            assert mgr.charm._defer_armed is False
            install_entries = [e for e in mgr.charm._event_ledger if e["event_name"] == "install"]
            assert len(install_entries) == 1
            assert install_entries[0]["extra"] == {"deferred": "true"}
        assert len(out.deferred) == 1
        assert "install" in out.deferred[0].handle_path


# ----------------------------------------------------------------------------- #
#  Re-emission marker (F18): tag the re-emitted event in the ledger             #
# ----------------------------------------------------------------------------- #


class TestReemitMarker:
    """ops resets event.deferred=False before re-running a deferred handler, so the
    charm persists the deferred event's NAME and matches it on the next reconcile to
    tag the re-emission (the old getattr(event,'deferred') branch was dead code)."""

    def test_defer_records_pending_reemit(self, monkeypatch, norma_bin):
        monkeypatch.setattr(norma, "read_defer_armed", lambda: True)
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.config_changed(), ops.testing.State(resources={norma_bin}))
        # The deferred event's name is recorded for the later re-emission.
        assert norma.read_pending_reemit() == "config-changed"

    def test_reemission_tagged_and_cleared(self, norma_bin):
        # Post-defer state: disarmed, pending re-emit set (as the gate would leave it).
        norma.write_defer_armed(False)
        norma.write_pending_reemit("config-changed")
        ctx = ops.testing.Context(NormaCharm)
        with ctx(ctx.on.config_changed(), ops.testing.State(resources={norma_bin})) as mgr:
            mgr.run()
            cc = [e for e in mgr.charm._event_ledger if e["event_name"] == "config-changed"]
            assert len(cc) == 1
            assert cc[0]["extra"].get("re-emitted") == "true"
        # The one-shot pending flag is cleared after tagging.
        assert norma.read_pending_reemit() == ""

    def test_non_pending_event_not_tagged(self, norma_bin):
        # A reconcile of a DIFFERENT event leaves the pending flag and adds no tag.
        norma.write_defer_armed(False)
        norma.write_pending_reemit("install")
        ctx = ops.testing.Context(NormaCharm)
        with ctx(ctx.on.config_changed(), ops.testing.State(resources={norma_bin})) as mgr:
            mgr.run()
            cc = [e for e in mgr.charm._event_ledger if e["event_name"] == "config-changed"]
            assert cc and "re-emitted" not in cc[0]["extra"]
        assert norma.read_pending_reemit() == "install"


# ----------------------------------------------------------------------------- #
#  Not-deferred paths: disarmed, or non-deferrable events                       #
# ----------------------------------------------------------------------------- #


class TestNotDeferred:
    def test_config_changed_not_deferred_when_disarmed(self, norma_bin):
        # Default state: flag absent on disk → not armed → normal reconcile.
        ctx = ops.testing.Context(NormaCharm)
        with ctx(ctx.on.config_changed(), ops.testing.State(resources={norma_bin})) as mgr:
            out = mgr.run()
            cc_entries = [
                e for e in mgr.charm._event_ledger if e["event_name"] == "config-changed"
            ]
            # Reconcile logged the event WITHOUT the deferred marker.
            assert len(cc_entries) == 1
            assert cc_entries[0]["extra"] == {}
        assert len(out.deferred) == 0

    def test_update_status_not_deferred_even_when_armed(self, monkeypatch, norma_bin):
        # update-status is in the skip set: armed flag must NOT defer it, and
        # must NOT be consumed by it.
        monkeypatch.setattr(norma, "read_defer_armed", lambda: True)
        ctx = ops.testing.Context(NormaCharm)
        with ctx(ctx.on.update_status(), ops.testing.State(resources={norma_bin})) as mgr:
            out = mgr.run()
            # Flag NOT consumed — still armed after a skipped event.
            assert mgr.charm._defer_armed is True
            us_entries = [e for e in mgr.charm._event_ledger if e["event_name"] == "update-status"]
            # Logged by reconcile (not deferred → no deferred marker).
            assert len(us_entries) == 1
            assert us_entries[0]["extra"] == {}
        assert len(out.deferred) == 0
        # Disk flag also untouched → still armed.
        assert norma.read_defer_armed() is True


# ----------------------------------------------------------------------------- #
#  Full arm-then-fire cycle through the public action surface                   #
# ----------------------------------------------------------------------------- #


class TestArmThenFireCycle:
    def test_action_arms_then_next_event_defers(self, norma_bin):
        ctx = ops.testing.Context(NormaCharm)
        # 1) Arm via the action (persists flag to tmp-isolated disk).
        ctx.run(ctx.on.action("test-defer", params={"arm": True}), ops.testing.State())
        assert norma.read_defer_armed() is True

        # 2) Fire a gated event in a fresh Context (reads the persisted flag in __init__).
        ctx2 = ops.testing.Context(NormaCharm)
        out2 = ctx2.run(ctx2.on.config_changed(), ops.testing.State(resources={norma_bin}))
        assert len(out2.deferred) == 1
        # One-shot: flag cleared on disk.
        assert norma.read_defer_armed() is False

        # 3) A subsequent event is NOT deferred.
        ctx3 = ops.testing.Context(NormaCharm)
        out3 = ctx3.run(ctx3.on.config_changed(), ops.testing.State(resources={norma_bin}))
        assert len(out3.deferred) == 0
