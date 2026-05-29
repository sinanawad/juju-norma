"""Unit tests for the juju-norma machine charm using ops.testing (Scenario).

Each test fires one event against an immutable input State and asserts on the
output State (Constitution Principle VI). NOTE: machine State has NO containers
— readiness is driven by the WorkloadDriver, which we monkeypatch so no host
side effects occur.
"""

import json

import ops
import ops.testing
import pytest

import workload_driver
from charm import NormaCharm


@pytest.fixture
def norma_bin(tmp_path):
    """An empty placeholder norma-bin file resource.

    A deployed charm always has the file resource declared; an empty file is
    the realistic "not yet attached" placeholder (resource-get returns a
    zero-byte file), which the charm treats as "binary not delivered".
    """
    placeholder = tmp_path / "norma-bin"
    placeholder.write_bytes(b"")
    return ops.testing.Resource(name="norma-bin", path=placeholder)


def _make_ready(monkeypatch, *, running=True):
    """Make the SystemdDriver report ready + running with a no-op apply."""
    monkeypatch.setattr(workload_driver.SystemdDriver, "is_ready", lambda self: True)
    monkeypatch.setattr(workload_driver.SystemdDriver, "apply", lambda self, **kw: None)
    monkeypatch.setattr(workload_driver.SystemdDriver, "service_running", lambda self: running)


class TestCharmInit:
    def test_install_runs_without_error(self, norma_bin):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.install(), ops.testing.State(resources={norma_bin}))

    def test_config_changed_runs_without_error(self, norma_bin):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.config_changed(), ops.testing.State(resources={norma_bin}))


class TestStatus:
    def test_waiting_when_binary_not_delivered(self):
        # Default driver: binary absent on the test host → Waiting.
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(ctx.on.collect_unit_status(), ops.testing.State())
        assert out.unit_status == ops.WaitingStatus("Waiting for workload binary")

    def test_active_when_ready_and_running(self, monkeypatch):
        _make_ready(monkeypatch)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(ctx.on.collect_unit_status(), ops.testing.State())
        assert out.unit_status == ops.ActiveStatus()

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

    def test_recovery_to_active_after_valid_config(self, monkeypatch):
        _make_ready(monkeypatch)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(ctx.on.config_changed(), ops.testing.State(config={"calibration-int": 8080}))
        assert out.unit_status == ops.ActiveStatus()

    def test_app_status_active_on_leader(self):
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(ctx.on.collect_app_status(), ops.testing.State(leader=True))
        assert out.app_status == ops.ActiveStatus()


class TestWorkloadApply:
    def test_workload_version_and_port_set_when_ready(self, monkeypatch):
        _make_ready(monkeypatch)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(ctx.on.config_changed(), ops.testing.State(config={"calibration-int": 9090}))
        assert out.workload_version == "dev"
        assert ops.testing.TCPPort(9090) in out.opened_ports


class TestResourceLayDown:
    def test_binary_installed_when_resource_attached(self, tmp_path, monkeypatch):
        # Record install_binary calls instead of touching the host.
        installed: list[str] = []
        monkeypatch.setattr(
            workload_driver.SystemdDriver,
            "install_binary",
            lambda self, source: installed.append(source),
        )
        bin_resource = tmp_path / "norma"
        bin_resource.write_text("#!/bin/true\n")
        ctx = ops.testing.Context(NormaCharm)
        state = ops.testing.State(
            resources={ops.testing.Resource(name="norma-bin", path=bin_resource)},
        )
        ctx.run(ctx.on.install(), state)
        assert installed == [str(bin_resource)]

    def test_no_install_when_resource_empty(self, monkeypatch, norma_bin):
        # Empty (placeholder) resource → treated as not delivered → no install.
        installed: list[str] = []
        monkeypatch.setattr(
            workload_driver.SystemdDriver,
            "install_binary",
            lambda self, source: installed.append(source),
        )
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.install(), ops.testing.State(resources={norma_bin}))
        assert installed == []


class TestEventLedger:
    def test_install_recorded(self, norma_bin):
        ctx = ops.testing.Context(NormaCharm)
        with ctx(ctx.on.install(), ops.testing.State(resources={norma_bin})) as mgr:
            mgr.run()
            ledger = mgr.charm._event_ledger
            assert ledger[0]["event_name"] == "install"
            assert ledger[0]["unit_name"] == "juju-norma/0"
            assert "timestamp" in ledger[0]

    def test_stop_recorded(self):
        ctx = ops.testing.Context(NormaCharm)
        with ctx(ctx.on.stop(), ops.testing.State()) as mgr:
            mgr.run()
            names = [e["event_name"] for e in mgr.charm._event_ledger]
            assert "stop" in names


class TestActions:
    def test_get_event_log_structure(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("get-event-log"), ops.testing.State())
        assert ctx.action_results["unit"] == "juju-norma/0"
        json.loads(ctx.action_results["events"])
        assert int(ctx.action_results["count"]) >= 0

    def test_get_config_returns_values(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-config"),
            ops.testing.State(
                config={
                    "calibration-string": "hello",
                    "calibration-int": 9090,
                    "calibration-float": 2.5,
                    "calibration-bool": False,
                }
            ),
        )
        assert ctx.action_results["calibration-string"] == "hello"
        assert ctx.action_results["calibration-int"] == "9090"
        assert ctx.action_results["calibration-float"] == "2.5"
        assert ctx.action_results["calibration-bool"] == "False"
        assert ctx.action_results["calibration-secret"] == "unset"

    def test_get_version(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("get-version"), ops.testing.State())
        assert ctx.action_results["charm-version"] == "dev"
        assert ctx.action_results["workload-version"] == "unavailable"

    def test_set_status_forces_blocked(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("set-status", params={"status": "blocked", "message": "x"}),
            ops.testing.State(),
        )
        assert ctx.action_results["new-status"] == "blocked"
        assert ctx.action_results["previous-status"] == "none"

    def test_set_status_unknown_fails(self):
        ctx = ops.testing.Context(NormaCharm)
        with pytest.raises(ops.testing.ActionFailed) as exc:
            ctx.run(ctx.on.action("set-status", params={"status": "bogus"}), ops.testing.State())
        assert "Unknown status type: bogus" in str(exc.value)

    def test_fail_action_raises(self):
        ctx = ops.testing.Context(NormaCharm)
        with pytest.raises(ops.testing.ActionFailed) as exc:
            ctx.run(
                ctx.on.action("fail-action", params={"message": "custom"}), ops.testing.State()
            )
        assert "custom" in str(exc.value)
