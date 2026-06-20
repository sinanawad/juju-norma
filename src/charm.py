#!/usr/bin/env python3
"""Norma machine calibration charm — exercises Juju machine charm features.

Machine substrate: NO Pebble, NO containers, NO OCI image. The Norma Go binary
is delivered as a charm file resource and supervised on the host by a
charm-managed systemd unit, driven through the ops-free ``WorkloadDriver`` seam.

Holistic reconciler (Constitution I): all lifecycle/relation/storage events route
through ``_on_defer_gate`` (which quarantines the only ``event.defer()`` in the
charm) into a single ``_reconcile()``. Dedicated handlers exist only for
stop/remove (and, from P4, secret rotation/expiration) and actions.
"""

import json
import logging
import os
import re
import secrets

import ops
from charms.grafana_agent.v0.cos_agent import COSAgentProvider
from ops import hookcmds

import norma
from norma_common import badbehavior
from workload_driver import SystemdDriver, WorkloadError

logger = logging.getLogger(__name__)


def _event_to_kebab(event: ops.EventBase) -> str:
    """Convert an event class name to kebab-case for the event ledger."""
    name = type(event).__name__
    if name.endswith("Event"):
        name = name[: -len("Event")]
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name).lower()


# Machine introspect sections (k8s 'containers' collector dropped; 'systemd-service' added).
REPORT_SECTIONS = (
    "identity",
    "version",
    "leadership",
    "config",
    "event-ledger",
    "relations",
    "storage",
    "systemd-service",
    "secrets",
    "goal-state",
)

_STATUS_CLASSES: dict[str, type[ops.StatusBase]] = {
    "active": ops.ActiveStatus,
    "blocked": ops.BlockedStatus,
    "waiting": ops.WaitingStatus,
    "maintenance": ops.MaintenanceStatus,
}


