"""F1/F5 workload supervision under fault (jubilant, LXD).

Roadmap P3-10. Machine-distinct: the workload is a systemd-supervised process, not a
Pebble-managed container. Calibrates (1) crash recovery — SIGKILL the process and
systemd's ``Restart=on-failure`` brings it back, with the NRestarts counter
incrementing; and (2) health toggle — driving the workload externally unhealthy
makes the charm's ``--check`` self-probe fail. Runs on the session app (both
recover to healthy/active). Full-suite only (crash/restart timing).
"""

import json
import time

import jubilant

from .conftest import APP


def _scrape_norma_healthy(juju: jubilant.Juju) -> str:
    """Read `norma_healthy` from the workload's own /metrics on the unit, live.

    Asserts exactly the series Prometheus scrapes (and the shipped health alert
    fires on), not the charm's view of health.
    """
    port = juju.config(APP).get("calibration-int", 8080)
    # test-workload-ops ends with a service restart and the unit is Type=simple, so
    # the port may not be bound yet: retry refused connections, bounded.
    task = juju.exec(
        f"curl -fsS -m 5 --retry 5 --retry-connrefused --retry-delay 1 localhost:{port}/metrics",
        unit=f"{APP}/leader",
        wait=60,
    )
    for line in task.stdout.splitlines():
        if line.startswith("norma_healthy "):
            return line.split()[1]
    raise AssertionError(f"norma_healthy absent from /metrics:\n{task.stdout}")


def _restart_count(juju: jubilant.Juju) -> int:
    svc = json.loads(
        juju.run(f"{APP}/leader", "introspect", params={"sections": "systemd-service"}).results[
            "systemd-service"
        ]
    )
    return int(svc.get("restart-count", -1))


class TestWorkloadSupervision:
    def test_crash_is_restarted_by_systemd(self, juju: jubilant.Juju):
        """F1: SIGKILL the workload → systemd Restart=on-failure brings it back."""
        before = _restart_count(juju)
        assert before >= 0, f"restart-count unavailable from introspect: {before}"

        juju.run(f"{APP}/leader", "crash-workload")

        # systemd restarts after RestartSec=5; poll the NRestarts counter increment.
        deadline = time.monotonic() + 90
        after = before
        while time.monotonic() < deadline:
            after = _restart_count(juju)
            if after > before:
                break
            time.sleep(10)
        assert after > before, (
            f"systemd did not auto-restart the workload (NRestarts {before} -> {after})"
        )
        # The unit recovers to active (workload reachable again).
        juju.wait(jubilant.all_active, timeout=120)

    def test_set_health_fails_then_recovers_self_probe(self, juju: jubilant.Juju):
        """F5: an externally-unhealthy workload fails the --check self-probe; the
        process stays up (status unchanged), and recovers when marked healthy."""
        juju.run(f"{APP}/leader", "set-health", params={"healthy": False})
        try:
            ops = juju.run(f"{APP}/leader", "test-workload-ops").results
            assert ops.get("binary-check", "").startswith("fail"), (
                f"unhealthy workload should fail the --check self-probe: {ops}"
            )
            # The metric follows the action. Before the canonical workload it
            # never moved (a plain Gauge set only by the HTTP toggle), so the
            # shipped NormaWorkloadUnhealthy alert could never fire.
            assert _scrape_norma_healthy(juju) == "0"
        finally:
            juju.run(f"{APP}/leader", "set-health", params={"healthy": True})

        ops = juju.run(f"{APP}/leader", "test-workload-ops").results
        assert ops.get("binary-check") == "pass", (
            f"healthy workload should pass the --check self-probe: {ops}"
        )
        assert _scrape_norma_healthy(juju) == "1"
