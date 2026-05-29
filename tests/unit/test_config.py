"""Unit tests for F3 typed config of the juju-norma machine charm.

Covers (Scenario / ops.testing):
- get-config action surfaces every typed option (string/int/float/bool),
  calibration-secret reported "set"/"unset", and bad-behavior-mode.
- Invalid calibration-int (boundary 0 and 99999) via config-changed → Blocked
  mentioning calibration-int.
- calibration-secret resolution: a present readable Secret → not blocked;
  a dangling "secret:bogus" URI → Blocked mentioning the secret.

Machine substrate: NO containers. Any event running the reconciler
(config-changed) calls model.resources.fetch("norma-bin"), so the State must
declare the norma-bin resource (an empty placeholder = "not yet delivered").
Action-only events (get-config) do NOT reconcile, so no resource is needed.
"""

import ops
import ops.testing
import pytest

import workload_driver
from charm import NormaCharm


@pytest.fixture
def norma_bin(tmp_path):
    """Empty placeholder norma-bin resource (treated as not-yet-delivered)."""
    placeholder = tmp_path / "norma-bin"
    placeholder.write_bytes(b"")
    return ops.testing.Resource(name="norma-bin", path=placeholder)


class TestGetConfigAction:
    """get-config returns every typed option in its expected string form."""

    def test_defaults(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("get-config"), ops.testing.State())
        r = ctx.action_results
        assert r["calibration-string"] == "default"
        assert r["calibration-int"] == "8080"
        assert r["calibration-float"] == "1.0"
        assert r["calibration-bool"] == "True"
        assert r["calibration-secret"] == "unset"
        assert r["bad-behavior-mode"] == "none"

    def test_explicit_values(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-config"),
            ops.testing.State(
                config={
                    "calibration-string": "calibrate-me",
                    "calibration-int": 1234,
                    "calibration-float": 3.5,
                    "calibration-bool": False,
                }
            ),
        )
        r = ctx.action_results
        assert r["calibration-string"] == "calibrate-me"
        assert r["calibration-int"] == "1234"
        assert r["calibration-float"] == "3.5"
        assert r["calibration-bool"] == "False"

    def test_secret_unset_by_default(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("get-config"), ops.testing.State())
        assert ctx.action_results["calibration-secret"] == "unset"

    def test_secret_set_when_configured(self):
        ctx = ops.testing.Context(NormaCharm)
        secret = ops.testing.Secret(
            tracked_content={"calibration": "value"},
            owner="app",
        )
        ctx.run(
            ctx.on.action("get-config"),
            ops.testing.State(
                secrets={secret},
                config={"calibration-secret": secret.id},
            ),
        )
        assert ctx.action_results["calibration-secret"] == "set"

    def test_bad_behavior_mode_reported(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-config"),
            ops.testing.State(config={"bad-behavior-mode": "stuck-maintenance"}),
        )
        assert ctx.action_results["bad-behavior-mode"] == "stuck-maintenance"

    def test_bad_behavior_mode_unknown_falls_back_to_none(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("get-config"),
            ops.testing.State(config={"bad-behavior-mode": "not-a-real-mode"}),
        )
        assert ctx.action_results["bad-behavior-mode"] == "none"


class TestInvalidCalibrationInt:
    """Out-of-range calibration-int → Blocked mentioning calibration-int."""

    def test_zero_blocks(self, norma_bin):
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.config_changed(),
            ops.testing.State(resources={norma_bin}, config={"calibration-int": 0}),
        )
        assert isinstance(out.unit_status, ops.BlockedStatus)
        assert "calibration-int" in out.unit_status.message

    def test_above_range_blocks(self, norma_bin):
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.config_changed(),
            ops.testing.State(resources={norma_bin}, config={"calibration-int": 99999}),
        )
        assert isinstance(out.unit_status, ops.BlockedStatus)
        assert "calibration-int" in out.unit_status.message


class TestCalibrationSecretResolution:
    """Secret-type config URI resolution on the reconcile path."""

    def test_resolvable_secret_not_blocked(self, monkeypatch, norma_bin):
        # Short-circuit binary readiness so the reconciler proceeds past secret
        # resolution to a clean (non-blocked) outcome without host side effects.
        monkeypatch.setattr(workload_driver.SystemdDriver, "is_ready", lambda self: True)
        monkeypatch.setattr(workload_driver.SystemdDriver, "apply", lambda self, **kw: None)
        monkeypatch.setattr(workload_driver.SystemdDriver, "service_running", lambda self: True)
        ctx = ops.testing.Context(NormaCharm)
        secret = ops.testing.Secret(
            tracked_content={"calibration": "value"},
            owner="app",
        )
        out = ctx.run(
            ctx.on.config_changed(),
            ops.testing.State(
                leader=True,
                resources={norma_bin},
                secrets={secret},
                config={"calibration-secret": secret.id},
            ),
        )
        assert not isinstance(out.unit_status, ops.BlockedStatus)
        assert out.unit_status == ops.ActiveStatus()

    def test_secret_not_found_blocks(self, monkeypatch, norma_bin):
        # A dangling secret URI must resolve to BlockedStatus mentioning the
        # secret. SCENARIO_SKIP_CONSISTENCY_CHECKS lets us reference a secret id
        # that is not present in the State.
        monkeypatch.setenv("SCENARIO_SKIP_CONSISTENCY_CHECKS", "1")
        ctx = ops.testing.Context(NormaCharm)
        out = ctx.run(
            ctx.on.config_changed(),
            ops.testing.State(
                resources={norma_bin},
                secrets=[],
                config={"calibration-secret": "secret:bogus"},
            ),
        )
        assert isinstance(out.unit_status, ops.BlockedStatus)
        assert "secret" in out.unit_status.message.lower()
