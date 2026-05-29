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
