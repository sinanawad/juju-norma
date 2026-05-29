"""Workload driver seam for the juju-norma machine charm.

This module is the reuse boundary between the charm and the substrate. It is
ops-free: it speaks only in primitives (port, version, env, paths) so the charm
can drive the workload without knowing whether it sits on systemd (machine) or
Pebble (k8s).

NET-NEW (SYNTHESIS §6 M2): the ``WorkloadDriver`` protocol does NOT exist in the
k8s sibling — the sibling calls Pebble directly. This charm defines the seam and
implements ``SystemdDriver``; a future ``PebbleDriver`` in the sibling is
hypothetical. ~80-85% of the sibling's *logic* is reusable behind this seam, but
the seam itself is new abstraction work.
"""

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol, runtime_checkable

import norma


class WorkloadError(Exception):
    """Raised when a host workload operation fails.

    The charm catches this in the reconciler and surfaces it via unit status
    rather than letting the hook crash (Constitution VII: no ErrorStatus for
    recoverable issues).
    """


@runtime_checkable
class WorkloadDriver(Protocol):
    """Substrate-neutral workload control surface.

    The machine charm implements this with systemd + filesystem + subprocess;
    the k8s sibling would implement it with Pebble. The charm depends only on
    this protocol, never on the concrete driver.
    """

    def is_ready(self) -> bool:
        """Whether the workload can be managed (binary delivered)."""
        ...

    def install_binary(self, source: str) -> None:
        """Lay the workload binary down on the host (idempotent)."""
        ...

    def apply(self, *, port: int, version: str, env: dict[str, str]) -> None:
        """Render + install the service definition and (re)start the workload."""
        ...

    def service_running(self) -> bool:
        """Whether the workload service is currently active."""
        ...

    def restart(self) -> None:
        """Restart the workload service."""
        ...

    def stop(self) -> None:
        """Stop and disable the workload service (idempotent)."""
        ...

    def teardown(self) -> None:
        """Stop the service and remove the unit file (idempotent)."""
        ...

    def workload_version(self, port: int = norma.DEFAULT_PORT) -> str:
        """Best-effort workload version, or "" if unavailable."""
        ...

    def exec_check(self) -> None:
        """Run the workload's --check self-probe; raise WorkloadError on failure."""
        ...


