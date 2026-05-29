"""Unit tests for F10 secrets lifecycle (app-owned secret, rotate/expire/remove).

ops.testing (Scenario). The conftest autouse fixture stubs systemctl + isolates
the ledger. Reconcile-driving events use the _ready() short-circuit so they do
not require a norma-bin resource.
"""

import ops
import ops.testing

import workload_driver
from charm import NormaCharm


def _ready(monkeypatch):
    monkeypatch.setattr(workload_driver.SystemdDriver, "is_ready", lambda self: True)
    monkeypatch.setattr(workload_driver.SystemdDriver, "apply", lambda self, **kw: None)
    monkeypatch.setattr(workload_driver.SystemdDriver, "service_running", lambda self: True)


class TestSecretCreation:
    def test_leader_creates_app_secret(self, monkeypatch):
        _ready(monkeypatch)
        ctx = ops.testing.Context(NormaCharm)
        peer = ops.testing.PeerRelation(endpoint="norma-peers")
        out = ctx.run(ctx.on.config_changed(), ops.testing.State(relations={peer}, leader=True))
        app_data = out.get_relation(peer.id).local_app_data
        assert app_data.get("secret-id", "").startswith("secret:")
        # The app secret exists in output state with the calibration label.
        labels = {s.label for s in out.secrets}
        assert "calibration-password" in labels

    def test_non_leader_does_not_create_secret(self, monkeypatch):
        _ready(monkeypatch)
        ctx = ops.testing.Context(NormaCharm)
        peer = ops.testing.PeerRelation(endpoint="norma-peers")
        out = ctx.run(ctx.on.config_changed(), ops.testing.State(relations={peer}, leader=False))
        assert "secret-id" not in out.get_relation(peer.id).local_app_data

    def test_existing_secret_not_recreated(self, monkeypatch):
        _ready(monkeypatch)
        existing = ops.testing.Secret(
            {"password": "pw"}, owner="app", label="calibration-password"
        )
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers", local_app_data={"secret-id": existing.id}
        )
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.config_changed(),
            ops.testing.State(relations={peer}, secrets={existing}, leader=True),
        )
        assert out.get_relation(peer.id).local_app_data["secret-id"] == existing.id
        assert len(out.secrets) == 1


class TestGetSecretInfo:
    def test_no_peer_relation(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("get-secret-info"), ops.testing.State())
        assert ctx.action_results["secret-id"] == ""
        assert ctx.action_results["has-content"] == "false"

    def test_reports_secret(self):
        existing = ops.testing.Secret(
            {"password": "pw"}, owner="app", label="calibration-password"
        )
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers", local_app_data={"secret-id": existing.id}
        )
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-secret-info"),
            ops.testing.State(relations={peer}, secrets={existing}, leader=True),
        )
        assert ctx.action_results["secret-id"] == existing.id
        assert ctx.action_results["has-content"] == "true"
        assert ctx.action_results["rotation"] == "monthly"


class TestSecretRotateExpire:
    def test_rotate_sets_new_content(self):
        existing = ops.testing.Secret(
            {"password": "old"}, owner="app", label="calibration-password"
        )
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.secret_rotate(existing),
            ops.testing.State(secrets={existing}, leader=True),
        )
        new_secret = out.get_secret(id=existing.id)
        # A fresh revision was written with a different password.
        assert new_secret.latest_content["password"] != "old"

    def test_rotate_logged(self):
        existing = ops.testing.Secret(
            {"password": "old"}, owner="app", label="calibration-password"
        )
        ctx = ops.testing.Context(NormaCharm)
        with ctx(
            ctx.on.secret_rotate(existing),
            ops.testing.State(secrets={existing}, leader=True),
        ) as mgr:
            mgr.run()
            names = [e["event_name"] for e in mgr.charm._event_ledger]
        assert "secret-rotate" in names

    def test_expire_logged(self):
        # secret-expired fires for a SUPERSEDED revision (a newer one exists),
        # so the handler can drop it. A 2-revision secret models that.
        existing = ops.testing.Secret(
            tracked_content={"password": "v1"},
            latest_content={"password": "v2"},
            owner="app",
            label="calibration-password",
        )
        ctx = ops.testing.Context(NormaCharm)
        with ctx(
            ctx.on.secret_expired(existing, revision=1),
            ops.testing.State(secrets={existing}, leader=True),
        ) as mgr:
            mgr.run()
            names = [e["event_name"] for e in mgr.charm._event_ledger]
        assert "secret-expired" in names


class TestSecretGrant:
    def test_grant_to_calibration_provider_no_error(self, monkeypatch):
        _ready(monkeypatch)
        peer = ops.testing.PeerRelation(endpoint="norma-peers")
        prov = ops.testing.Relation(endpoint="calibration-provider")
        ctx = ops.testing.Context(NormaCharm)
        # Leader creates the secret and grants it to the provider relation.
        out = ctx.run(
            ctx.on.relation_changed(prov),
            ops.testing.State(relations={peer, prov}, leader=True),
        )
        assert out.get_relation(peer.id).local_app_data.get("secret-id", "").startswith("secret:")
