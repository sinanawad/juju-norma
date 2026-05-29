"""Unit tests (ops.testing / Scenario) for F5 action capability surface.

Scope: the calibration/diagnostic actions of the juju-norma MACHINE charm —
run-check, test-networking, check-security, get-version, set-status,
fail-action, get-event-log.

Machine substrate has NO containers: workload readiness is driven by the
WorkloadDriver, monkeypatched here so no host side effects occur. These actions
do NOT run the holistic reconciler (only lifecycle/relation/storage events do),
so none of them require the ``norma-bin`` resource to be declared in State.
"""

import json

import ops
import ops.testing
import pytest

import workload_driver
from charm import NormaCharm


def _patch_running(monkeypatch, *, running):
    """Force SystemdDriver.service_running to a fixed value (no host call)."""
    monkeypatch.setattr(workload_driver.SystemdDriver, "service_running", lambda self: running)


class TestRunCheck:
    def test_systemd_pass_when_service_running(self, monkeypatch):
        _patch_running(monkeypatch, running=True)
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("run-check", params={"check": "systemd"}), ops.testing.State())
        assert ctx.action_results["check"] == "systemd"
        assert ctx.action_results["result"] == "pass"

    def test_systemd_fail_when_service_not_running(self, monkeypatch):
        _patch_running(monkeypatch, running=False)
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("run-check", params={"check": "systemd"}), ops.testing.State())
        assert ctx.action_results["check"] == "systemd"
        assert ctx.action_results["result"] == "fail"

    def test_config_pass_when_valid(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("run-check", params={"check": "config"}),
            ops.testing.State(config={"calibration-int": 8080}),
        )
        assert ctx.action_results["check"] == "config"
        assert ctx.action_results["result"] == "pass"

    def test_config_fail_when_invalid(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("run-check", params={"check": "config"}),
            ops.testing.State(config={"calibration-int": 0}),
        )
        assert ctx.action_results["check"] == "config"
        assert ctx.action_results["result"] == "fail"
        # An invalid config check must surface an explanatory message.
        assert "calibration-int" in ctx.action_results["details"]

    def test_unknown_check_fails(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("run-check", params={"check": "bogus"}), ops.testing.State())
        assert ctx.action_results["result"] == "fail"
        assert "Unknown check" in ctx.action_results["details"]


class TestTestNetworking:
    def test_opened_ports_and_bindings_are_json(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("test-networking"), ops.testing.State())
        ports = json.loads(ctx.action_results["opened-ports"])
        bindings = json.loads(ctx.action_results["bindings"])
        assert isinstance(ports, list)
        assert isinstance(bindings, dict)

    def test_opened_ports_reflects_state(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("test-networking"),
            ops.testing.State(opened_ports={ops.testing.TCPPort(8080)}),
        )
        ports = json.loads(ctx.action_results["opened-ports"])
        assert "8080/tcp" in ports


class TestCheckSecurity:
    def test_security_posture_fields(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("check-security"), ops.testing.State())
        results = ctx.action_results
        # UID/GID are numeric strings (the charm process identity).
        assert results["charm-uid"].isdigit()
        assert results["charm-gid"].isdigit()
        assert results["substrate"] == "machine"
        assert results["k8s-api-reachable"] == "n/a"
        assert results["workload-user"] == "root"
        assert "trust-available" in results


class TestGetVersion:
    def test_charm_dev_workload_unavailable_when_not_ready(self):
        # Default driver: binary absent on the test host → is_ready() False →
        # workload-version reported as "unavailable".
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("get-version"), ops.testing.State())
        assert ctx.action_results["charm-version"] == "dev"
        assert ctx.action_results["workload-version"] == "unavailable"


class TestSetStatus:
    @pytest.mark.parametrize("status_name", ["blocked", "waiting", "maintenance"])
    def test_force_non_active_status(self, status_name):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("set-status", params={"status": status_name, "message": "x"}),
            ops.testing.State(),
        )
        assert ctx.action_results["new-status"] == status_name
        assert ctx.action_results["previous-status"] == "none"

    def test_active_clears_forced_status(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("set-status", params={"status": "active"}), ops.testing.State())
        assert ctx.action_results["new-status"] == "active"

    def test_unknown_status_fails(self):
        ctx = ops.testing.Context(NormaCharm)
        with pytest.raises(ops.testing.ActionFailed) as exc:
            ctx.run(
                ctx.on.action("set-status", params={"status": "nonsense"}),
                ops.testing.State(),
            )
        assert "Unknown status type: nonsense" in str(exc.value)


class TestFailAction:
    def test_fail_raises_with_message(self):
        ctx = ops.testing.Context(NormaCharm)
        with pytest.raises(ops.testing.ActionFailed) as exc:
            ctx.run(
                ctx.on.action("fail-action", params={"message": "boom"}),
                ops.testing.State(),
            )
        assert "boom" in str(exc.value)

    def test_fail_default_message(self):
        ctx = ops.testing.Context(NormaCharm)
        with pytest.raises(ops.testing.ActionFailed) as exc:
            ctx.run(ctx.on.action("fail-action"), ops.testing.State())
        assert "Intentional failure for testing" in str(exc.value)


class TestGetEventLog:
    def test_returns_events_json_count_unit(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("get-event-log"), ops.testing.State())
        events = json.loads(ctx.action_results["events"])
        assert isinstance(events, list)
        assert int(ctx.action_results["count"]) == len(events)
        assert ctx.action_results["unit"] == "juju-norma/0"
