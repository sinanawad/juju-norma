"""Unit tests for integration wait-timeout diagnostics.

The unit environment has no juju or lxc. Every subprocess invocation is mocked.
"""

import importlib.util
import pathlib
import re
import subprocess
import sys
import types

import pytest

_DIAG_PATH = pathlib.Path(__file__).resolve().parents[1] / "integration" / "diagnostics.py"
_spec = importlib.util.spec_from_file_location("integration_diagnostics", _DIAG_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load {_DIAG_PATH}")
diag = importlib.util.module_from_spec(_spec)
sys.modules["integration_diagnostics"] = diag
_spec.loader.exec_module(diag)

_WAIT_CALL = re.compile(r"\b(?:juju|j)\.wait\(")
_SUBORDINATE_NODE = (
    "tests/integration/test_subordinate.py::TestSubordinate::test_colocated_via_juju_info (call)"
)

_STATUS = """\
machines:
  "0":
    instance-id: juju-deadbe-0
    machine-status:
      current: running
    juju-status:
      current: pending
  "1":
    instance-id: pending
  "2":
    instance-id: juju-deadbe-2
"""

_LXC_LIST = """\
+----------------+---------+
|      NAME      |  STATE  |
+----------------+---------+
| juju-deadbe-0  | RUNNING |
| juju-c0ffee-0  | RUNNING |
| juju-deadbe-2  | RUNNING |
+----------------+---------+
"""


def _completed(code: int, stdout: str, stderr: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


class _Juju:
    def __init__(
        self, *, model="norma-deadbeef", cli_binary="/usr/bin/juju", result=None, error=None
    ):
        self.model = model
        self.cli_binary = cli_binary
        self._result = result
        self._error = error
        self.calls: list[tuple] = []

    def wait(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._error is not None:
            raise self._error
        return self._result


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def scripted_run(monkeypatch):
    """Replace subprocess.run so a forgotten handler cannot touch a real juju or lxc."""
    state: dict = {"calls": [], "kwargs": [], "handler": None}

    def _run(argv, **kwargs):
        state["calls"].append(list(argv))
        state["kwargs"].append(kwargs)
        handler = state["handler"]
        if handler is None:
            missing = argv[0] if argv else "command"
            raise FileNotFoundError(missing)
        return handler(list(argv), kwargs)

    monkeypatch.setattr(diag.subprocess, "run", _run)
    return state


def _root(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "artifacts" / "integration-diagnostics"


def test_each_failing_or_missing_command_is_tolerated(scripted_run, tmp_path):
    """One broken command must not skip the others or raise."""

    def handler(argv, kwargs):
        if "status" in argv:
            raise FileNotFoundError("juju")
        if "debug-log" in argv:
            return _completed(1, "", "permission denied")
        if argv[:2] == ["lxc", "list"]:
            raise subprocess.TimeoutExpired(argv, 20, output=b"partial list", stderr=b"slow")
        raise AssertionError(f"unexpected command {argv}")

    scripted_run["handler"] = handler
    dest = diag.dump_wait_diagnostics(_Juju(), "case")

    assert dest == _root(tmp_path) / "case"
    status = (dest / "juju-status.yaml").read_text()
    debug = (dest / "juju-debug-log.txt").read_text()
    listing = (dest / "lxc-list.txt").read_text()
    assert "FileNotFoundError" in status
    assert "permission denied" in debug
    assert "partial list" in listing
    assert scripted_run["calls"][0][0] == "/usr/bin/juju"
    assert all(kw["timeout"] == diag.COMMAND_TIMEOUT_SECONDS for kw in scripted_run["kwargs"])
    assert diag.COMMAND_TIMEOUT_SECONDS <= 30
    assert all(kw["stdin"] is subprocess.DEVNULL for kw in scripted_run["kwargs"])


def test_successful_dump_writes_status_debug_log_and_instance_logs(scripted_run, tmp_path):
    def handler(argv, kwargs):
        if argv[1:4] == ["status", "--format=yaml", "-m"]:
            assert argv == [
                "/usr/bin/juju",
                "status",
                "--format=yaml",
                "-m",
                "norma-deadbeef",
            ]
            return _completed(0, _STATUS)
        if "debug-log" in argv:
            assert argv == [
                "/usr/bin/juju",
                "debug-log",
                "--replay",
                "--no-tail",
                "-m",
                "norma-deadbeef",
            ]
            return _completed(0, "UNIT_AGENT_NEVER_STARTED\n")
        if argv == ["lxc", "list"]:
            return _completed(0, _LXC_LIST)
        if argv[:4] == ["lxc", "exec", argv[2], "--"] and argv[4:] == [
            "tail",
            "-n",
            "300",
            "/var/log/cloud-init-output.log",
        ]:
            return _completed(0, f"cloud-init-{argv[2]}\n")
        if argv[:3] == ["lxc", "exec", argv[2]] and "machine-*.log" in " ".join(argv):
            assert "tail -n 300" in " ".join(argv)
            return _completed(0, f"machine-log-{argv[2]}\n")
        raise AssertionError(f"unexpected command {argv}")

    scripted_run["handler"] = handler
    dest = diag.dump_wait_diagnostics(_Juju(), "juju")

    exec_targets = [c[2] for c in scripted_run["calls"] if c[:2] == ["lxc", "exec"]]
    assert exec_targets == [
        "juju-deadbe-0",
        "juju-deadbe-0",
        "juju-deadbe-2",
        "juju-deadbe-2",
    ]
    assert "juju-c0ffee-0" not in exec_targets

    assert "instance-id: juju-deadbe-0" in (dest / "juju-status.yaml").read_text()
    assert "current: pending" in (dest / "juju-status.yaml").read_text()
    assert "UNIT_AGENT_NEVER_STARTED" in (dest / "juju-debug-log.txt").read_text()
    assert "juju-deadbe-0" in (dest / "lxc-list.txt").read_text()
    for instance in ("juju-deadbe-0", "juju-deadbe-2"):
        inst = dest / instance
        assert f"cloud-init-{instance}" in (inst / "cloud-init-output.log").read_text()
        assert f"machine-log-{instance}" in (inst / "juju-machine-logs.txt").read_text()
    assert not (dest / "juju-c0ffee-0").exists()


def test_debug_log_keeps_the_last_2000_lines(scripted_run, tmp_path):
    payload = "".join(f"line-{i}\n" for i in range(2500))

    def handler(argv, kwargs):
        if "debug-log" in argv:
            return _completed(0, payload)
        if argv == ["lxc", "list"]:
            return _completed(0, "")
        return _completed(0, "machines: {}\n")

    scripted_run["handler"] = handler
    dest = diag.dump_wait_diagnostics(_Juju(), "logs")
    stdout = (dest / "juju-debug-log.txt").read_text().split("--- stdout ---\n", 1)[1]
    lines = stdout.splitlines()
    assert lines[0] == "line-500"
    assert lines[-1] == "line-2499"
    assert len(lines) == 2000


def test_lxc_list_is_used_when_status_has_no_instance_id(scripted_run, tmp_path):
    listing = """\
+----------------+---------+
|      NAME      |  STATE  |
+----------------+---------+
| juju-abc123-0  | RUNNING |
| other-box      | RUNNING |
+----------------+---------+
"""

    def handler(argv, kwargs):
        if "status" in argv:
            return _completed(2, "this is not status", "error: model not found")
        if argv == ["lxc", "list"]:
            return _completed(0, listing)
        if argv[:3] == ["lxc", "exec", "juju-abc123-0"]:
            return _completed(1, "", "cloud-init log not present")
        raise AssertionError(f"unexpected command {argv}")

    scripted_run["handler"] = handler
    dest = diag.dump_wait_diagnostics(_Juju(), "fallback")

    exec_targets = [c[2] for c in scripted_run["calls"] if c[:2] == ["lxc", "exec"]]
    assert exec_targets == ["juju-abc123-0", "juju-abc123-0"]
    assert "other-box" not in exec_targets
    cloud = (dest / "juju-abc123-0" / "cloud-init-output.log").read_text()
    assert "cloud-init log not present" in cloud
    assert (dest / "juju-abc123-0" / "juju-machine-logs.txt").exists()


def test_model_flag_is_omitted_when_the_juju_object_has_no_model(scripted_run):
    def handler(argv, kwargs):
        return _completed(0, "machines: {}\n" if "status" in argv else "")

    scripted_run["handler"] = handler
    diag.dump_wait_diagnostics(_Juju(model=None), "current-model")
    for argv in scripted_run["calls"]:
        assert "-m" not in argv
        assert "--model" not in argv


def test_diagnostic_directory_stays_under_the_workspace_root(scripted_run, tmp_path):
    diag.dump_wait_diagnostics(_Juju(), "../../outside")
    root = _root(tmp_path)
    written = [path for path in root.rglob("*") if path.is_file()]
    assert written
    assert all(path.resolve().is_relative_to(root.resolve()) for path in written)
    assert not (tmp_path / "outside").exists()


def test_workspace_defaults_to_the_current_directory(scripted_run, tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    diag.dump_wait_diagnostics(_Juju(), "local")
    assert (
        tmp_path / "artifacts" / "integration-diagnostics" / "local" / "juju-status.yaml"
    ).is_file()


def test_wait_forwards_arguments_and_returns_the_status(scripted_run):
    ready = object()
    status = object()
    juju = _Juju(result=status)
    assert diag.wait(juju, ready, timeout=600, error=None, delay=2, successes=1) is status
    assert juju.calls == [((ready,), {"timeout": 600, "error": None, "delay": 2, "successes": 1})]
    assert scripted_run["calls"] == []


def test_wait_reraises_the_same_timeout_and_writes_output(scripted_run, tmp_path):
    err = TimeoutError("wait timed out after 600s")
    juju = _Juju(error=err)
    ready = object()
    with pytest.raises(TimeoutError) as raised:
        diag.wait(juju, ready, timeout=600, successes=3, diag_name="juju")
    assert raised.value is err
    assert juju.calls == [((ready,), {"timeout": 600, "successes": 3})]
    status = (_root(tmp_path) / "juju" / "juju-status.yaml").read_text()
    assert "FileNotFoundError" in status


def test_second_timeout_does_not_clobber_the_first_dump(scripted_run, tmp_path):
    def handler(argv, kwargs):
        if "status" in argv:
            seen = sum(1 for call in scripted_run["calls"] if "status" in call)
            return _completed(0, f"status-dump-{seen}\n")
        return _completed(0, "")

    scripted_run["handler"] = handler
    err = TimeoutError("again")
    juju = _Juju(error=err)
    for _ in range(2):
        with pytest.raises(TimeoutError) as raised:
            diag.wait(juju, object(), timeout=120, diag_name="same")
        assert raised.value is err

    root = _root(tmp_path)
    assert "status-dump-1" in (root / "same" / "juju-status.yaml").read_text()
    assert "status-dump-2" in (root / "same-2" / "juju-status.yaml").read_text()


def test_non_timeout_errors_skip_diagnostics(scripted_run):
    err = RuntimeError("unit in error")
    juju = _Juju(error=err)
    with pytest.raises(RuntimeError) as raised:
        diag.wait(juju, object(), timeout=180)
    assert raised.value is err
    assert scripted_run["calls"] == []


def test_original_timeout_survives_a_diagnostics_bug(monkeypatch):
    def boom(juju, name):
        raise RuntimeError("disk full")

    monkeypatch.setattr(diag, "dump_wait_diagnostics", boom)
    err = TimeoutError("waiting for machine")
    juju = _Juju(error=err)
    with pytest.raises(TimeoutError) as raised:
        diag.wait(juju, object(), timeout=600)
    assert raised.value is err


def test_wait_names_the_directory_after_the_current_test(scripted_run, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", _SUBORDINATE_NODE)

    def handler(argv, kwargs):
        return _completed(0, "machines: {}\n" if "status" in argv else "")

    scripted_run["handler"] = handler
    with pytest.raises(TimeoutError):
        diag.wait(_Juju(error=TimeoutError("stalled")), object(), timeout=900)

    name = "test_subordinate.py-TestSubordinate-test_colocated_via_juju_info"
    assert (_root(tmp_path) / name / "juju-status.yaml").is_file()


def test_explicit_diag_name_wins_over_the_current_test(scripted_run, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", _SUBORDINATE_NODE)
    with pytest.raises(TimeoutError):
        diag.wait(_Juju(error=TimeoutError("stalled")), object(), timeout=600, diag_name="juju")
    root = _root(tmp_path)
    assert (root / "juju" / "juju-status.yaml").is_file()
    assert list(root.iterdir()) == [root / "juju"]


def test_every_jubilant_wait_is_wrapped():
    root = pathlib.Path(__file__).resolve().parents[1] / "integration"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "diagnostics.py":
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if _WAIT_CALL.search(line):
                offenders.append(f"{path.relative_to(root)}:{lineno}:{line.strip()}")
    assert offenders == []
