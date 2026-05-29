"""Unit tests for the WorkloadDriver seam (src/workload_driver.py).

Plain pytest, zero ops dependency. Host side effects (systemctl) are
monkeypatched; file operations use tmp paths so nothing touches the real host.
"""

import subprocess
import types

import pytest

import norma
import workload_driver
from workload_driver import SystemdDriver, WorkloadError


def _driver(tmp_path):
    return SystemdDriver(
        service_name="norma",
        binary_path=str(tmp_path / "norma"),
        unit_path=str(tmp_path / "norma.service"),
    )


def _fake_systemctl(calls, returncode=0):
    """Return a subprocess.run replacement recording argv and returning a code."""

    def _run(argv, **kwargs):
        calls.append(argv)
        return types.SimpleNamespace(returncode=returncode, stderr="")

    return _run


class TestIsReady:
    def test_false_when_binary_absent(self, tmp_path):
        assert _driver(tmp_path).is_ready() is False

    def test_true_when_binary_present(self, tmp_path):
        (tmp_path / "norma").write_text("#!/bin/true\n")
        assert _driver(tmp_path).is_ready() is True


class TestApply:
    def test_fresh_install_writes_unit_and_starts(self, tmp_path, monkeypatch):
        calls: list[list[str]] = []
        monkeypatch.setattr(workload_driver.subprocess, "run", _fake_systemctl(calls))
        driver = _driver(tmp_path)

        driver.apply(port=9090, version="1.2.3", env={})

        unit_text = (tmp_path / "norma.service").read_text()
        assert f"ExecStart={norma.BINARY_PATH} --port 9090" in unit_text
        assert 'Environment="VERSION=1.2.3"' in unit_text
        # Unit absent → changed → write, reload, enable, reset-failed, restart.
        verbs = [c[1] for c in calls]
        assert verbs == ["daemon-reload", "enable", "reset-failed", "restart"]

    def test_unchanged_and_running_is_noop(self, tmp_path, monkeypatch):
        # Pre-write the exact desired unit; service already active.
        desired = norma.build_systemd_unit(8080, "1.0.0", {})
        (tmp_path / "norma.service").write_text(desired)
        calls: list[list[str]] = []
        monkeypatch.setattr(
            workload_driver.subprocess, "run", _fake_systemctl(calls, returncode=0)
        )
        driver = _driver(tmp_path)

        driver.apply(port=8080, version="1.0.0", env={})

        verbs = [c[1] for c in calls]
        # No daemon-reload, no restart — only enable (idempotent) + is-active probe.
        assert "daemon-reload" not in verbs
        assert "restart" not in verbs
        assert "enable" in verbs

    def test_unchanged_but_stopped_restarts(self, tmp_path, monkeypatch):
        desired = norma.build_systemd_unit(8080, "1.0.0", {})
        (tmp_path / "norma.service").write_text(desired)
        calls: list[list[str]] = []
        # is-active returns non-zero (stopped) → restart path.
        monkeypatch.setattr(
            workload_driver.subprocess, "run", _fake_systemctl(calls, returncode=3)
        )
        driver = _driver(tmp_path)

        driver.apply(port=8080, version="1.0.0", env={})

        verbs = [c[1] for c in calls]
        assert "daemon-reload" not in verbs  # unit unchanged
        assert "restart" in verbs  # but it was not running

    def test_write_failure_raises_workload_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workload_driver.subprocess, "run", _fake_systemctl([]))
        driver = SystemdDriver(
            binary_path=str(tmp_path / "norma"),
            unit_path="/no/such/dir/norma.service",
        )
        with pytest.raises(WorkloadError):
            driver.apply(port=8080, version="dev", env={})


class TestServiceRunning:
    def test_running_true_on_zero_exit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workload_driver.subprocess, "run", _fake_systemctl([], returncode=0))
        assert _driver(tmp_path).service_running() is True

    def test_running_false_on_nonzero_exit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workload_driver.subprocess, "run", _fake_systemctl([], returncode=3))
        assert _driver(tmp_path).service_running() is False


class TestSystemctlErrors:
    def test_missing_systemctl_raises(self, tmp_path, monkeypatch):
        def _boom(argv, **kwargs):
            raise FileNotFoundError

        monkeypatch.setattr(workload_driver.subprocess, "run", _boom)
        with pytest.raises(WorkloadError, match="systemctl not found"):
            _driver(tmp_path).restart()

    def test_called_process_error_raises(self, tmp_path, monkeypatch):
        def _fail(argv, **kwargs):
            raise subprocess.CalledProcessError(1, argv, stderr="boom")

        monkeypatch.setattr(workload_driver.subprocess, "run", _fail)
        with pytest.raises(WorkloadError, match="failed"):
            _driver(tmp_path).restart()


class TestInstallBinary:
    def test_copies_and_makes_executable(self, tmp_path):
        source = tmp_path / "src-norma"
        source.write_text("#!/bin/true\n")
        driver = _driver(tmp_path)

        driver.install_binary(str(source))

        dest = tmp_path / "norma"
        assert dest.exists()
        assert dest.stat().st_mode & 0o111  # executable bit set

    def test_missing_source_raises(self, tmp_path):
        with pytest.raises(WorkloadError, match="failed to install binary"):
            _driver(tmp_path).install_binary(str(tmp_path / "does-not-exist"))


class TestTeardown:
    def test_removes_unit_file_idempotently(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workload_driver.subprocess, "run", _fake_systemctl([]))
        unit = tmp_path / "norma.service"
        unit.write_text("stub")
        driver = _driver(tmp_path)

        driver.teardown()
        assert not unit.exists()
        # Idempotent: a second teardown does not raise.
        driver.teardown()
