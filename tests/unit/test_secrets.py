"""Unit tests for F10 secrets lifecycle (app-owned secret, rotate/expire/remove).

ops.testing (Scenario). The conftest autouse fixture stubs systemctl + isolates
the ledger. Reconcile-driving events use the _ready() short-circuit so they do
not require a norma-bin resource.
"""

import ops
import ops.testing

import norma
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

    def test_grant_model_error_does_not_crash(self, monkeypatch):
        """The grant/revoke ModelError guard (departing relations) holds."""
        _ready(monkeypatch)

        def _raise_model_error(self, *args, **kwargs):
            raise ops.ModelError("ERROR permission denied")

        monkeypatch.setattr(ops.Secret, "grant", _raise_model_error)
        existing = ops.testing.Secret(
            {"password": "pw"}, owner="app", label="calibration-password"
        )
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers", local_app_data={"secret-id": existing.id}
        )
        prov = ops.testing.Relation(endpoint="calibration-provider")
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.relation_changed(prov),
            ops.testing.State(relations={peer, prov}, secrets={existing}, leader=True),
        )
        # Hook survived; the pointer is untouched.
        assert out.get_relation(peer.id).local_app_data["secret-id"] == existing.id


class TestSecretForensics:
    """P2-1: the previously-blind branches must surface evidence, not eat it."""

    def test_get_secret_info_surfaces_missing_secret(self):
        # Stale pointer, no such secret: error names the exception class.
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers", local_app_data={"secret-id": "secret:stale123"}
        )
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-secret-info"),
            ops.testing.State(relations={peer}, leader=True),
        )
        assert ctx.action_results["has-content"] == "false"
        assert ctx.action_results["error"].startswith("SecretNotFoundError")

    def test_get_secret_info_surfaces_model_error(self, monkeypatch):
        # E.g. Juju's "lease not held" class: message must come through verbatim.
        def _raise_lease(self, *args, **kwargs):
            raise ops.ModelError("ERROR lease not held")

        monkeypatch.setattr(ops.Secret, "get_content", _raise_lease)
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
        assert ctx.action_results["has-content"] == "false"
        assert ctx.action_results["error"] == "ModelError: ERROR lease not held"

    def test_get_secret_info_no_error_when_healthy(self):
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
        assert ctx.action_results["has-content"] == "true"
        assert ctx.action_results["error"] == ""

    def test_stale_pointer_repaired_by_label(self, monkeypatch):
        """FINDINGS#1: when the by-id lookup lies but the secret EXISTS, the
        self-heal must repair the pointer via label — never re-create (a
        re-create can't commit while the label is held: 'already exists')."""
        _ready(monkeypatch)
        existing = ops.testing.Secret(
            {"password": "pw"}, owner="app", label="calibration-password"
        )
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers", local_app_data={"secret-id": "secret:stale123"}
        )
        ctx = ops.testing.Context(NormaCharm)
        with ctx(
            ctx.on.config_changed(),
            ops.testing.State(relations={peer}, secrets={existing}, leader=True),
        ) as mgr:
            out = mgr.run()
            ledger = list(mgr.charm._event_ledger)
        assert out.get_relation(peer.id).local_app_data["secret-id"] == existing.id
        assert len(out.secrets) == 1  # repaired, NOT re-created
        repaired = [e for e in ledger if e["event_name"] == "secret-pointer-repaired"]
        assert repaired and repaired[0]["extra"]["stale-id"] == "secret:stale123"

    def test_recreate_loop_suppressed(self, monkeypatch):
        """FINDINGS#1 anomaly 2: if WE minted the current stale pointer in a
        prior (never-committed) self-heal, re-creating again would error-loop
        forever — the ledger loop-detector must suppress and keep the hook
        green."""
        _ready(monkeypatch)
        norma.write_event_ledger(
            [
                {
                    "timestamp": "2026-06-10T07:51:28+00:00",
                    "event_name": "secret-recreated",
                    "unit_name": "juju-norma/0",
                    "extra": {"stale-id": "secret:orig", "new-id": "secret:phantom1"},
                }
            ]
        )
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers", local_app_data={"secret-id": "secret:phantom1"}
        )
        ctx = ops.testing.Context(NormaCharm)
        with ctx(
            ctx.on.config_changed(),
            ops.testing.State(relations={peer}, leader=True),
        ) as mgr:
            out = mgr.run()
            ledger = list(mgr.charm._event_ledger)
        assert len(out.secrets) == 0  # no blind re-create
        # Pointer untouched (recovery retried on a later event).
        assert out.get_relation(peer.id).local_app_data["secret-id"] == "secret:phantom1"
        assert any(e["event_name"] == "secret-recreate-suppressed" for e in ledger)

    def test_stale_pointer_self_heals_with_ledger_entry(self, monkeypatch):
        """A vanished secret no longer poisons the pointer forever (charm.py
        used to `return` silently): the leader re-creates and ledgers it."""
        _ready(monkeypatch)
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers", local_app_data={"secret-id": "secret:stale123"}
        )
        ctx = ops.testing.Context(NormaCharm)
        with ctx(
            ctx.on.config_changed(),
            ops.testing.State(relations={peer}, leader=True),
        ) as mgr:
            out = mgr.run()
            ledger = list(mgr.charm._event_ledger)
        new_id = out.get_relation(peer.id).local_app_data["secret-id"]
        assert new_id.startswith("secret:") and new_id != "secret:stale123"
        assert "calibration-password" in {s.label for s in out.secrets}
        recreated = [e for e in ledger if e["event_name"] == "secret-recreated"]
        assert recreated and recreated[0]["extra"]["stale-id"] == "secret:stale123"

    def test_recreate_failure_is_recorded_not_raised(self, monkeypatch):
        """If re-creation itself is refused (transient lease loss etc.), the
        hook survives and the refusal class lands in the ledger."""
        _ready(monkeypatch)

        def _raise_model_error(self, *args, **kwargs):
            raise ops.ModelError("ERROR lease not held")

        monkeypatch.setattr(ops.Application, "add_secret", _raise_model_error)
        peer = ops.testing.PeerRelation(
            endpoint="norma-peers", local_app_data={"secret-id": "secret:stale123"}
        )
        ctx = ops.testing.Context(NormaCharm)
        with ctx(
            ctx.on.config_changed(),
            ops.testing.State(relations={peer}, leader=True),
        ) as mgr:
            mgr.run()
            ledger = list(mgr.charm._event_ledger)
        failed = [e for e in ledger if e["event_name"] == "secret-recreate-failed"]
        assert failed and failed[0]["extra"]["error"].startswith("ModelError")