class NormaCharm(ops.CharmBase):
    """Main charm class for the juju-norma machine calibration charm."""

    def __init__(self, framework: ops.Framework) -> None:
        super().__init__(framework)

        self.driver = SystemdDriver()
        self._event_ledger: list[dict] = norma.read_event_ledger()
        self._forced_status: ops.StatusBase | None = None
        self._defer_armed: bool = norma.read_defer_armed()

        # --- All lifecycle/relation/storage events → defer-gate → _reconcile ---
        gated = [
            self.on.install,
            self.on.start,
            self.on.config_changed,
            self.on.upgrade_charm,
            self.on.update_status,
            self.on.leader_elected,
            self.on.secret_changed,
        ]
        if "data" in self.meta.storages:
            gated += [self.on.data_storage_attached, self.on.data_storage_detaching]
        for endpoint in ("norma_peers", "calibration_provider", "calibration_requirer"):
            for suffix in (
                "relation_created",
                "relation_joined",
                "relation_changed",
                "relation_departed",
                "relation_broken",
            ):
                evt = getattr(self.on, f"{endpoint}_{suffix}", None)
                if evt is not None:
                    gated.append(evt)
        for evt in gated:
            self.framework.observe(evt, self._on_defer_gate)

        # --- Dedicated handlers (permitted by Constitution I) ---
        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(self.on.remove, self._on_remove)
        self.framework.observe(self.on.secret_rotate, self._on_secret_rotate)
        self.framework.observe(self.on.secret_expired, self._on_secret_expired)
        self.framework.observe(self.on.secret_remove, self._on_secret_remove)

        # --- Status collection ---
        self.framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)
        self.framework.observe(self.on.collect_app_status, self._on_collect_app_status)

        # --- Actions ---
        self.framework.observe(self.on.get_event_log_action, self._on_get_event_log_action)
        self.framework.observe(self.on.get_config_action, self._on_get_config_action)
        self.framework.observe(self.on.get_version_action, self._on_get_version_action)
        self.framework.observe(self.on.set_status_action, self._on_set_status_action)
        self.framework.observe(self.on.fail_action_action, self._on_fail_action)
        self.framework.observe(self.on.get_peer_data_action, self._on_get_peer_data_action)
        self.framework.observe(self.on.get_relation_data_action, self._on_get_relation_data_action)
        self.framework.observe(self.on.get_cluster_info_action, self._on_get_cluster_info_action)
        self.framework.observe(self.on.get_secret_info_action, self._on_get_secret_info_action)
        self.framework.observe(self.on.rotate_secret_action, self._on_rotate_secret_action)
        self.framework.observe(
            self.on.read_shared_secret_action, self._on_read_shared_secret_action
        )
        self.framework.observe(self.on.check_storage_action, self._on_check_storage_action)
        self.framework.observe(self.on.run_check_action, self._on_run_check_action)
        self.framework.observe(self.on.test_networking_action, self._on_test_networking_action)
        self.framework.observe(self.on.check_security_action, self._on_check_security_action)
        self.framework.observe(self.on.test_defer_action, self._on_test_defer_action)
        self.framework.observe(self.on.test_workload_ops_action, self._on_test_workload_ops_action)
        self.framework.observe(self.on.crash_workload_action, self._on_crash_workload_action)
        self.framework.observe(self.on.set_health_action, self._on_set_health_action)
        self.framework.observe(self.on.introspect_action, self._on_introspect_action)

        # --- COS observability (F16): machine PUSH model via cos-agent ---
        # ONE relation carries metrics jobs + dashboards + alert rules to a
        # grafana-agent subordinate (no k8s pull trio). Topology labels are
        # injected by the lib.
        if "cos-agent" in self.meta.relations:
            # Track the configured workload port (M1): if calibration-int moves,
            # the scrape target moves with it, instead of hardcoding 8080.
            workload_port = int(self.config.get("calibration-int", norma.DEFAULT_PORT))
            self._cos_agent = COSAgentProvider(
                self,
                metrics_endpoints=[{"path": "/metrics", "port": workload_port}],
                metrics_rules_dir="./src/prometheus_alert_rules",
                dashboard_dirs=["./src/grafana_dashboards"],
            )

    # ------------------------------------------------------------------ #
    #  Defer gate (F18) — the ONLY event.defer() in the charm            #
    # ------------------------------------------------------------------ #

    def _on_defer_gate(self, event: ops.EventBase) -> None:
        """Pre-reconcile deferral gate (F18).

        Quarantines ``event.defer()`` outside the reconciler (Constitution VII).
        When armed via the test-defer action, the next eligible event is
        deferred (and re-emitted later) instead of reconciling.
        """
        event_name = _event_to_kebab(event)
        if self._defer_armed and event_name not in norma.DEFER_SKIP_EVENTS:
            self._log_event(event_name, {"deferred": "true"})
            event.defer()
            self._defer_armed = False
            norma.write_defer_armed(False)
            # Record which event was deferred so its re-emission can be tagged
            # (ops clears event.deferred before re-running the handler, so the
            # event object can't reveal the re-emission — F18).
            norma.write_pending_reemit(event_name)
            return
        self._reconcile(event)

    # ------------------------------------------------------------------ #
    #  Core reconciler                                                    #
    # ------------------------------------------------------------------ #

    def _reconcile(self, event: ops.EventBase) -> None:
        """Holistic reconciler — idempotent; MUST NOT call event.defer()."""
        event_name = _event_to_kebab(event)
        extra: dict[str, str] = {}
        # Tag the re-emission of a previously-deferred event (F18). We match the
        # persisted pending-reemit name rather than event.deferred (which ops has
        # already reset to False by the time the re-emitted handler runs).
        if norma.read_pending_reemit() == event_name:
            extra["re-emitted"] = "true"
            norma.write_pending_reemit("")
        if isinstance(event, ops.RelationDepartedEvent) and event.departing_unit:
            extra["departing-unit"] = event.departing_unit.name
        if isinstance(event, ops.RelationEvent) and event.app:
            extra["remote-app"] = event.app.name
        self._log_event(event_name, extra)

        # Bad-behavior test-bed: fault injection (logged above first).
        self._maybe_trigger_hook_error()
        self._maybe_trigger_stuck_dying(event)

        broken_relation: ops.Relation | None = None
        if (
            isinstance(event, ops.RelationBrokenEvent)
            and event.relation.name == "calibration-provider"
        ):
            broken_relation = event.relation

        # Relation data + storage marker are independent of workload readiness.
        self._update_relation_data(broken_relation=broken_relation)
        self._write_storage_marker()

        # Config validation (F3/F4).
        valid, error_msg = norma.validate_config(self._config_dict())
        if not valid:
            self._forced_status = ops.BlockedStatus(error_msg)
            return

        # Secret-type config resolution (F3). NOTE: this calibrates secret-URI
        # *resolution/access*, not delivery — the resolved content is
        # intentionally not passed to the workload (apply uses env={}). The point
        # is to exercise that the charm can read a secret-typed config option.
        secret_uri = self.config.get("calibration-secret")
        if secret_uri:
            try:
                self.model.get_secret(id=secret_uri).get_content(refresh=True)
            except ops.SecretNotFoundError:
                self._forced_status = ops.BlockedStatus(f"Secret not found: {secret_uri}")
                return
            except ops.ModelError as e:
                self._forced_status = ops.BlockedStatus(f"Secret error: {e}")
                return

        self._forced_status = None

        # Workload convergence (F1/F2).
        self._ensure_workload_binary()
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
        # Reconcile the opened-port set (M3): open the configured port and close
        # any stale TCP port left over from a previous calibration-int value, so
        # reconfiguring the port converges cleanly instead of accumulating opens.
        for opened in self.unit.opened_ports():
            if opened.protocol == "tcp" and opened.port != port:
                self.unit.close_port("tcp", opened.port)
        self.unit.open_port("tcp", port)

    # ------------------------------------------------------------------ #
    #  Dedicated handlers                                                 #
    # ------------------------------------------------------------------ #

    def _on_stop(self, event: ops.StopEvent) -> None:
        self._log_event("stop")
        self._maybe_trigger_stuck_dying(event)
        try:
            self.driver.stop()
        except WorkloadError:
            logger.exception("Workload stop failed during teardown")

    def _on_remove(self, event: ops.RemoveEvent) -> None:
        self._log_event("remove")
        self._maybe_trigger_stuck_dying(event)
        try:
            self.driver.teardown()
        except WorkloadError:
            logger.exception("Workload teardown failed during remove")

    def _on_secret_rotate(self, event: ops.SecretRotateEvent) -> None:
        """F10: rotate by writing a fresh revision."""
        self._log_event("secret-rotate", {"secret-label": event.secret.label or ""})
        event.secret.set_content({"password": secrets.token_urlsafe(24)})

    def _on_secret_expired(self, event: ops.SecretExpiredEvent) -> None:
        """F10: drop the expired (superseded) revision; never crash the hook."""
        outcome = self._drop_revision(event.secret, event.revision)
        self._log_event(
            "secret-expired",
            {
                "secret-label": event.secret.label or "",
                "revision": str(event.revision),
                "outcome": outcome,
            },
        )

    def _on_secret_remove(self, event: ops.SecretRemoveEvent) -> None:
        """F10: drop an obsolete revision; never crash the hook."""
        outcome = self._drop_revision(event.secret, event.revision)
        self._log_event(
            "secret-remove",
            {
                "secret-label": event.secret.label or "",
                "revision": str(event.revision),
                "outcome": outcome,
            },
        )

    @staticmethod
    def _drop_revision(secret: ops.Secret, revision: int) -> str:
        # ops refuses to remove the latest/only revision (ValueError); in the
        # normal rotate→expire flow the revision is superseded so this succeeds.
        # Guard so an edge-case expire on a sole revision can't wedge teardown —
        # but return a forensic outcome ("dropped" | "skipped:<Class>: <msg>")
        # for the event ledger, so an engine misfire (e.g. remove fired for a
        # live revision) stays attributable instead of vanishing into a warning.
        try:
            secret.remove_revision(revision)
        except (ops.ModelError, ValueError) as exc:
            logger.warning("Skipped removing secret revision %s: %s", revision, exc)
            return f"skipped:{type(exc).__name__}: {exc}"
        return "dropped"

    # ------------------------------------------------------------------ #
    #  Status collection (F4)                                             #
    # ------------------------------------------------------------------ #

    def _on_collect_unit_status(self, event: ops.CollectStatusEvent) -> None:
        if self._forced_status is not None:
            event.add_status(self._forced_status)
            return
        if not self.driver.is_ready():
            event.add_status(ops.WaitingStatus("Waiting for workload binary"))
            return
        bad = self._bad_behavior_status()
        if bad is not None:
            event.add_status(bad)
            return
        try:
            running = self.driver.service_running()
            # A crash-looping workload latches into systemd `failed` (the apply()
            # path no longer masks it with reset-failed). Surface that as Blocked
            # so a permanently-broken workload is reported, not hidden behind an
            # endless "Starting workload" maintenance message (M2).
            failed = self.driver.service_failed() if not running else False
        except WorkloadError:
            running, failed = False, False
        if failed:
            event.add_status(ops.BlockedStatus("Workload failed to start"))
            return
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
    #  Bad-behavior test-bed (F20)                                        #
    # ------------------------------------------------------------------ #

    def _bad_mode(self) -> str:
        return badbehavior.normalize_mode(self.config.get("bad-behavior-mode", "none"))

    def _bad_behavior_status(self) -> ops.StatusBase | None:
        port = int(self.config.get("calibration-int", norma.DEFAULT_PORT))
        result = badbehavior.status_for_mode(self._bad_mode(), port, len(self._event_ledger))
        if result is None:
            return None
        kind, message = result
        cls = _STATUS_CLASSES[kind]
        return cls(message) if message else cls()

    def _maybe_trigger_hook_error(self) -> None:
        if self._bad_mode() == "hook-error":
            raise RuntimeError("bad-behavior-mode=hook-error: simulated uncaught exception")

    def _maybe_trigger_stuck_dying(self, event: ops.EventBase) -> None:
        if self._bad_mode() != "stuck-dying":
            return
        if isinstance(
            event,
            (ops.StopEvent, ops.RemoveEvent, ops.RelationBrokenEvent, ops.RelationDepartedEvent),
        ):
            raise RuntimeError("bad-behavior-mode=stuck-dying: simulated teardown failure")

    def _inject_bad_relation_data(self, broken_relation: ops.Relation | None) -> None:
        if self._bad_mode() != "secret-in-relation" or not self.unit.is_leader():
            return
        for rel in self.model.relations.get("calibration-provider", []):
            if rel == broken_relation:
                continue
            rel.data[self.app]["password"] = "hunter2"  # deliberate test-bed leak
            rel.data[self.app]["api-key"] = "AKIAIOSFODNN7EXAMPLE"

    # ------------------------------------------------------------------ #
    #  Actions                                                            #
    # ------------------------------------------------------------------ #

    def _on_get_event_log_action(self, event: ops.ActionEvent) -> None:
        event.log("Retrieving event ledger")
        limit = event.params.get("limit", 0)
        event_filter = event.params.get("event-filter", "")
        entries = self._event_ledger
        if event_filter:
            entries = [e for e in entries if event_filter in e["event_name"]]
        if limit > 0:
            entries = entries[-limit:]
        event.set_results(
            {"events": json.dumps(entries), "count": str(len(entries)), "unit": self.unit.name}
        )

    def _on_get_config_action(self, event: ops.ActionEvent) -> None:
        event.log("Retrieving configuration")
        # String-coerce the single typed config view (M4: one source of defaults).
        event.set_results({k: str(v) for k, v in self._collect_config().items()})

    def _on_get_version_action(self, event: ops.ActionEvent) -> None:
        event.log("Retrieving version info")
        port = int(self.config.get("calibration-int", norma.DEFAULT_PORT))
        workload_version = self.driver.workload_version(port) if self.driver.is_ready() else ""
        event.set_results(
            {
                "charm-version": self._get_charm_version(),
                "workload-version": workload_version or "unavailable",
            }
        )

    def _on_set_status_action(self, event: ops.ActionEvent) -> None:
        """Force a unit status for the F4 calibration check.

        NOTE: the forced status is request-scoped — it is held on the charm
        instance and reported by collect_unit_status in the SAME dispatch, then
        forgotten. The next reconcile recomputes status from real state. This is
        intentional (no StoredState); it is not meant to persist across hooks.
        """
        event.log("Setting forced status")
        status_name = event.params.get("status", "")
        message = event.params.get("message", "")
        status_cls = _STATUS_CLASSES.get(status_name)
        if status_cls is None:
            event.fail(f"Unknown status type: {status_name}")
            return
        previous = type(self._forced_status).__name__ if self._forced_status else "none"
        self._forced_status = None if status_cls is ops.ActiveStatus else status_cls(message)
        event.set_results({"previous-status": previous, "new-status": status_name})

    def _on_fail_action(self, event: ops.ActionEvent) -> None:
        message = event.params.get("message", "Intentional failure for testing")
        event.log(f"Failing action with message: {message}")
        self._log_event("fail-action", {"message": message})
        event.fail(message)

    def _on_get_peer_data_action(self, event: ops.ActionEvent) -> None:
        event.log("Retrieving peer relation data")
        peer = self.model.get_relation("norma-peers")
        if not peer:
            event.set_results({"app-data": "{}", "unit-data": "{}"})
            return
        unit_data = {self.unit.name: dict(peer.data[self.unit])}
        for unit in peer.units:
            unit_data[unit.name] = dict(peer.data[unit])
        event.set_results(
            {"app-data": json.dumps(dict(peer.data[self.app])), "unit-data": json.dumps(unit_data)}
        )

    def _on_get_relation_data_action(self, event: ops.ActionEvent) -> None:
        """Return relation data for an endpoint: our app databag + all unit databags (F7/F8)."""
        event.log("Retrieving relation data")
        endpoint = event.params.get("endpoint", "")
        rel_id_filter = event.params.get("relation-id")
        relations = self.model.relations.get(endpoint, [])
        if rel_id_filter is not None:
            relations = [r for r in relations if r.id == rel_id_filter]
        result = []
        for rel in relations:
            app_data = dict(rel.data[self.app]) if self.unit.is_leader() else {}
            units = {self.unit.name: dict(rel.data[self.unit])}
            for unit in rel.units:
                units[unit.name] = dict(rel.data[unit])
            result.append({"id": rel.id, "app-data": app_data, "units": units})
        event.set_results({"relations": json.dumps(result)})

    def _on_get_cluster_info_action(self, event: ops.ActionEvent) -> None:
        event.log("Retrieving cluster info")
        peer = self.model.get_relation("norma-peers")
        all_units = [self.unit.name]
        if peer:
            all_units.extend(sorted(u.name for u in peer.units))
        leader_unit = self.unit.name if self.unit.is_leader() else ""
        if peer and not self.unit.is_leader():
            leader_unit = peer.data[self.app].get("leader-unit", "")
        event.set_results(
            {
                "unit-count": str(len(all_units)),
                "planned-units": str(self.app.planned_units()),
                "leader": leader_unit,
                "is-leader": str(self.unit.is_leader()),
                "units": json.dumps(all_units),
            }
        )

    def _on_get_secret_info_action(self, event: ops.ActionEvent) -> None:
        """F10: report the app-owned calibration secret (never the value)."""
        event.log("Retrieving secret info")
        peer = self.model.get_relation("norma-peers")
        empty = {
            "secret-id": "",
            "has-content": "false",
            "revision": "",
            "rotation": "",
            "error": "",
        }
        if not peer:
            event.set_results(empty)
            return
        secret_id = peer.data[self.app].get("secret-id", "")
        if not secret_id:
            event.set_results(empty)
            return
        try:
            secret = self.model.get_secret(id=secret_id)
            has_content = bool(secret.get_content(refresh=True).get("password"))
            # The latest revision number — increments on each rotation (F10).
            revision = str(secret.get_info().revision)
            error = ""
        except (ops.SecretNotFoundError, ops.ModelError) as exc:
            # Calibration forensics: surface the exception class + message
            # verbatim (e.g. Juju's "lease not held") instead of erasing the
            # signal — a has-content=false alone is undiagnosable.
            has_content = False
            revision = ""
            error = f"{type(exc).__name__}: {exc}"
        event.set_results(
            {
                "secret-id": secret_id,
                "has-content": str(has_content).lower(),
                "revision": revision,
                "rotation": "monthly",
                "error": error,
            }
        )

    def _on_rotate_secret_action(self, event: ops.ActionEvent) -> None:
        """F10: force an immediate rotation (new revision) of the app secret.

        Juju exposes no CLI to trigger an owner's rotate hook on demand, so this is
        the calibration lever for rotate-now. Leader-only (only the owner rotates).
        """
        if not self.unit.is_leader():
            event.fail("rotate-secret must run on the leader")
            return
        peer = self.model.get_relation("norma-peers")
        secret_id = peer.data[self.app].get("secret-id", "") if peer else ""
        if not secret_id:
            event.fail("no app secret to rotate")
            return
        try:
            self.model.get_secret(id=secret_id).set_content(
                {"password": secrets.token_urlsafe(24)}
            )
        except (ops.SecretNotFoundError, ops.ModelError) as exc:
            event.fail(f"rotate failed: {type(exc).__name__}: {exc}")
            return
        self._log_event("secret-rotate", {"trigger": "action"})
        # The new revision is committed at hook end; read it back via get-secret-info.
        event.set_results({"rotated": "true", "secret-id": secret_id})

    def _on_read_shared_secret_action(self, event: ops.ActionEvent) -> None:
        """F10 cross-app: read the app secret GRANTED to us over calibration-requirer.

        Demonstrates the grant end-to-end: the provider propagates the granted
        secret id into the relation app databag; here the requirer resolves it.
        """
        for rel in self.model.relations.get("calibration-requirer", []):
            if rel.app is None:
                continue
            shared_id = rel.data[rel.app].get("shared-secret-id", "")
            if not shared_id:
                continue
            try:
                content = self.model.get_secret(id=shared_id).get_content(refresh=True)
                event.set_results(
                    {
                        "secret-id": shared_id,
                        "readable": str(bool(content.get("password"))).lower(),
                        "error": "",
                    }
                )
            except (ops.SecretNotFoundError, ops.ModelError) as exc:
                event.set_results(
                    {
                        "secret-id": shared_id,
                        "readable": "false",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            return
        event.set_results({"secret-id": "", "readable": "false", "error": "no shared secret"})

    def _on_check_storage_action(self, event: ops.ActionEvent) -> None:
        """F11: report storage status. data=filesystem (marker/writable), blk=block device."""
        name = event.params.get("name", "data")
        if name not in ("data", "blk"):
            event.fail(f"Unknown storage name: {name}")
            return
        event.log(f"Checking storage '{name}'")
        storages = self.model.storages.get(name, [])
        results: dict[str, str] = {"name": name, "attached": str(bool(storages)).lower()}
        if not storages:
            results["mount-point"] = ""
            event.set_results(results)
            return
        location = str(storages[0].location)
        results["mount-point"] = location
        if name == "data":
            marker = os.path.join(location, norma.MARKER_FILE)
            results["marker-exists"] = str(os.path.exists(marker)).lower()
            if os.path.exists(marker):
                try:
                    with open(marker) as f:
                        results["marker-content"] = f.read()
                except OSError:
                    results["marker-content"] = ""
            results["writable"] = str(self._is_writable(location)).lower()
        else:
            results["is-block-device"] = str(_is_block_device(location)).lower()
        event.set_results(results)

    def _on_run_check_action(self, event: ops.ActionEvent) -> None:
        event.log("Running capability check")
        check = event.params.get("check", "")
        if check == "systemd":
            try:
                running = self.driver.service_running()
            except WorkloadError:
                running = False
            event.set_results(
                {
                    "check": "systemd",
                    "result": "pass" if running else "fail",
                    "details": "service active" if running else "service not active",
                }
            )
        elif check == "config":
            valid, msg = norma.validate_config(self._config_dict())
            event.set_results(
                {
                    "check": "config",
                    "result": "pass" if valid else "fail",
                    "details": msg or "valid",
                }
            )
        else:
            event.set_results(
                {"check": check, "result": "fail", "details": f"Unknown check: {check}"}
            )

    def _on_test_networking_action(self, event: ops.ActionEvent) -> None:
        event.log("Retrieving networking info")
        port_list = [f"{p.port}/{p.protocol}" for p in self.unit.opened_ports()]
        bindings: dict[str, dict[str, str]] = {}
        for endpoint in ("norma-peers", "calibration-provider", "calibration-requirer"):
            try:
                binding = self.model.get_binding(endpoint)
                if binding and binding.network:
                    bindings[endpoint] = {
                        "bind-address": str(binding.network.bind_address),
                        "ingress-address": str(binding.network.ingress_address),
                    }
            except ops.ModelError:
                bindings[endpoint] = {"error": "binding unavailable"}
        event.set_results(
            {"opened-ports": json.dumps(port_list), "bindings": json.dumps(bindings)}
        )

    def _on_check_security_action(self, event: ops.ActionEvent) -> None:
        event.log("Checking security posture")
        results: dict[str, str] = {
            "charm-uid": str(os.getuid()),
            "charm-gid": str(os.getgid()),
            "workload-user": "root",
            "substrate": "machine",
            "k8s-api-reachable": "n/a",
        }
        try:
            cloud_spec = self.model.get_cloud_spec()
            results["trust-available"] = "true"
            results["cloud-type"] = cloud_spec.type if cloud_spec else ""
        except ops.ModelError:
            results["trust-available"] = "false"
            results["cloud-type"] = ""
        event.set_results(results)

    def _on_test_defer_action(self, event: ops.ActionEvent) -> None:
        event.log("Configuring event deferral")
        previous = self._defer_armed
        self._defer_armed = event.params.get("arm", True)
        norma.write_defer_armed(self._defer_armed)
        event.set_results(
            {
                "deferral-armed": str(self._defer_armed).lower(),
                "previous-state": str(previous).lower(),
            }
        )

    def _on_crash_workload_action(self, event: ops.ActionEvent) -> None:
        """F1: SIGKILL the workload so systemd's Restart=on-failure brings it back.
        Calibrates machine workload supervision (the k8s sibling relies on Pebble)."""
        try:
            before = self.driver.restart_count()
            self.driver.kill()
        except WorkloadError as exc:
            event.fail(f"crash failed: {exc}")
            return
        self._log_event("crash-workload", {"restart-count-before": str(before)})
        event.set_results({"killed": "true", "restart-count-before": str(before)})

    def _on_set_health_action(self, event: ops.ActionEvent) -> None:
        """F5: toggle the workload health flag the Go binary's /health reads; driving
        it unhealthy makes the --check self-probe fail."""
        healthy = bool(event.params.get("healthy", True))
        try:
            self.driver.set_health(healthy)
        except WorkloadError as exc:
            event.fail(f"set-health failed: {exc}")
            return
        self._log_event("set-health", {"healthy": str(healthy).lower()})
        event.set_results({"healthy": str(healthy).lower()})

    def _on_test_workload_ops_action(self, event: ops.ActionEvent) -> None:
        """F5b: machine analogue of test-pebble-ops — systemd + file + subprocess suite."""
        event.log("Running workload-ops suite")
        import shutil
        import tempfile

        results: dict[str, str] = {}
        passed = 0
        total = 0

        def run_op(name: str, fn) -> None:
            nonlocal passed, total
            total += 1
            try:
                fn()
                results[name] = "pass"
                passed += 1
            except Exception as e:  # each op records its own failure
                results[name] = f"fail: {e}"

        tmpdir = tempfile.mkdtemp(prefix="norma-ops-")
        testfile = os.path.join(tmpdir, "ops.txt")

        def op_file_write() -> None:
            with open(testfile, "w") as f:
                f.write("norma-ops")

        def op_file_read() -> None:
            with open(testfile) as f:
                assert f.read() == "norma-ops"

        def op_file_exists() -> None:
            assert os.path.exists(testfile)

        def op_file_remove() -> None:
            os.remove(testfile)
            assert not os.path.exists(testfile)

        def op_service_status() -> None:
            self.driver.service_running()  # exercises systemctl is-active

        def op_service_restart() -> None:
            if not self.driver.is_ready():
                raise AssertionError("workload binary not delivered")
            self.driver.restart()

        def op_binary_check() -> None:
            if not self.driver.is_ready():
                raise AssertionError("workload binary not delivered")
            self.driver.exec_check()

        run_op("file-write", op_file_write)
        run_op("file-read", op_file_read)
        run_op("file-exists", op_file_exists)
        run_op("file-remove", op_file_remove)
        run_op("service-status", op_service_status)
        # binary-check (norma --check → HTTP GET /health) MUST run before
        # service-restart: the unit is Type=simple, so `systemctl restart` returns
        # the instant the process forks — before the server binds its port. Probing
        # the steady-state server first avoids racing the restart (which otherwise
        # intermittently fails the self-probe on a slow/loaded host, e.g. CI).
        run_op("binary-check", op_binary_check)
        run_op("service-restart", op_service_restart)
        shutil.rmtree(tmpdir, ignore_errors=True)

        results["summary"] = f"{passed}/{total} passed"
        event.set_results(results)

    # ------------------------------------------------------------------ #
    #  Introspect (F19) + collectors                                      #
    # ------------------------------------------------------------------ #

    def _on_introspect_action(self, event: ops.ActionEvent) -> None:
        event.log("Collecting introspection report")
        filter_str = event.params.get("sections", "")
        if filter_str:
            requested = {s.strip() for s in filter_str.split(",") if s.strip()}
            sections = [s for s in REPORT_SECTIONS if s in requested]
        else:
            sections = list(REPORT_SECTIONS)
        collectors = {
            "identity": self._collect_identity,
            "version": self._collect_version,
            "leadership": self._collect_leadership,
            "config": self._collect_config,
            "event-ledger": self._collect_event_ledger,
            "relations": self._collect_relations,
            "storage": self._collect_storage,
            "systemd-service": self._collect_systemd,
            "secrets": self._collect_secrets,
            "goal-state": self._collect_goal_state,
        }
        report: dict[str, str] = {"unit": self.unit.name}
        for section in sections:
            try:
                report[section] = json.dumps(collectors[section]())
            except Exception as e:  # introspection must never crash
                report[section] = json.dumps({"status": "unavailable", "reason": str(e)})
        event.set_results(report)

    def _collect_identity(self) -> dict:
        return {
            "unit-name": self.unit.name,
            "app-name": self.app.name,
            "model-name": self.model.name,
            "model-uuid": self.model.uuid,
            "charm-uid": os.getuid(),
            "charm-gid": os.getgid(),
        }

    def _collect_version(self) -> dict:
        return {"charm-version": self._get_charm_version()}

    def _collect_leadership(self) -> dict:
        return {"is-leader": self.unit.is_leader()}

    def _collect_config(self) -> dict:
        return {
            "calibration-string": self.config.get("calibration-string", "default"),
            "calibration-int": int(self.config.get("calibration-int", norma.DEFAULT_PORT)),
            "calibration-float": float(self.config.get("calibration-float", 1.0)),
            "calibration-bool": self.config.get("calibration-bool", True),
            "calibration-secret": "set" if self.config.get("calibration-secret") else "unset",
            "bad-behavior-mode": self._bad_mode(),
        }

    def _collect_event_ledger(self) -> dict:
        return {"count": len(self._event_ledger), "events": self._event_ledger[-50:]}

    def _collect_relations(self) -> dict:
        result: dict[str, list] = {}
        for endpoint, relations in self.model.relations.items():
            rel_list = [
                {
                    "id": rel.id,
                    "our-app-data": dict(rel.data.get(self.app, {})),
                    "our-unit-data": dict(rel.data.get(self.unit, {})),
                    "remote-units": sorted(u.name for u in rel.units),
                }
                for rel in relations
            ]
            if rel_list:
                result[endpoint] = rel_list
        return result

    def _collect_storage(self) -> dict:
        result: dict = {}
        for name in ("data", "blk"):
            storages = self.model.storages.get(name, [])
            info: dict = {"attached": bool(storages)}
            if storages:
                info["mount-point"] = str(storages[0].location)
            result[name] = info
        return result

    def _collect_systemd(self) -> dict:
        """Machine analogue of the k8s 'containers' collector."""
        try:
            running = self.driver.service_running()
        except WorkloadError:
            running = False
        return {
            "binary-present": self.driver.is_ready(),
            "service-running": running,
            "unit-file": os.path.exists(norma.SYSTEMD_UNIT_PATH),
            "restart-count": self.driver.restart_count(),
        }

    def _collect_secrets(self) -> dict:
        peer = self.model.get_relation("norma-peers")
        if not peer:
            return {"status": "no-peer-relation"}
        secret_id = peer.data[self.app].get("secret-id") if self.unit.is_leader() else None
        return {"secret-id": secret_id or "unavailable", "has-secret": bool(secret_id)}

    def _collect_goal_state(self) -> dict:
        # Public ops goal-state API (ops.hookcmds.goal_state, present since the
        # 3.x line). Returns a typed GoalState; we flatten it to the JSON-shaped
        # {units, relations} the introspect report emits — stringifying the
        # datetime `since` so the section stays json.dumps-able. Under
        # ops.testing/Scenario there is no goal-state backend, so we degrade to
        # "unavailable" rather than failing introspection.
        try:
            gs = hookcmds.goal_state()
        except Exception:  # introspection must never crash
            return {"status": "unavailable", "reason": "goal-state not accessible"}

        def _goal(g: hookcmds.Goal) -> dict:
            return {"status": g.status, "since": g.since.isoformat()}

        return {
            "units": {name: _goal(g) for name, g in gs.units.items()},
            "relations": {
                endpoint: {who: _goal(g) for who, g in goals.items()}
                for endpoint, goals in gs.relations.items()
            },
        }

    # ------------------------------------------------------------------ #
    #  Workload + relation-data helpers                                   #
    # ------------------------------------------------------------------ #

    def _ensure_workload_binary(self) -> None:
        """Fetch the norma-bin file resource and lay it down on the host (F2).

        Always re-fetch and content-compare so a ``juju refresh`` /
        ``attach-resource`` that ships NEW bytes is picked up on existing units —
        the old ``is_ready()`` short-circuit returned before fetching, so a
        running unit served a stale binary forever. ``resource-get``
        fingerprint-matches, so an unchanged resource is a cheap no-op; the
        driver only rewrites + we only restart when the bytes actually change.
        """
        try:
            path = self.model.resources.fetch("norma-bin")
        except (ops.ModelError, NameError):
            return
        if not path or not path.exists() or path.stat().st_size == 0:
            return
        try:
            changed = self.driver.install_binary(str(path))
        except WorkloadError:
            logger.exception("Failed to install workload binary")
            return
        if not changed:
            return
        self._log_event("workload-binary-updated")
        # A binary swap with an otherwise-unchanged unit file won't trip apply()'s
        # restart, so restart explicitly when the workload is already running
        # (a fresh unit is not yet running — apply() starts it after this).
        try:
            running = self.driver.service_running()
        except WorkloadError:
            running = False
        if running:
            try:
                self.driver.restart()
            except WorkloadError:
                logger.exception("Failed to restart workload after binary update")

    def _update_relation_data(self, *, broken_relation: ops.Relation | None = None) -> None:
        """Write peer + calibration relation data idempotently (F6; F7/F8 wiring)."""
        peer = self.model.get_relation("norma-peers")
        if peer:
            self._write_if_changed(
                peer.data[self.unit],
                {"unit-name": self.unit.name, "leader": str(self.unit.is_leader())},
            )
            if self.unit.is_leader():
                self._write_if_changed(
                    peer.data[self.app],
                    {"cluster-size": str(len(peer.units) + 1), "leader-unit": self.unit.name},
                )
        for endpoint in ("calibration-provider", "calibration-requirer"):
            for rel in self.model.relations.get(endpoint, []):
                if rel == broken_relation:
                    continue
                # Relation databag access can be denied while a relation is
                # departing/breaking (Juju raises ModelError "permission denied"
                # on relation-get). Guard so teardown of a related app never
                # crashes our reconcile (Constitution VII).
                try:
                    self._write_if_changed(
                        rel.data[self.unit],
                        {"unit-name": self.unit.name, "role": endpoint.split("-")[-1]},
                    )
                    # F8 app-databag mode: only the leader writes the app-scope
                    # bag; it propagates to every unit on both ends.
                    if self.unit.is_leader():
                        self._write_if_changed(
                            rel.data[self.app],
                            {
                                "app-name": self.app.name,
                                "role": endpoint.split("-")[-1],
                                "planned-units": str(self.app.planned_units()),
                            },
                        )
                except ops.ModelError:
                    logger.debug("Relation %s data not writable (departing); skipping", rel.id)
        self._manage_app_secret(broken_relation)
        self._inject_bad_relation_data(broken_relation)

    def _manage_app_secret(self, broken_relation: ops.Relation | None) -> None:
        """F10: leader owns an app secret, stores its id in peer app data, and
        grants it to calibration-provider relations (revoking the broken one)."""
        if not self.unit.is_leader():
            return
        peer = self.model.get_relation("norma-peers")
        if not peer:
            return
        secret_id = peer.data[self.app].get("secret-id")
        if not secret_id:
            new_secret = self.app.add_secret(
                {"password": secrets.token_urlsafe(24)},
                label="calibration-password",
                rotate=ops.SecretRotate.MONTHLY,
            )
            peer.data[self.app]["secret-id"] = new_secret.id
            secret_id = new_secret.id
        try:
            secret = self.model.get_secret(id=secret_id)
        except ops.SecretNotFoundError:
            secret = self._recover_app_secret(peer, secret_id)
            if secret is None:
                return
        for rel in self.model.relations.get("calibration-provider", []):
            # grant/revoke can fail while a relation is departing/breaking.
            try:
                if rel is broken_relation:
                    secret.revoke(rel)
                else:
                    secret.grant(rel)
                    # Propagate the granted id into the relation app databag so the
                    # requirer can resolve it — the grant alone is invisible to the
                    # consumer (F10 cross-app read).
                    rel.data[self.app]["shared-secret-id"] = secret.id or secret_id
            except ops.ModelError:
                logger.debug("Secret grant/revoke on relation %s skipped (departing)", rel.id)

    def _recover_app_secret(self, peer: ops.Relation, stale_id: str) -> ops.Secret | None:
        """FINDINGS#1 recovery: the stored secret-id no longer resolves.

        Live forensics (docs/FINDINGS.md, FINDINGS#1; juju 4.0.10.1) proved two
        engine anomalies: (1) in-hook SecretNotFoundError for an app-owned
        secret that still EXISTS (leader's peer relation-joined during
        scale-up); (2) non-atomic CommitHookChanges — a FAILED hook's
        relation-set persists, so a blind re-create poisons the pointer with a
        never-committed id and every later hook error-loops at commit
        ("secret with label ... already exists").

        Therefore, in order:
        1. REPAIR by label — the commit rejection itself proves a label lookup
           can succeed while the by-id lookup lies. Cheap, idempotent, loud.
        2. SUPPRESS re-creation if the ledger shows WE minted the current
           (stale) pointer in a prior self-heal — that means the create never
           committed and re-creating would loop forever (anomaly 2 makes the
           phantom pointer durable). Recovery is retried on the next event.
        3. RE-CREATE only when the label is genuinely free (secret truly gone).
        """
        repaired = None
        repaired_id = ""
        repair_error = ""
        try:
            repaired = self.model.get_secret(label="calibration-password")
            # A by-label lookup can return a Secret with id=None — resolve the
            # canonical id via owner introspection before writing the pointer
            # (a None databag write would itself crash the hook).
            repaired_id = repaired.id or repaired.get_info().id
        except (ops.SecretNotFoundError, ops.ModelError) as exc:
            # Record WHY repair failed (run 3 showed by-label resolution breaks
            # too — distinguishing NotFound from e.g. "lease not held" is the
            # forensic payload).
            repaired = None
            repair_error = f"{type(exc).__name__}: {exc}"
        if repaired is not None and repaired_id:
            peer.data[self.app]["secret-id"] = repaired_id
            self._log_event(
                "secret-pointer-repaired",
                {"stale-id": stale_id, "id": repaired_id},
            )
            return repaired
        # Loop detector — scan the FULL ledger (run 3: a sliding window misses
        # the minting entry once suppression events crowd it out). Two loop
        # worlds, both observed live:
        #   new-id == stale_id  → 4.0.10.1: the failed hook's relation-set
        #     PERSISTED (non-atomic commit), pointer became our phantom;
        #   stale-id == stale_id → 4.0 tip: commit rolls back properly, the
        #     pointer stays the unresolvable REAL id — but we already attempted
        #     a re-create for this exact pointer and it cannot commit while the
        #     label is held (17 looping attempts observed without this guard).
        # Repair-by-label remains the live retry path on every event, so
        # suppression never strands a recoverable state.
        if any(
            e.get("event_name") == "secret-recreated"
            and (
                e.get("extra", {}).get("new-id") == stale_id
                or e.get("extra", {}).get("stale-id") == stale_id
            )
            for e in self._event_ledger
        ):
            self._log_event(
                "secret-recreate-suppressed",
                {"stale-id": stale_id, "repair-error": repair_error},
            )
            return None
        try:
            new_secret = self.app.add_secret(
                {"password": secrets.token_urlsafe(24)},
                label="calibration-password",
                rotate=ops.SecretRotate.MONTHLY,
            )
        except ops.ModelError as exc:
            # Keep the hook alive (this path also runs during relation
            # teardown) but record the refusal class for the ledger.
            self._log_event(
                "secret-recreate-failed",
                {
                    "stale-id": stale_id,
                    "error": f"{type(exc).__name__}: {exc}",
                    "repair-error": repair_error,
                },
            )
            return None
        peer.data[self.app]["secret-id"] = new_secret.id
        self._log_event(
            "secret-recreated",
            {
                "stale-id": stale_id,
                "new-id": new_secret.id or "",
                "repair-error": repair_error,
            },
        )
        return new_secret

    @staticmethod
    def _write_if_changed(databag, values: dict[str, str]) -> None:
        """Write only when a value differs (avoids relation-changed feedback loops)."""
        if any(databag.get(k) != v for k, v in values.items()):
            databag.update(values)

    def _write_storage_marker(self) -> None:
        """F11: drop a one-time calibration marker into the filesystem storage mount."""
        storages = self.model.storages.get("data", [])
        if not storages:
            return
        location = str(storages[0].location)
        if not os.path.isdir(location):
            return
        marker = os.path.join(location, norma.MARKER_FILE)
        if os.path.exists(marker):
            return
        try:
            with open(marker, "w") as f:
                json.dump(
                    {
                        "created_by": self.unit.name,
                        "created_at": _utc_now_iso(),
                        "storage": "data",
                        "revision": 1,
                    },
                    f,
                )
        except OSError:
            logger.warning("Could not write storage marker at %s", marker)

    @staticmethod
    def _is_writable(location: str) -> bool:
        test_path = os.path.join(location, ".write-test")
        try:
            with open(test_path, "w") as f:
                f.write("test")
            os.remove(test_path)
            return True
        except OSError:
            return False

    def _config_dict(self) -> dict:
        # Underscore-keyed view for norma.validate_config, derived from the single
        # typed source (_collect_config) so defaults live in exactly one place (M4).
        typed = self._collect_config()
        return {
            "calibration_string": typed["calibration-string"],
            "calibration_int": typed["calibration-int"],
            "calibration_float": typed["calibration-float"],
            "calibration_bool": typed["calibration-bool"],
        }

    def _log_event(self, event_name: str, extra: dict[str, str] | None = None) -> None:
        self._event_ledger.append(
            {
                "timestamp": _utc_now_iso(),
                "event_name": event_name,
                "unit_name": self.unit.name,
                "extra": extra or {},
            }
        )
        norma.write_event_ledger(self._event_ledger)
        logger.info("Event: %s on %s", event_name, self.unit.name)

    def _get_charm_version(self) -> str:
        try:
            return (self.charm_dir / "version").read_text().strip()
        except FileNotFoundError:
            return "dev"


def _utc_now_iso() -> str:
    """Current UTC time as ISO-8601 (3.10-safe: timezone.utc, not datetime.UTC)."""
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _is_block_device(path: str) -> bool:
    """True if path is a block special file (F11 block-storage detection)."""
    import stat

    try:
        return stat.S_ISBLK(os.stat(path).st_mode)
    except OSError:
        return False


if __name__ == "__main__":  # pragma: nocover
    ops.main(NormaCharm)
