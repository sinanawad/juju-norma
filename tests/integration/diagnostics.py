"""Best-effort diagnostics when a jubilant model wait times out.

Never raises. A failing or missing ``juju`` / ``lxc`` command is recorded and the
original ``TimeoutError`` propagates unchanged.

Artifacts land in ``${GITHUB_WORKSPACE:-.}/artifacts/integration-diagnostics/<name>/``.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import shlex
import subprocess

import yaml

logger = logging.getLogger(__name__)

COMMAND_TIMEOUT_SECONDS = 20
# Juju 4.0 rejects ``--no-tail`` together with ``--lines`` (debuglog.go Init).
# ``--limit N`` prints the N most recent lines and exits; it does not replay.
_DEBUG_LOG_LIMIT = "2000"
_REMOTE_TAIL_LINES = "300"
_STATUS_NAME = "juju-status.yaml"

# ``sh`` so the glob expands inside the instance. With the default shell a
# non-matching glob is left literal, ``[ -f ]`` fails, and the command exits 0.
_MACHINE_LOGS = (
    "for f in /var/log/juju/machine-*.log; do "
    'if [ -f "$f" ]; then echo "===== $f ====="; tail -n '
    f"{_REMOTE_TAIL_LINES} "
    '"$f"; fi; '
    "done"
)

_SKIP_INSTANCE_IDS = {"", "pending", "unknown", "none"}
_INSTANCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LXC_INSTANCE = re.compile(r"^juju-[A-Za-z0-9]+-\d+$")


class _Cmd:
    def __init__(self, argv: list[str], returncode: int | None, stdout: str, stderr: str):
        self.argv = argv
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def wait(juju, *args, diag_name: str | None = None, **kwargs):
    """Call ``juju.wait`` and dump diagnostics if it times out.

    The ``TimeoutError`` object raised by jubilant is re-raised unchanged.
    ``diag_name`` selects the artifact directory; otherwise the current pytest
    node id is used.
    """
    try:
        return juju.wait(*args, **kwargs)
    except TimeoutError:
        try:
            dest = dump_wait_diagnostics(juju, _label(diag_name))
            logger.warning("juju wait timed out; diagnostics written to %s", dest)
        except Exception:
            logger.exception("wait-timeout diagnostics failed")
        raise


def dump_wait_diagnostics(juju, name: str) -> pathlib.Path:
    """Collect status, debug-log, and per-machine LXD logs. Never raises."""
    try:
        dest = _destination(str(name))
    except Exception:
        logger.exception("could not choose a diagnostics directory for %s", name)
        return _diagnostics_root() / "wait"
    try:
        _collect(juju, dest)
    except Exception:
        logger.exception("wait-timeout diagnostics failed for %s", dest)
    return dest


def _label(diag_name: str | None) -> str:
    if diag_name:
        return diag_name
    return os.environ.get("PYTEST_CURRENT_TEST", "").strip() or "wait"


def _diagnostics_root() -> pathlib.Path:
    workspace = os.environ.get("GITHUB_WORKSPACE") or "."
    return pathlib.Path(workspace) / "artifacts" / "integration-diagnostics"


def _safe_component(name: str) -> str:
    text = name.strip().split(" (", 1)[0].strip()
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    text = text.replace("::", "-")
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-.")
    if not text or text in {".", ".."}:
        return "wait"
    return text


def _destination(name: str) -> pathlib.Path:
    root = _diagnostics_root()
    base = _safe_component(name)
    path = root / base
    suffix = 2
    while _marker_exists(path) and suffix < 1000:
        path = root / f"{base}-{suffix}"
        suffix += 1
    return path


def _marker_exists(path: pathlib.Path) -> bool:
    try:
        return (path / _STATUS_NAME).is_file()
    except OSError:
        return False


def _collect(juju, dest: pathlib.Path) -> None:
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("could not create %s", dest)
        return

    binary = _binary(juju)
    model = _model(juju)

    status = _run(_juju_argv(binary, "status", ["--format=yaml"], model))
    _write(dest / _STATUS_NAME, status)

    debug = _run(_debug_log_argv(binary, model))
    _write(dest / "juju-debug-log.txt", debug)

    listing = _run(["lxc", "list"])
    _write(dest / "lxc-list.txt", listing)

    instances = _instances_from_status(status.stdout) or _instances_from_lxc(listing.stdout)
    for instance in instances:
        _dump_instance(dest, instance)


def _binary(juju) -> str:
    binary = getattr(juju, "cli_binary", None) or "juju"
    return str(binary)


def _model(juju) -> str | None:
    model = getattr(juju, "model", None)
    if model is None:
        return None
    text = str(model).strip()
    return text or None


def _juju_argv(binary: str, command: str, args: list[str], model: str | None) -> list[str]:
    argv = [binary, command, *args]
    if model is not None:
        argv.extend(["-m", model])
    return argv


def _debug_log_argv(binary: str, model: str | None) -> list[str]:
    argv = [binary, "debug-log"]
    if model is not None:
        argv.extend(["-m", model])
    argv.extend(["--limit", _DEBUG_LOG_LIMIT])
    return argv


def _dump_instance(dest: pathlib.Path, instance: str) -> None:
    if _INSTANCE_ID.fullmatch(instance) is None:
        return
    inst_dir = dest / instance
    cloud = _run(
        [
            "lxc",
            "exec",
            instance,
            "--",
            "tail",
            "-n",
            _REMOTE_TAIL_LINES,
            "/var/log/cloud-init-output.log",
        ]
    )
    _write(inst_dir / "cloud-init-output.log", cloud)
    logs = _run(["lxc", "exec", instance, "--", "sh", "-c", _MACHINE_LOGS])
    _write(inst_dir / "juju-machine-logs.txt", logs)


def _instances_from_status(text: str) -> list[str]:
    try:
        data = yaml.safe_load(text) if text.strip() else None
    except Exception:
        logger.exception("could not parse juju status")
        return []
    if not isinstance(data, dict):
        return []
    machines = data.get("machines")
    if not isinstance(machines, dict):
        return []
    found: list[str] = []
    for machine in machines.values():
        if not isinstance(machine, dict):
            continue
        raw = machine.get("instance-id")
        if not isinstance(raw, str):
            continue
        inst = raw.strip()
        if inst.lower() in _SKIP_INSTANCE_IDS or _INSTANCE_ID.fullmatch(inst) is None:
            continue
        if inst not in found:
            found.append(inst)
    return found


def _instances_from_lxc(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        name = cells[0]
        if _LXC_INSTANCE.fullmatch(name) and name not in found:
            found.append(name)
    return found


def _run(argv: list[str]) -> _Cmd:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = _as_text(exc.stderr)
        note = f"command timed out after {COMMAND_TIMEOUT_SECONDS}s"
        stderr = f"{stderr}\n{note}" if stderr else note
        return _Cmd(list(argv), None, _as_text(exc.stdout), stderr)
    except Exception as exc:
        return _Cmd(list(argv), None, "", f"{type(exc).__name__}: {exc}")
    return _Cmd(list(argv), completed.returncode, completed.stdout or "", completed.stderr or "")


def _as_text(data: object) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return str(data)


def _render(cmd: _Cmd, stdout: str) -> str:
    exit_text = "none" if cmd.returncode is None else str(cmd.returncode)
    parts = [f"$ {shlex.join(cmd.argv)}", f"exit={exit_text}"]
    if cmd.stderr:
        parts.append("--- stderr ---")
        parts.append(cmd.stderr.rstrip("\n"))
    parts.append("--- stdout ---")
    return "\n".join(parts) + "\n" + stdout


def _write(path: pathlib.Path, cmd: _Cmd, stdout: str | None = None) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = cmd.stdout if stdout is None else stdout
        path.write_text(_render(cmd, body), encoding="utf-8")
    except Exception:
        logger.exception("failed to write %s", path)
