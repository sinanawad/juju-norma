"""F1/F2: lifecycle + systemd workload + file-resource delivery (jubilant, LXD)."""

import jubilant

from .conftest import APP


class TestLifecycle:
    def test_deploy_active_idle(self, juju: jubilant.Juju):
        """The shared fixture already deployed + waited; assert the steady state."""
        status = juju.status()
        unit = next(iter(status.apps[APP].units.values()))
        assert unit.is_active
        assert unit.workload_status.message == ""  # ActiveStatus carries no message

    def test_systemd_service_active(self, juju: jubilant.Juju):
        """F1: the workload runs under a charm-managed systemd unit."""
        result = juju.cli("exec", "--unit", f"{APP}/0", "--", "systemctl", "is-active", "norma")
        assert result.strip() == "active"

    def test_workload_binary_laid_down(self, juju: jubilant.Juju):
        """F2: the file resource was installed at the host binary path, executable."""
        out = juju.cli("exec", "--unit", f"{APP}/0", "--", "test", "-x", "/usr/local/bin/norma")
        # `test -x` exits 0 (no output) when the binary exists and is executable;
        # a non-zero exit would raise CLIError.
        assert out is not None

    def test_health_endpoint(self, juju: jubilant.Juju):
        """F1: the workload serves its HTTP health endpoint on the host."""
        out = juju.cli(
            "exec",
            "--unit",
            f"{APP}/0",
            "--",
            "curl",
            "-sS",
            "-m",
            "5",
            "http://localhost:8080/health",
        )
        assert "OK" in out