class SystemdDriver:
    """Drive the Norma workload via a charm-managed systemd unit.

    All host operations are wrapped defensively and re-raised as
    :class:`WorkloadError` (Constitution Tech Stack: "Wrap host operations
    defensively; surface failures via status").
    """

    def __init__(
        self,
        service_name: str = norma.SERVICE_NAME,
        binary_path: str = norma.BINARY_PATH,
        unit_path: str = norma.SYSTEMD_UNIT_PATH,
    ) -> None:
        self.service_name = service_name
        self.binary_path = binary_path
        self.unit_path = unit_path

    # -- readiness -----------------------------------------------------------

    def is_ready(self) -> bool:
        """The workload is manageable once its binary has been laid down.

        On a fresh unit (and in unit tests) the binary is absent, so this
        returns False and the reconciler skips workload application — keeping
        unit tests free of host side effects.
        """
        return Path(self.binary_path).exists()

    def install_binary(self, source: str) -> None:
        """Copy the fetched file resource to the host binary path, executable.

        This is F2 (workload via file resource): the charm fetches the
        ``norma-bin`` resource with ``resource-get`` and hands us the path;
        we lay it down at ``self.binary_path``. Idempotent — safe to re-run on
        every reconcile and on ``juju refresh``/``attach-resource``.
        """
        try:
            dest = Path(self.binary_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            dest.chmod(0o755)
        except OSError as e:
            raise WorkloadError(f"failed to install binary to {self.binary_path}: {e}") from e

    # -- lifecycle -----------------------------------------------------------

    def apply(self, *, port: int, version: str, env: dict[str, str]) -> None:
        """Idempotently converge the unit file + running service.

        Like Pebble's replan, this is a no-op when nothing changed: it only
        rewrites + reloads + restarts when the unit text differs, and otherwise
        merely starts the service if it is not already active. This avoids
        restart storms during the install hook burst (install/start/
        config-changed/leader-elected all reconcile within seconds), which
        otherwise trips systemd's start-limit and wedges the unit as failed.
        """
        desired = norma.build_systemd_unit(port, version, env)
        unit = Path(self.unit_path)
        try:
            changed = not unit.exists() or unit.read_text() != desired
            if changed:
                unit.write_text(desired)
        except OSError as e:
            raise WorkloadError(f"failed to write unit file {self.unit_path}: {e}") from e

        if changed:
            self._systemctl("daemon-reload")
        self._systemctl("enable", self.service_name)

        if changed:
            # A real config change: clear any prior start-limit so the new unit
            # can start cleanly, then (re)start it.
            self._systemctl("reset-failed", self.service_name, check=False)
            self._systemctl("restart", self.service_name)
        elif self.service_state() == "inactive":
            # Unchanged unit that has settled to inactive (e.g. clean stop) — try
            # to bring it back, but DO NOT reset-failed: a genuinely crash-looping
            # workload must be allowed to latch into `failed` so it surfaces as
            # Blocked rather than being masked as perpetual "Maintenance" (M2).
            self._systemctl("restart", self.service_name)

    def service_running(self) -> bool:
        return self._systemctl("is-active", "--quiet", self.service_name, check=False) == 0

    def service_state(self) -> str:
        """The systemd ActiveState word: active|inactive|failed|activating|...

        Returns "unknown" if systemctl gives no parseable answer.
        """
        out = self._systemctl_output("is-active", self.service_name)
        return out.strip() or "unknown"

    def service_failed(self) -> bool:
        """True when the unit has latched into `failed` (e.g. crash-loop hit the
        start-limit) — the signal the charm surfaces as Blocked."""
        return self.service_state() == "failed"

    def restart(self) -> None:
        self._systemctl("restart", self.service_name)

    def stop(self) -> None:
        self._systemctl("stop", self.service_name, check=False)
        self._systemctl("disable", self.service_name, check=False)

    def teardown(self) -> None:
        self.stop()
        Path(self.unit_path).unlink(missing_ok=True)
        self._systemctl("daemon-reload", check=False)

    def workload_version(self, port: int = norma.DEFAULT_PORT) -> str:
        """Best-effort workload version via the running server's GET /version.

        Returns the JSON ``version`` field, or "" if the workload is not
        reachable (not started yet, wrong port, malformed response). Never
        raises — the caller treats "" as "unavailable".
        """
        try:
            with urllib.request.urlopen(  # localhost only
                f"http://localhost:{port}/version", timeout=2
            ) as resp:
                return str(json.loads(resp.read().decode()).get("version", ""))
        except (urllib.error.URLError, OSError, ValueError):
            return ""

    def exec_check(self) -> None:
        """Run `<binary> --check` (the workload self-probe); raise on failure."""
        try:
            subprocess.run(
                [self.binary_path, "--check"], capture_output=True, text=True, check=True
            )
        except FileNotFoundError as e:
            raise WorkloadError(f"binary not found at {self.binary_path}") from e
        except subprocess.CalledProcessError as e:
            raise WorkloadError(f"workload --check failed: {e.stderr.strip()}") from e

    # -- internals -----------------------------------------------------------

    def _systemctl(self, *args: str, check: bool = True) -> int:
        try:
            result = subprocess.run(
                ["systemctl", *args],
                capture_output=True,
                text=True,
                check=check,
            )
        except FileNotFoundError as e:
            raise WorkloadError("systemctl not found on host") from e
        except subprocess.CalledProcessError as e:
            raise WorkloadError(f"systemctl {' '.join(args)} failed: {e.stderr.strip()}") from e
        return result.returncode

    def _systemctl_output(self, *args: str) -> str:
        """Run systemctl and return stdout (never raises; "" on error).

        Used for state queries like ``is-active`` where a non-zero exit (e.g.
        the unit is ``failed``/``inactive``) still prints the state word we want.
        """
        try:
            result = subprocess.run(
                ["systemctl", *args], capture_output=True, text=True, check=False
            )
        except FileNotFoundError as e:
            raise WorkloadError("systemctl not found on host") from e
        return result.stdout or ""
