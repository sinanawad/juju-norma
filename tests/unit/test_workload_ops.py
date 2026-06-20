"""Unit tests for F5b test-workload-ops action (machine analogue of test-pebble-ops).

The conftest autouse fixture stubs subprocess.run, so systemctl-backed ops
(service-status) succeed; the binary is absent on the test host, so ops gated on
``is_ready()`` (service-restart, binary-check) fail gracefully — the action must
never crash and must report a pass/total summary.
"""

import ops
import ops.testing

import workload_driver
from charm import NormaCharm


class TestWorkloadOps:
    def test_summary_and_file_ops_pass(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("test-workload-ops"), ops.testing.State())
        r = ctx.action_results
        for op in ("file-write", "file-read", "file-exists", "file-remove"):
            assert r[op] == "pass", f"{op}={r[op]}"
        assert r["service-status"] == "pass"  # systemctl stubbed → query succeeds
        assert "summary" in r and "passed" in r["summary"]

    def test_binary_ops_fail_gracefully_without_binary(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("test-workload-ops"), ops.testing.State())
        r = ctx.action_results
        # Binary absent on the test host → these report failure, not a crash.
        assert r["service-restart"].startswith("fail")
        assert r["binary-check"].startswith("fail")

    def test_all_pass_when_ready(self, monkeypatch):
        monkeypatch.setattr(workload_driver.SystemdDriver, "is_ready", lambda self: True)
        monkeypatch.setattr(workload_driver.SystemdDriver, "restart", lambda self: None)
        monkeypatch.setattr(workload_driver.SystemdDriver, "service_running", lambda self: True)
        monkeypatch.setattr(workload_driver.SystemdDriver, "exec_check", lambda self: None)
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("test-workload-ops"), ops.testing.State())
        assert ctx.action_results["summary"] == "7/7 passed"


class TestCrashWorkload:
    """F1: the crash-workload action SIGKILLs the workload (systemd restarts it)."""

    def test_crash_reports_killed(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("crash-workload"), ops.testing.State())
        r = ctx.action_results
        assert r["killed"] == "true"
        # subprocess stubbed → NRestarts query returns "" → restart_count() == -1.
        assert r["restart-count-before"] == "-1"


class TestSetHealth:
    """F5: the set-health action toggles the workload health flag file."""

    def test_set_unhealthy_writes_flag(self):
        import os

        import norma

        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("set-health", params={"healthy": False}), ops.testing.State())
        assert ctx.action_results["healthy"] == "false"
        assert os.path.exists(norma.HEALTH_FLAG_FILE)

    def test_set_healthy_removes_flag(self):
        import os

        import norma

        os.makedirs(os.path.dirname(norma.HEALTH_FLAG_FILE), exist_ok=True)
        with open(norma.HEALTH_FLAG_FILE, "w") as f:
            f.write("unhealthy")
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("set-health", params={"healthy": True}), ops.testing.State())
        assert ctx.action_results["healthy"] == "true"
        assert not os.path.exists(norma.HEALTH_FLAG_FILE)