class TestDropRevisionForensics:
    """P2-1: secret-remove/expired ledger entries carry revision + outcome."""

    def test_superseded_revision_dropped_outcome(self, monkeypatch):
        # Scenario's backend treats the expiring revision as latest, so ops's
        # client-side guard raises ValueError there (a swallow the OLD code hid
        # — visible now as outcome="skipped:..."). Patch remove_revision with a
        # success recorder to model the real-Juju superseded-revision path:
        # correct revision through the wiring, outcome "dropped" in the ledger.
        removed = []
        monkeypatch.setattr(
            ops.Secret, "remove_revision", lambda self, revision: removed.append(revision)
        )
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
            entries = [e for e in mgr.charm._event_ledger if e["event_name"] == "secret-expired"]
        assert removed == [1]
        assert entries[0]["extra"]["revision"] == "1"
        assert entries[0]["extra"]["outcome"] == "dropped"

    def test_unremovable_revision_outcome_names_exception(self, monkeypatch):
        # A sole/latest revision can't be removed: the swallow must be visible.
        def _raise_value_error(self, *args, **kwargs):
            raise ValueError("cannot remove the latest revision")

        monkeypatch.setattr(ops.Secret, "remove_revision", _raise_value_error)
        existing = ops.testing.Secret(
            {"password": "only"}, owner="app", label="calibration-password"
        )
        ctx = ops.testing.Context(NormaCharm)
        with ctx(
            ctx.on.secret_remove(existing, revision=1),
            ops.testing.State(secrets={existing}, leader=True),
        ) as mgr:
            mgr.run()
            entries = [e for e in mgr.charm._event_ledger if e["event_name"] == "secret-remove"]
        assert entries[0]["extra"]["revision"] == "1"
        assert entries[0]["extra"]["outcome"].startswith("skipped:ValueError")
