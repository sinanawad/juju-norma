"""F5/F18 event-ORDER calibration (jubilant, LXD).

Roadmap P3-5. Juju dispatches lifecycle + relation hooks in a defined order, and
the holistic reconciler records EVERY event (in dispatch order) in the charm's
ledger — so the ledger is a faithful transcript of what the engine fired and when.
This calibrates that ORDER on a fresh machine deploy: a 4.0 engine dispatch-order
regression would reorder these entries.

Both tests deploy + read a fresh ledger, so they run in dedicated throwaway models
(``isolated_juju``): a fresh deploy keeps the startup sequence at the head of the
ledger and never touches the shared session app. Full-suite only (real machines).
"""

import json
import time

import jubilant
import pytest

from .conftest import RESOURCE_NAME

ONE = "norma-evt"
PROV = "norma-evt-prov"
REQ = "norma-evt-req"


def _ledger(juju: jubilant.Juju, unit: str) -> list[dict]:
    """Full event ledger for a unit, in dispatch order (get-event-log, uncapped)."""
    log = juju.run(unit, "get-event-log")
    return json.loads(log.results["events"])


def _first(events: list[dict], name: str) -> int | None:
    return next((i for i, e in enumerate(events) if e.get("event_name") == name), None)


def _wait_recorded(juju: jubilant.Juju, unit: str, required: set[str], timeout: float = 300):
    """Poll the ledger until every event in ``required`` has been recorded.

    all_active does NOT imply the full startup sequence is in the ledger: the
    holistic reconciler starts the workload DURING the install hook, so the charm
    can report active after install alone — before config-changed/start fire and
    are logged. (The 4.0/edge leg's timing exposed this as a flake.) So wait for the
    events to actually appear before asserting their order.
    """
    deadline = time.monotonic() + timeout
    events = _ledger(juju, unit)
    while time.monotonic() < deadline:
        if required <= {e.get("event_name") for e in events}:
            return events
        time.sleep(10)
        events = _ledger(juju, unit)
    return events  # return whatever we have; the caller's assertions report the gap


@pytest.mark.mutates
class TestEventOrder:
    def test_startup_hook_order(self, isolated_juju: jubilant.Juju, charm_path, workload_bin):
        """Canonical machine startup: install -> (leader-elected) -> config-changed -> start."""
        juju = isolated_juju
        juju.deploy(str(charm_path), app=ONE, resources={RESOURCE_NAME: str(workload_bin)})
        juju.wait(jubilant.all_active, timeout=900)

        # active can be reached on the install hook alone (workload started there),
        # so wait for the whole startup sequence to be logged before asserting order.
        events = _wait_recorded(juju, f"{ONE}/leader", {"install", "config-changed", "start"})
        names = [e.get("event_name") for e in events]
        i_install = _first(events, "install")
        i_config = _first(events, "config-changed")
        i_start = _first(events, "start")
        assert i_install is not None, f"no install recorded: {names}"
        assert i_config is not None, f"no config-changed recorded: {names}"
        assert i_start is not None, f"no start recorded: {names}"
        # Juju's documented machine lifecycle order.
        assert i_install < i_config < i_start, (
            f"startup order violated — install@{i_install} config-changed@{i_config} "
            f"start@{i_start}: {names}"
        )
        # leader-elected fires for the leader after install (a machine unit elects
        # leadership during startup, before the workload is told to start).
        i_leader = _first(events, "leader-elected")
        if i_leader is not None:
            assert i_install < i_leader, f"leader-elected before install: {names}"

    def test_relation_hook_order(self, isolated_juju: jubilant.Juju, charm_path, workload_bin):
        """Relation dispatch order on integrate — the GUARANTEED invariant.

        SOLID (asserted): on the provider, the calibration relation fires
        relation-created strictly BEFORE relation-joined.

        CALIBRATED / deliberately NOT asserted (source-traced + 4x live-verified):
        relation-changed's position — and even its PRESENCE in the immediate
        post-active window — is timing-dependent, so asserting "joined before
        changed" (the textbook order) would FLAKE. The remote APPLICATION databag is
        surfaced only via relation-changed, and the uniter's relation resolver
        evaluates the app-changed branch BEFORE the unit-joined branch
        (internal/worker/uniter/relation/resolver.go:290-335, one hook per NextOp),
        so when the app-data delta and the unit-scope delta land in the same
        change-stream snapshot the provider sees created -> changed -> joined. But
        those are two INDEPENDENT watcher signals (relation_application_settings_hash
        vs relation_unit_settings_hash), so the relative order varies and a
        relation-changed may not have been dispatched yet when the unit goes active.
        Observed across 4 live runs (4.0.10.1): [created,changed,joined] once,
        [created,joined] (no changed yet) twice. Longstanding (app databags ~2.7/2.8,
        jam 2019); byte-identical ordering on origin/3.6 and 4.0 — NOT a 4.0 delta.
        Lesson a charm must heed: do NOT assume relation-changed has fired by the
        time the unit is active, nor that joined precedes changed.
        """
        juju = isolated_juju
        resources = {RESOURCE_NAME: str(workload_bin)}
        juju.deploy(str(charm_path), app=PROV, resources=resources)
        juju.deploy(str(charm_path), app=REQ, resources=resources)
        juju.wait(jubilant.all_active, timeout=900)

        juju.integrate(f"{PROV}:calibration-provider", f"{REQ}:calibration-requirer")
        juju.wait(jubilant.all_active, timeout=300)

        events = _ledger(juju, f"{PROV}/leader")
        order = [
            e.get("event_name")
            for e in events
            if e.get("event_name", "").startswith("relation-")
            and e.get("extra", {}).get("remote-app") == REQ
        ]
        assert "relation-created" in order, f"no relation-created (remote-app={REQ}): {order}"
        assert "relation-joined" in order, f"no relation-joined (remote-app={REQ}): {order}"
        # The guaranteed invariant: the relation is created before any unit joins it.
        assert order.index("relation-created") < order.index("relation-joined"), (
            f"relation-created must precede relation-joined: {order}"
        )
