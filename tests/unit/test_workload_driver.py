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


def _fake_systemctl(calls, returncode=0, stdout=""):
    """Return a subprocess.run replacement recording argv and returning a code.

    ``stdout`` is what ``systemctl is-active <svc>`` (no --quiet) reports, used by
    SystemdDriver.service_state().
    """

    def _run(argv, **kwargs):
        calls.append(argv)
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

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

    def test_unchanged_and_active_is_noop(self, tmp_path, monkeypatch):
        # Pre-write the exact desired unit; service already active.
        desired = norma.build_systemd_unit(8080, "1.0.0", {})
        (tmp_path / "norma.service").write_text(desired)
        calls: list[list[str]] = []
        monkeypatch.setattr(
            workload_driver.subprocess, "run", _fake_systemctl(calls, stdout="active\n")
        )
        driver = _driver(tmp_path)

        driver.apply(port=8080, version="1.0.0", env={})

        verbs = [c[1] for c in calls]
        # No daemon-reload, no restart — only enable (idempotent) + is-active probe.
        assert "daemon-reload" not in verbs
        assert "restart" not in verbs
        assert "enable" in verbs

    def test_unchanged_but_inactive_restarts(self, tmp_path, monkeypatch):
        desired = norma.build_systemd_unit(8080, "1.0.0", {})
        (tmp_path / "norma.service").write_text(desired)
        calls: list[list[str]] = []
        # service_state() == "inactive" (cleanly stopped) → restart, no reset-failed.
        monkeypatch.setattr(
            workload_driver.subprocess, "run", _fake_systemctl(calls, stdout="inactive\n")
        )
        driver = _driver(tmp_path)

        driver.apply(port=8080, version="1.0.0", env={})

        verbs = [c[1] for c in calls]
        assert "daemon-reload" not in verbs  # unit unchanged
        assert "reset-failed" not in verbs  # M2: do not mask a crash-loop
        assert "restart" in verbs  # but it was inactive, so bring it back

    def test_unchanged_and_failed_does_not_restart(self, tmp_path, monkeypatch):
        # M2: a crash-looped unit latched into `failed` must NOT be reset/restarted
        # by an unchanged reconcile — it stays failed so the charm surfaces Blocked.
        desired = norma.build_systemd_unit(8080, "1.0.0", {})
        (tmp_path / "norma.service").write_text(desired)
        calls: list[list[str]] = []
        monkeypatch.setattr(
            workload_driver.subprocess, "run", _fake_systemctl(calls, stdout="failed\n")
        )
        driver = _driver(tmp_path)

        driver.apply(port=8080, version="1.0.0", env={})

        verbs = [c[1] for c in calls]
        assert "restart" not in verbs
        assert "reset-failed" not in verbs

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


class TestWorkloadVersion:
    def test_returns_version_from_http(self, tmp_path, monkeypatch):
        import contextlib
        import io

        @contextlib.contextmanager
        def _fake_urlopen(url, timeout=2):
            assert "9090/version" in url
            yield io.BytesIO(b'{"version": "1.2.3"}')

        monkeypatch.setattr(workload_driver.urllib.request, "urlopen", _fake_urlopen)
        assert _driver(tmp_path).workload_version(9090) == "1.2.3"

    def test_returns_empty_on_connection_error(self, tmp_path, monkeypatch):
        def _boom(url, timeout=2):
            raise workload_driver.urllib.error.URLError("refused")

        monkeypatch.setattr(workload_driver.urllib.request, "urlopen", _boom)
        assert _driver(tmp_path).workload_version(8080) == ""

    def test_returns_empty_on_malformed_json(self, tmp_path, monkeypatch):
        import contextlib
        import io

        @contextlib.contextmanager
        def _fake_urlopen(url, timeout=2):
            yield io.BytesIO(b"not json")

        monkeypatch.setattr(workload_driver.urllib.request, "urlopen", _fake_urlopen)
        assert _driver(tmp_path).workload_version() == ""


class TestServiceState:
    def test_state_word_returned(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            workload_driver.subprocess, "run", _fake_systemctl([], stdout="active\n")
        )
        assert _driver(tmp_path).service_state() == "active"

    def test_failed_detected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            workload_driver.subprocess, "run", _fake_systemctl([], stdout="failed\n")
        )
        d = _driver(tmp_path)
        assert d.service_state() == "failed"
        assert d.service_failed() is True

    def test_empty_output_is_unknown(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workload_driver.subprocess, "run", _fake_systemctl([], stdout=""))
        assert _driver(tmp_path).service_state() == "unknown"
        assert _driver(tmp_path).service_failed() is False


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

    def test_noop_when_content_identical(self, tmp_path):
        # P2-4: re-installing identical bytes is a no-op and reports unchanged,
        # so a juju refresh shipping the same binary doesn't churn/restart.
        source = tmp_path / "src-norma"
        source.write_text("#!/bin/true\n")
        driver = _driver(tmp_path)
        assert driver.install_binary(str(source)) is True  # first lay-down → changed
        assert driver.install_binary(str(source)) is False  # identical → no-op

    def test_reinstalls_when_content_differs(self, tmp_path):
        # P2-4: NEW bytes (attach-resource / refresh) are picked up and reported
        # as changed — the bug this fix closes (stale binary served forever).
        source = tmp_path / "src-norma"
        source.write_text("v1\n")
        driver = _driver(tmp_path)
        assert driver.install_binary(str(source)) is True
        source.write_text("v2-different\n")
        assert driver.install_binary(str(source)) is True
        assert (tmp_path / "norma").read_text() == "v2-different\n"


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


class TestSupervision:
    """F1/F5 supervision seam: kill, restart_count, set_health."""

    def test_kill_sends_sigkill(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(workload_driver.subprocess, "run", _fake_systemctl(calls))
        _driver(tmp_path).kill()
        assert any("kill" in c and "--signal=SIGKILL" in c for c in calls), calls

    def test_restart_count_parses_nrestarts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workload_driver.subprocess, "run", _fake_systemctl([], stdout="3\n"))
        assert _driver(tmp_path).restart_count() == 3

    def test_restart_count_unknown_is_negative_one(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workload_driver.subprocess, "run", _fake_systemctl([], stdout=""))
        assert _driver(tmp_path).restart_count() == -1

    def test_set_health_writes_and_removes_flag(self, tmp_path):
        # conftest isolates norma.HEALTH_FLAG_FILE to tmp_path/norma-unhealthy.
        flag = tmp_path / "norma-unhealthy"
        d = _driver(tmp_path)
        d.set_health(False)
        assert flag.exists()
        d.set_health(True)
        assert not flag.exists()

    def test_set_health_idempotent_when_already_healthy(self, tmp_path):
        # Removing an absent flag must not raise.
        _driver(tmp_path).set_health(True)
        assert not (tmp_path / "norma-unhealthy").exists()
