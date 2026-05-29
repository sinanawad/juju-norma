"""Unit tests for P3 relations: F7 provides/requires, F8 app-databag, F17 upgrade-charm.

ops.testing (Scenario), machine substrate (no containers). The conftest autouse
fixture stubs systemctl and isolates the ledger.
"""

import json

import ops
import ops.testing
import pytest

import workload_driver
from charm import NormaCharm


@pytest.fixture
def norma_bin(tmp_path):
    p = tmp_path / "norma-bin"
    p.write_bytes(b"")
    return ops.testing.Resource(name="norma-bin", path=p)


def _ready(monkeypatch):
    monkeypatch.setattr(workload_driver.SystemdDriver, "is_ready", lambda self: True)
    monkeypatch.setattr(workload_driver.SystemdDriver, "apply", lambda self, **kw: None)
    monkeypatch.setattr(workload_driver.SystemdDriver, "service_running", lambda self: True)


class TestSelfRelateData:
    """F7: provider/requirer unit-data on the calibration interface."""

    def test_provider_unit_data(self, monkeypatch):
        _ready(monkeypatch)
        ctx = ops.testing.Context(NormaCharm)
        rel = ops.testing.Relation(endpoint="calibration-provider")
        out = ctx.run(ctx.on.relation_changed(rel), ops.testing.State(relations=[rel]))
        data = out.get_relation(rel.id).local_unit_data
        assert data["unit-name"] == "juju-norma/0"
        assert data["role"] == "provider"

    def test_requirer_unit_data(self, monkeypatch):
        _ready(monkeypatch)
        ctx = ops.testing.Context(NormaCharm)
        rel = ops.testing.Relation(endpoint="calibration-requirer")
        out = ctx.run(ctx.on.relation_changed(rel), ops.testing.State(relations=[rel]))
        data = out.get_relation(rel.id).local_unit_data
        assert data["role"] == "requirer"

    def test_both_endpoints_self_relate(self, monkeypatch):
        _ready(monkeypatch)
        ctx = ops.testing.Context(NormaCharm)
        prov = ops.testing.Relation(endpoint="calibration-provider")
        req = ops.testing.Relation(endpoint="calibration-requirer")
        out = ctx.run(ctx.on.relation_changed(prov), ops.testing.State(relations=[prov, req]))
        assert out.get_relation(prov.id).local_unit_data["role"] == "provider"
        assert out.get_relation(req.id).local_unit_data["role"] == "requirer"


class TestAppDatabag:
    """F8: leader-only app-scope databag propagation."""

    def test_leader_writes_app_data(self, monkeypatch):
        _ready(monkeypatch)
        ctx = ops.testing.Context(NormaCharm)
        rel = ops.testing.Relation(endpoint="calibration-provider")
        out = ctx.run(
            ctx.on.relation_changed(rel), ops.testing.State(relations=[rel], leader=True)
        )
        app_data = out.get_relation(rel.id).local_app_data
        assert app_data["app-name"] == "juju-norma"
        assert app_data["role"] == "provider"
        assert app_data["planned-units"] == "1"

    def test_non_leader_no_app_data(self, monkeypatch):
        _ready(monkeypatch)
        ctx = ops.testing.Context(NormaCharm)
        rel = ops.testing.Relation(endpoint="calibration-provider")
        out = ctx.run(
            ctx.on.relation_changed(rel), ops.testing.State(relations=[rel], leader=False)
        )
        assert "app-name" not in out.get_relation(rel.id).local_app_data

    def test_app_data_idempotent(self, monkeypatch):
        _ready(monkeypatch)
        ctx = ops.testing.Context(NormaCharm)
        rel = ops.testing.Relation(
            endpoint="calibration-provider",
            local_unit_data={"unit-name": "juju-norma/0", "role": "provider"},
            local_app_data={"app-name": "juju-norma", "role": "provider", "planned-units": "1"},
        )
        out = ctx.run(
            ctx.on.relation_changed(rel), ops.testing.State(relations=[rel], leader=True)
        )
        assert out.get_relation(rel.id).local_app_data == {
            "app-name": "juju-norma",
            "role": "provider",
            "planned-units": "1",
        }


class TestGetRelationDataAction:
    def test_empty_endpoint(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-relation-data", params={"endpoint": "calibration-provider"}),
            ops.testing.State(),
        )
        assert json.loads(ctx.action_results["relations"]) == []

    def test_returns_app_and_unit_data(self):
        ctx = ops.testing.Context(NormaCharm)
        rel = ops.testing.Relation(
            endpoint="calibration-provider",
            local_unit_data={"unit-name": "juju-norma/0", "role": "provider"},
            local_app_data={"app-name": "juju-norma"},
        )
        ctx.run(
            ctx.on.action("get-relation-data", params={"endpoint": "calibration-provider"}),
            ops.testing.State(relations=[rel], leader=True),
        )
        result = json.loads(ctx.action_results["relations"])
        assert len(result) == 1
        assert result[0]["id"] == rel.id
        assert result[0]["app-data"]["app-name"] == "juju-norma"
        assert "juju-norma/0" in result[0]["units"]

    def test_filter_by_relation_id(self):
        ctx = ops.testing.Context(NormaCharm)
        r1 = ops.testing.Relation(endpoint="calibration-provider")
        r2 = ops.testing.Relation(endpoint="calibration-provider")
        ctx.run(
            ctx.on.action(
                "get-relation-data",
                params={"endpoint": "calibration-provider", "relation-id": r1.id},
            ),
            ops.testing.State(relations=[r1, r2], leader=True),
        )
        result = json.loads(ctx.action_results["relations"])
        assert len(result) == 1
        assert result[0]["id"] == r1.id


class TestUpgradeCharm:
    """F17: upgrade-charm routes through reconcile and stamps workload version."""

    def test_upgrade_charm_logged(self, monkeypatch, norma_bin):
        ctx = ops.testing.Context(NormaCharm)
        with ctx(ctx.on.upgrade_charm(), ops.testing.State(resources={norma_bin})) as mgr:
            mgr.run()
            names = [e["event_name"] for e in mgr.charm._event_ledger]
        assert "upgrade-charm" in names

    def test_workload_version_set_on_upgrade(self, monkeypatch):
        _ready(monkeypatch)
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(ctx.on.upgrade_charm(), ops.testing.State())
        assert out.workload_version == "dev"
