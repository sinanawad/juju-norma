"""Unit tests for F6 (peer relation + leadership) using ops.testing (Scenario).

The charm writes peer relation data in ``_update_relation_data`` during every
reconcile: each unit publishes ``unit-name`` + ``leader`` to its own unit bag,
and the leader publishes ``cluster-size`` + ``leader-unit`` to the app bag.
The ``get-peer-data`` and ``get-cluster-info`` actions read this back.

Machine substrate: NO containers. Reconcile-driving events (config-changed)
call ``model.resources.fetch("norma-bin")`` so we monkeypatch the driver
ready/short-circuit the fetch. Action-only events do NOT reconcile and need no
resource (so peer-data / cluster-info actions run against a bare State).
"""

import json

import ops
import ops.testing

import workload_driver
from charm import NormaCharm


def _make_ready(monkeypatch):
    """Short-circuit SystemdDriver so reconcile skips resource fetch + apply."""
    monkeypatch.setattr(workload_driver.SystemdDriver, "is_ready", lambda self: True)
    monkeypatch.setattr(workload_driver.SystemdDriver, "apply", lambda self, **kw: None)
    monkeypatch.setattr(workload_driver.SystemdDriver, "service_running", lambda self: True)


def _peer_from(out):
    """Return the single norma-peers PeerRelation from an output State."""
    peers = [r for r in out.relations if r.endpoint == "norma-peers"]
    assert len(peers) == 1, f"expected one norma-peers relation, got {peers}"
    return peers[0]


class TestPeerDataWrite:
    def test_leader_writes_unit_and_app_data(self, monkeypatch):
        _make_ready(monkeypatch)
        peer = ops.testing.PeerRelation(endpoint="norma-peers")
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.config_changed(),
            ops.testing.State(leader=True, relations={peer}),
        )
        rel = _peer_from(out)
        assert rel.local_unit_data["unit-name"] == "juju-norma/0"
        assert rel.local_unit_data["leader"] == "True"
        assert rel.local_app_data["leader-unit"] == "juju-norma/0"
        assert rel.local_app_data["cluster-size"] == "1"

    def test_non_leader_writes_unit_data_only(self, monkeypatch):
        _make_ready(monkeypatch)
        peer = ops.testing.PeerRelation(endpoint="norma-peers")
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.config_changed(),
            ops.testing.State(leader=False, relations={peer}),
        )
        rel = _peer_from(out)
        assert rel.local_unit_data["unit-name"] == "juju-norma/0"
        assert rel.local_unit_data["leader"] == "False"
        # Non-leader must not touch the app databag.
        assert "leader-unit" not in rel.local_app_data
        assert "cluster-size" not in rel.local_app_data

    def test_cluster_size_reflects_peer_count(self, monkeypatch):
        _make_ready(monkeypatch)
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers",
            peers_data={1: {}, 2: {}},
        )
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.config_changed(),
            ops.testing.State(leader=True, relations={peer}),
        )
        rel = _peer_from(out)
        # self (1) + two peers = 3.
        assert rel.local_app_data["cluster-size"] == "3"


class TestPeerDataIdempotency:
    def test_no_churn_when_data_already_matches(self, monkeypatch):
        _make_ready(monkeypatch)
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers",
            local_unit_data={"unit-name": "juju-norma/0", "leader": "True"},
            local_app_data={"cluster-size": "1", "leader-unit": "juju-norma/0"},
        )
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.config_changed(),
            ops.testing.State(leader=True, relations={peer}),
        )
        rel = _peer_from(out)
        # Data is unchanged (no spurious writes / feedback-loop churn).
        assert rel.local_unit_data == {"unit-name": "juju-norma/0", "leader": "True"}
        assert rel.local_app_data == {"cluster-size": "1", "leader-unit": "juju-norma/0"}


class TestGetPeerDataAction:
    def test_no_peer_relation_returns_empty(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("get-peer-data"), ops.testing.State())
        assert ctx.action_results["app-data"] == "{}"
        assert ctx.action_results["unit-data"] == "{}"

    def test_returns_app_and_unit_data_json(self):
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers",
            local_unit_data={"unit-name": "juju-norma/0", "leader": "True"},
            local_app_data={"cluster-size": "1", "leader-unit": "juju-norma/0"},
        )
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-peer-data"),
            ops.testing.State(leader=True, relations={peer}),
        )
        app_data = json.loads(ctx.action_results["app-data"])
        unit_data = json.loads(ctx.action_results["unit-data"])
        assert app_data == {"cluster-size": "1", "leader-unit": "juju-norma/0"}
        assert unit_data["juju-norma/0"] == {"unit-name": "juju-norma/0", "leader": "True"}

    def test_unit_data_includes_peers(self):
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers",
            local_unit_data={"unit-name": "juju-norma/0"},
            peers_data={1: {"unit-name": "juju-norma/1"}},
        )
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-peer-data"),
            ops.testing.State(relations={peer}),
        )
        unit_data = json.loads(ctx.action_results["unit-data"])
        assert "juju-norma/0" in unit_data
        assert unit_data["juju-norma/1"] == {"unit-name": "juju-norma/1"}


class TestGetClusterInfoAction:
    def test_single_leader_unit(self):
        peer = ops.testing.PeerRelation(endpoint="norma-peers")
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-cluster-info"),
            ops.testing.State(leader=True, relations={peer}),
        )
        assert ctx.action_results["unit-count"] == "1"
        assert ctx.action_results["is-leader"] == "True"
        assert ctx.action_results["leader"] == "juju-norma/0"
        assert json.loads(ctx.action_results["units"]) == ["juju-norma/0"]

    def test_single_unit_no_peer_relation(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-cluster-info"),
            ops.testing.State(leader=True),
        )
        assert ctx.action_results["unit-count"] == "1"
        assert ctx.action_results["is-leader"] == "True"

    def test_unit_count_with_peers(self):
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers",
            peers_data={1: {}, 2: {}},
        )
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-cluster-info"),
            ops.testing.State(leader=True, relations={peer}),
        )
        assert ctx.action_results["unit-count"] == "3"

    def test_non_leader_reads_leader_from_app_data(self):
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers",
            local_app_data={"leader-unit": "juju-norma/1"},
            peers_data={1: {}},
        )
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-cluster-info"),
            ops.testing.State(leader=False, relations={peer}),
        )
        assert ctx.action_results["is-leader"] == "False"
        assert ctx.action_results["leader"] == "juju-norma/1"
        assert ctx.action_results["unit-count"] == "2"

    def test_non_leader_empty_leader_when_app_data_missing(self):
        peer = ops.testing.PeerRelation(endpoint="norma-peers")
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-cluster-info"),
            ops.testing.State(leader=False, relations={peer}),
        )
        assert ctx.action_results["is-leader"] == "False"
        assert ctx.action_results["leader"] == ""


class TestLeaderElected:
    def test_leader_elected_populates_app_data(self, monkeypatch):
        _make_ready(monkeypatch)
        peer = ops.testing.PeerRelation(endpoint="norma-peers")
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.leader_elected(),
            ops.testing.State(leader=True, relations={peer}),
        )
        rel = _peer_from(out)
        assert rel.local_app_data["leader-unit"] == "juju-norma/0"
        assert rel.local_app_data["cluster-size"] == "1"
