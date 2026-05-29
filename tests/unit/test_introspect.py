"""Unit tests for the F19 ``introspect`` action of the juju-norma machine charm.

The introspect action is ACTION-ONLY: it does not run the holistic reconciler
and therefore needs no ``norma-bin`` resource declared in the input State. It
must produce a structured, JSON-decodable report and must NEVER raise even if an
individual collector fails (Constitution: introspection is a diagnostic and may
never crash the hook).

Machine substrate: State has NO containers. The default ``SystemdDriver`` reports
the workload binary absent on the test host (``is_ready()`` False), and the
conftest autouse fixture stubs ``subprocess.run`` so no real ``systemctl`` runs.
"""

import json

import ops
import ops.testing

import workload_driver
from charm import NormaCharm

ALL_SECTIONS = (
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


def _run_introspect(ctx, state, *, sections=None):
    """Run the introspect action and return the (raw results, decoded) pair."""
    if sections is None:
        ctx.run(ctx.on.action("introspect"), state)
    else:
        ctx.run(ctx.on.action("introspect", params={"sections": sections}), state)
    raw = ctx.action_results
    decoded = {k: json.loads(v) for k, v in raw.items() if k != "unit"}
    return raw, decoded


class TestIntrospectNoFilter:
    def test_unit_present_and_correct(self):
        ctx = ops.testing.Context(NormaCharm)
        raw, _ = _run_introspect(ctx, ops.testing.State())
        assert raw["unit"] == "juju-norma/0"

    def test_all_sections_present(self):
        ctx = ops.testing.Context(NormaCharm)
        raw, _ = _run_introspect(ctx, ops.testing.State())
        # unit + every declared section.
        for section in ALL_SECTIONS:
            assert section in raw, f"missing section {section}"
        assert set(raw) == {"unit", *ALL_SECTIONS}

    def test_every_section_is_json_decodable(self):
        ctx = ops.testing.Context(NormaCharm)
        raw, _ = _run_introspect(ctx, ops.testing.State())
        for section in ALL_SECTIONS:
            json.loads(raw[section])  # raises if not decodable


class TestSectionFilter:
    def test_filter_two_sections(self):
        ctx = ops.testing.Context(NormaCharm)
        raw, _ = _run_introspect(ctx, ops.testing.State(), sections="config,systemd-service")
        assert set(raw) == {"unit", "config", "systemd-service"}

    def test_filter_preserves_report_order(self):
        # Requested out of declared order; charm yields declared (REPORT_SECTIONS) order.
        ctx = ops.testing.Context(NormaCharm)
        raw, _ = _run_introspect(ctx, ops.testing.State(), sections="systemd-service,config")
        keys = [k for k in raw if k != "unit"]
        assert keys == ["config", "systemd-service"]

    def test_filter_ignores_unknown_sections(self):
        ctx = ops.testing.Context(NormaCharm)
        raw, _ = _run_introspect(ctx, ops.testing.State(), sections="config,bogus,nonsense")
        assert set(raw) == {"unit", "config"}

    def test_single_section(self):
        ctx = ops.testing.Context(NormaCharm)
        raw, _ = _run_introspect(ctx, ops.testing.State(), sections="storage")
        assert set(raw) == {"unit", "storage"}


class TestSystemdSection:
    def test_keys_all_bool_default_host(self):
        # Default driver on the test host: binary absent, unit-file absent.
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(ctx, ops.testing.State(), sections="systemd-service")
        svc = decoded["systemd-service"]
        for key in ("binary-present", "service-running", "unit-file"):
            assert key in svc
            assert isinstance(svc[key], bool)
        assert svc["binary-present"] is False
        assert svc["unit-file"] is False

    def test_running_when_driver_ready(self, monkeypatch):
        monkeypatch.setattr(workload_driver.SystemdDriver, "is_ready", lambda self: True)
        monkeypatch.setattr(workload_driver.SystemdDriver, "service_running", lambda self: True)
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(ctx, ops.testing.State(), sections="systemd-service")
        svc = decoded["systemd-service"]
        assert svc["binary-present"] is True
        assert svc["service-running"] is True

    def test_service_running_workload_error_is_false(self, monkeypatch):
        def _boom(self):
            raise workload_driver.WorkloadError("boom")

        monkeypatch.setattr(workload_driver.SystemdDriver, "is_ready", lambda self: True)
        monkeypatch.setattr(workload_driver.SystemdDriver, "service_running", _boom)
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(ctx, ops.testing.State(), sections="systemd-service")
        # Collector swallows WorkloadError and reports running False; never raises.
        assert decoded["systemd-service"]["service-running"] is False


class TestConfigSection:
    def test_includes_bad_behavior_mode(self):
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(ctx, ops.testing.State(), sections="config")
        assert decoded["config"]["bad-behavior-mode"] == "none"

    def test_bad_behavior_mode_reflects_config(self):
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(
            ctx,
            ops.testing.State(config={"bad-behavior-mode": "stuck-maintenance"}),
            sections="config",
        )
        assert decoded["config"]["bad-behavior-mode"] == "stuck-maintenance"

    def test_unknown_bad_behavior_mode_normalized(self):
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(
            ctx,
            ops.testing.State(config={"bad-behavior-mode": "not-a-real-mode"}),
            sections="config",
        )
        assert decoded["config"]["bad-behavior-mode"] == "none"

    def test_config_values_reflect_state(self):
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(
            ctx,
            ops.testing.State(
                config={
                    "calibration-string": "hello",
                    "calibration-int": 9090,
                    "calibration-float": 2.5,
                    "calibration-bool": False,
                }
            ),
            sections="config",
        )
        cfg = decoded["config"]
        assert cfg["calibration-string"] == "hello"
        assert cfg["calibration-int"] == 9090
        assert cfg["calibration-float"] == 2.5
        assert cfg["calibration-bool"] is False
        assert cfg["calibration-secret"] == "unset"


class TestStorageSection:
    def test_data_keys_and_types(self):
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(ctx, ops.testing.State(), sections="storage")
        storage = decoded["storage"]
        # Both declared storages are reported; mount-point appears only when
        # attached (real Juju mount location, not a hardcoded path).
        assert "data" in storage
        assert "blk" in storage
        assert isinstance(storage["data"]["attached"], bool)
        assert storage["data"]["attached"] is False
        assert "mount-point" not in storage["data"]

    def test_data_not_attached_by_default(self):
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(ctx, ops.testing.State(), sections="storage")
        assert decoded["storage"]["data"]["attached"] is False

    def test_data_attached_when_storage_present(self):
        ctx = ops.testing.Context(NormaCharm)
        storage = ops.testing.Storage("data")
        _, decoded = _run_introspect(
            ctx, ops.testing.State(storages={storage}), sections="storage"
        )
        assert decoded["storage"]["data"]["attached"] is True


class TestRelationsAndSecretsSections:
    def test_secrets_no_peer_relation(self):
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(ctx, ops.testing.State(), sections="secrets")
        assert decoded["secrets"] == {"status": "no-peer-relation"}

    def test_relations_empty_without_relations(self):
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(ctx, ops.testing.State(), sections="relations")
        assert decoded["relations"] == {}

    def test_relations_populate_with_peer_relation(self):
        ctx = ops.testing.Context(NormaCharm)
        peer = ops.testing.PeerRelation("norma-peers")
        _, decoded = _run_introspect(
            ctx,
            ops.testing.State(leader=True, relations={peer}),
            sections="relations",
        )
        assert "norma-peers" in decoded["relations"]
        rel_entries = decoded["relations"]["norma-peers"]
        assert len(rel_entries) == 1
        assert "id" in rel_entries[0]
        assert "our-app-data" in rel_entries[0]
        assert "our-unit-data" in rel_entries[0]

    def test_secrets_populate_with_peer_and_leader(self):
        ctx = ops.testing.Context(NormaCharm)
        peer = ops.testing.PeerRelation(
            "norma-peers", local_app_data={"secret-id": "secret://abc"}
        )
        _, decoded = _run_introspect(
            ctx,
            ops.testing.State(leader=True, relations={peer}),
            sections="secrets",
        )
        sec = decoded["secrets"]
        assert sec["has-secret"] is True
        assert sec["secret-id"] == "secret://abc"


class TestLeadershipAndIdentity:
    def test_leadership_reflects_leader_flag(self):
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(ctx, ops.testing.State(leader=True), sections="leadership")
        assert decoded["leadership"]["is-leader"] is True

    def test_leadership_non_leader(self):
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(ctx, ops.testing.State(leader=False), sections="leadership")
        assert decoded["leadership"]["is-leader"] is False

    def test_identity_and_version(self):
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(ctx, ops.testing.State(), sections="identity,version")
        assert decoded["identity"]["unit-name"] == "juju-norma/0"
        assert decoded["identity"]["app-name"] == "juju-norma"
        assert decoded["version"]["charm-version"] == "dev"


class TestNeverRaises:
    def test_collector_failure_does_not_crash(self, monkeypatch):
        # Force a collector to raise; introspect must capture it as a section
        # value with status 'unavailable' rather than propagating the exception.
        def _boom(self):
            raise RuntimeError("collector exploded")

        monkeypatch.setattr(NormaCharm, "_collect_config", _boom)
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(ctx, ops.testing.State(), sections="config")
        assert decoded["config"]["status"] == "unavailable"
        assert "collector exploded" in decoded["config"]["reason"]

    def test_goal_state_unavailable_in_scenario(self):
        # No real goal-state hook tool under Scenario → collector returns a
        # structured 'unavailable' payload, still JSON-decodable, never raises.
        ctx = ops.testing.Context(NormaCharm)
        _, decoded = _run_introspect(ctx, ops.testing.State(), sections="goal-state")
        assert "goal-state" in decoded
        assert isinstance(decoded["goal-state"], dict)
