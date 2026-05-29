#!/usr/bin/env python3
"""Norma machine calibration charm — exercises Juju machine charm features.

Machine substrate: NO Pebble, NO containers, NO OCI image. The Norma Go binary
is delivered as a charm file resource and supervised on the host by a
charm-managed systemd unit, driven through the ops-free ``WorkloadDriver`` seam.

Follows the holistic reconciler architecture (Constitution I): all lifecycle
events route to a single ``_reconcile()``. Dedicated handlers exist only for
stop/remove, secret rotation/expiration, and actions.

P0 is the scaffold: the reconciler validates config and drives the workload
through the driver; feature logic (relations, secrets, storage, COS, …) lands
in later phases.
"""

import json
import logging
import re

import ops

import norma
from workload_driver import SystemdDriver, WorkloadError

logger = logging.getLogger(__name__)


def _event_to_kebab(event: ops.EventBase) -> str:
    """Convert an event class name to kebab-case for the event ledger."""
    name = type(event).__name__
    if name.endswith("Event"):
        name = name[: -len("Event")]
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name).lower()


class NormaCharm(ops.CharmBase):
    """Main charm class for the juju-norma machine calibration charm."""

    def __init__(self, framework: ops.Framework) -> None:
        super().__init__(framework)

        # Workload control surface (machine substrate). Depends only on the
        # ops-free WorkloadDriver protocol; SystemdDriver is the machine impl.
        self.driver = SystemdDriver()

        # Event ledger, persisted to the host filesystem.
        self._event_ledger: list[dict] = norma.read_event_ledger()

        # Forced status from the set-status action (cleared on next reconcile).
        self._forced_status: ops.StatusBase | None = None

        # --- Lifecycle events → _reconcile (holistic reconciler) ---
        for evt in (
            self.on.install,
            self.on.start,
            self.on.config_changed,
            self.on.upgrade_charm,
            self.on.update_status,
            self.on.leader_elected,
        ):
            self.framework.observe(evt, self._reconcile)

        # --- Storage events → _reconcile ---
        if "data" in self.meta.storages:
            self.framework.observe(self.on.data_storage_attached, self._reconcile)
            self.framework.observe(self.on.data_storage_detaching, self._reconcile)

        # --- Relation events (peers + calibration) → _reconcile ---
        for endpoint in ("norma_peers", "calibration_provider", "calibration_requirer"):
            for suffix in (
                "relation_created",
                "relation_joined",
                "relation_changed",
                "relation_departed",
                "relation_broken",
            ):
                event = getattr(self.on, f"{endpoint}_{suffix}", None)
                if event is not None:
                    self.framework.observe(event, self._reconcile)

        # --- Dedicated handlers (permitted by Constitution I) ---
        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(self.on.remove, self._on_remove)

        # --- Status collection ---
        self.framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)
        self.framework.observe(self.on.collect_app_status, self._on_collect_app_status)

        # --- Actions ---
        self.framework.observe(self.on.get_event_log_action, self._on_get_event_log_action)
        self.framework.observe(self.on.get_config_action, self._on_get_config_action)
        self.framework.observe(self.on.get_version_action, self._on_get_version_action)
        self.framework.observe(self.on.set_status_action, self._on_set_status_action)
        self.framework.observe(self.on.fail_action_action, self._on_fail_action)

    # ------------------------------------------------------------------ #
    #  Core reconciler                                                    #
    # ------------------------------------------------------------------ #

    def _reconcile(self, event: ops.EventBase) -> None:
        """Holistic reconciler — single entry point for all lifecycle events.

        Reads current model state, computes desired state, writes outputs
        (systemd unit + status). MUST be idempotent and MUST NOT call
        ``event.defer()``.
        """
        self._log_event(_event_to_kebab(event))

        config = self._config_dict()
        valid, error_msg = norma.validate_config(config)
        if not valid:
            self._forced_status = ops.BlockedStatus(error_msg)
            return
        self._forced_status = None

        # F2: lay the workload binary down from the attached file resource.
        # Idempotent — no-op once present; re-runs cover attach-resource/refresh.
        self._ensure_workload_binary()

        # Workload application is gated on the binary being delivered. Until
        # the file resource is attached + laid down, there is nothing to
        # supervise — collect_unit_status reports Waiting.
        if not self.driver.is_ready():
            return

        version = self._get_charm_version()
        port = int(self.config.get("calibration-int", norma.DEFAULT_PORT))
        try:
            self.driver.apply(port=port, version=version, env={})
        except WorkloadError:
            logger.exception("Workload apply failed; will retry on next event")
            return

        self.unit.set_workload_version(version)
        self.unit.open_port("tcp", port)

    # ------------------------------------------------------------------ #
    #  Dedicated handlers                                                 #
    # ------------------------------------------------------------------ #

    def _on_stop(self, event: ops.StopEvent) -> None:
        """Stop and disable the workload service (idempotent teardown)."""
        self._log_event("stop")
        try:
            self.driver.stop()
        except WorkloadError:
            logger.exception("Workload stop failed during teardown")

    def _on_remove(self, event: ops.RemoveEvent) -> None:
        """Remove the unit file and reload systemd (idempotent teardown)."""
        self._log_event("remove")
        try:
            self.driver.teardown()
        except WorkloadError:
            logger.exception("Workload teardown failed during remove")

    # ------------------------------------------------------------------ #
    #  Status collection                                                  #
    # ------------------------------------------------------------------ #

    def _on_collect_unit_status(self, event: ops.CollectStatusEvent) -> None:
        if self._forced_status is not None:
            event.add_status(self._forced_status)
            return

        if not self.driver.is_ready():
            event.add_status(ops.WaitingStatus("Waiting for workload binary"))
            return

        try:
            running = self.driver.service_running()
        except WorkloadError:
            running = False
        if not running:
            event.add_status(ops.MaintenanceStatus("Starting workload"))
            return

        event.add_status(ops.ActiveStatus())

    def _on_collect_app_status(self, event: ops.CollectStatusEvent) -> None:
        if not self.unit.is_leader():
            return
        if self._forced_status is not None:
            event.add_status(self._forced_status)
            return
        event.add_status(ops.ActiveStatus())

    # ------------------------------------------------------------------ #
    #  Actions                                                            #
    # ------------------------------------------------------------------ #

    def _on_get_event_log_action(self, event: ops.ActionEvent) -> None:
        """Return the event ledger, optionally filtered."""
        event.log("Retrieving event ledger")
        limit = event.params.get("limit", 0)
        event_filter = event.params.get("event-filter", "")

        entries = self._event_ledger
        if event_filter:
            entries = [e for e in entries if event_filter in e["event_name"]]
        if limit > 0:
            entries = entries[-limit:]

        event.set_results(
            {
                "events": json.dumps(entries),
                "count": str(len(entries)),
                "unit": self.unit.name,
            }
        )

    def _on_get_config_action(self, event: ops.ActionEvent) -> None:
        """Return all current configuration values."""
        event.log("Retrieving configuration")
        event.set_results(
            {
                "calibration-string": str(self.config.get("calibration-string", "default")),
                "calibration-int": str(self.config.get("calibration-int", norma.DEFAULT_PORT)),
                "calibration-float": str(self.config.get("calibration-float", 1.0)),
                "calibration-bool": str(self.config.get("calibration-bool", True)),
                "calibration-secret": (
                    "set" if self.config.get("calibration-secret") else "unset"
                ),
            }
        )

    def _on_get_version_action(self, event: ops.ActionEvent) -> None:
        """Return charm and workload version information."""
        event.log("Retrieving version info")
        workload_version = self.driver.workload_version() if self.driver.is_ready() else ""
        event.set_results(
            {
                "charm-version": self._get_charm_version(),
                "workload-version": workload_version or "unavailable",
            }
        )

    def _on_set_status_action(self, event: ops.ActionEvent) -> None:
        """Force a specific status condition for testing."""
        event.log("Setting forced status")
        status_map: dict[str, type[ops.StatusBase]] = {
            "active": ops.ActiveStatus,
            "blocked": ops.BlockedStatus,
            "waiting": ops.WaitingStatus,
            "maintenance": ops.MaintenanceStatus,
        }
        status_name = event.params.get("status", "")
        message = event.params.get("message", "")
        status_cls = status_map.get(status_name)
        if status_cls is None:
            event.fail(f"Unknown status type: {status_name}")
            return

        previous = type(self._forced_status).__name__ if self._forced_status else "none"
        if status_cls is ops.ActiveStatus:
            self._forced_status = None
        else:
            self._forced_status = status_cls(message)

        event.set_results({"previous-status": previous, "new-status": status_name})

    def _on_fail_action(self, event: ops.ActionEvent) -> None:
        """Intentionally fail to test error reporting."""
        message = event.params.get("message", "Intentional failure for testing")
        event.log(f"Failing action with message: {message}")
        self._log_event("fail-action", {"message": message})
        event.fail(message)

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    def _ensure_workload_binary(self) -> None:
        """Fetch the norma-bin file resource and lay it down on the host (F2).

        The charm side does the ops-only work (resource-get); the driver does
        the host I/O. A not-yet-attached or empty resource leaves the binary
        absent, so the reconciler reports Waiting until it arrives.
        """
        if self.driver.is_ready():
            return
        try:
            path = self.model.resources.fetch("norma-bin")
        except (ops.ModelError, NameError):
            return  # resource not attached yet
        if not path or not path.exists() or path.stat().st_size == 0:
            return  # placeholder / empty resource
        try:
            self.driver.install_binary(str(path))
        except WorkloadError:
            logger.exception("Failed to install workload binary")

    def _config_dict(self) -> dict:
        """Extract config primitives (dash→underscore) for the ops-free module."""
        return {
            "calibration_string": self.config.get("calibration-string", "default"),
            "calibration_int": int(self.config.get("calibration-int", norma.DEFAULT_PORT)),
            "calibration_float": float(self.config.get("calibration-float", 1.0)),
            "calibration_bool": self.config.get("calibration-bool", True),
        }

    def _log_event(self, event_name: str, extra: dict[str, str] | None = None) -> None:
        """Append an event to the ledger and persist to disk."""
        self._event_ledger.append(
            {
                "timestamp": datetime_now_iso(),
                "event_name": event_name,
                "unit_name": self.unit.name,
                "extra": extra or {},
            }
        )
        norma.write_event_ledger(self._event_ledger)
        logger.info("Event: %s on %s", event_name, self.unit.name)

    def _get_charm_version(self) -> str:
        """Read the charm version from the file written by charmcraft."""
        try:
            return (self.charm_dir / "version").read_text().strip()
        except FileNotFoundError:
            return "dev"


def datetime_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (3.10-safe)."""
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


if __name__ == "__main__":  # pragma: nocover
    ops.main(NormaCharm)
