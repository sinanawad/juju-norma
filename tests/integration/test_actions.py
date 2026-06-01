"""F5 actions (class-A + machine workload-ops) + F19 introspect (jubilant, LXD)."""

import json

import jubilant
import pytest

from .conftest import APP


class TestReadOnlyActions:
    def test_get_version(self, juju: jubilant.Juju):
        task = juju.run(f"{APP}/leader", "get-version")
        assert task.results["charm-version"]
        # #4: workload_version now probes GET /version on the running workload.
        assert task.results["workload-version"] not in ("", "unavailable")

    def test_run_check_systemd(self, juju: jubilant.Juju):
        task = juju.run(f"{APP}/leader", "run-check", params={"check": "systemd"})
        assert task.results["result"] == "pass"

    def test_run_check_unknown(self, juju: jubilant.Juju):
        task = juju.run(f"{APP}/leader", "run-check", params={"check": "bogus"})
        assert task.results["result"] == "fail"

    def test_check_security_machine_posture(self, juju: jubilant.Juju):
        task = juju.run(f"{APP}/leader", "check-security")
        assert task.results["substrate"] == "machine"
        assert task.results["k8s-api-reachable"] == "n/a"
        assert task.results["workload-user"] == "root"

    def test_test_networking_opened_ports(self, juju: jubilant.Juju):
        task = juju.run(f"{APP}/leader", "test-networking")
        ports = json.loads(task.results["opened-ports"])
        assert any(p.startswith("8080") for p in ports)

    def test_get_event_log(self, juju: jubilant.Juju):
        task = juju.run(f"{APP}/leader", "get-event-log")
        assert int(task.results["count"]) >= 1
        json.loads(task.results["events"])


class TestWorkloadOps:
    def test_test_workload_ops_all_pass(self, juju: jubilant.Juju):
        """F5b: systemd + file + subprocess suite on the host."""
        task = juju.run(f"{APP}/leader", "test-workload-ops")
        assert "passed" in task.results["summary"]
        # All seven ops pass on a healthy live unit.
        assert (
            task.results["summary"].split("/")[0]
            == task.results["summary"].split("/")[1].split()[0]
        )


class TestFailAndStatus:
    def test_fail_action(self, juju: jubilant.Juju):
        try:
            juju.run(f"{APP}/leader", "fail-action", params={"message": "boom"})
            pytest.fail("fail-action should raise TaskError")
        except jubilant.TaskError as e:
            assert "boom" in e.task.message


class TestIntrospect:
    def test_introspect_all_sections(self, juju: jubilant.Juju):
        task = juju.run(f"{APP}/leader", "introspect")
        for section in ("config", "systemd-service", "leadership", "relations", "storage"):
            assert section in task.results
            json.loads(task.results[section])

    def test_introspect_systemd_collector(self, juju: jubilant.Juju):
        """F19 machine-specific collector (replaces k8s containers)."""
        task = juju.run(f"{APP}/leader", "introspect", params={"sections": "systemd-service"})
        svc = json.loads(task.results["systemd-service"])
        assert svc["binary-present"] is True
        assert svc["service-running"] is True
