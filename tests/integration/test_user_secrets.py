"""F3/F4 user-secret consumption + secret-config access enforcement (jubilant, LXD).

Roadmap P3-4. A USER secret (``juju add-secret`` — admin/model-owned, distinct from
the charm's own APP secret in F10) is consumed via the ``type: secret`` config
option ``calibration-secret``: the charm resolves it with
``model.get_secret().get_content()`` in the reconciler, blocking if it can't.

Calibrates two machine-charm behaviours:
  A. happy consumption — add-secret -> grant-secret -> config set -> charm resolves
     -> active (and a clean reset);
  B. access enforcement — an UNGRANTED secret URI must never leave the app silently
     active. A ``type: secret`` option makes Juju enforce the grant at config-set
     time, so this is calibrated as "Juju rejects the set OR the charm Blocks" —
     never silent acceptance.

Dedicated throwaway model (``isolated_juju``): creates/grants user secrets and
churns secret-config; isolation keeps it off the shared session app. Full-suite only.
"""

import jubilant
import pytest

from .conftest import RESOURCE_NAME

APP = "norma-usec"


def _unit(juju: jubilant.Juju):
    return next(iter(juju.status().apps[APP].units.values()))


@pytest.mark.mutates
class TestUserSecret:
    def test_granted_user_secret_resolves(
        self, isolated_juju: jubilant.Juju, charm_path, workload_bin
    ):
        """A: add-secret -> grant-secret -> config set -> charm resolves -> active."""
        juju = isolated_juju
        juju.deploy(str(charm_path), app=APP, resources={RESOURCE_NAME: str(workload_bin)})
        juju.wait(jubilant.all_active, timeout=900)

        uri = juju.cli("add-secret", "cal-user-secret", "token=s3cr3t").strip()
        assert uri.startswith("secret:"), f"unexpected add-secret output: {uri!r}"
        juju.cli("grant-secret", "cal-user-secret", APP)

        # Setting the granted secret URI drives a reconcile that calls
        # get_secret().get_content(); staying active proves the charm read it
        # (an unresolvable secret would force BlockedStatus, charm.py:203-212).
        juju.config(APP, {"calibration-secret": uri})
        juju.wait(jubilant.all_active, timeout=180)
        assert _unit(juju).is_active, "granted user secret did not resolve to active"

        # Reset is clean (recovers to active with no secret config).
        juju.config(APP, reset=["calibration-secret"])
        juju.wait(jubilant.all_active, timeout=180)

    def test_ungranted_secret_blocks_at_read_time(
        self, isolated_juju: jubilant.Juju, charm_path, workload_bin
    ):
        """B: an UNGRANTED user secret must not leave the app silently active.

        CALIBRATED (4.0.10.1): a ``type: secret`` config option does NOT enforce the
        grant at config-SET time — ``juju config calibration-secret=<ungranted-uri>``
        is ACCEPTED — enforcement is deferred to READ time: the charm's
        get_secret().get_content() raises a permission ModelError, surfaced as
        Blocked ("... is not allowed to read this secret"). The CLIError branch keeps
        the test honest if a future Juju instead rejects at config-set (still no
        silent acceptance). Isolated model → no recovery/cleanup step needed.
        """
        juju = isolated_juju
        juju.deploy(str(charm_path), app=APP, resources={RESOURCE_NAME: str(workload_bin)})
        juju.wait(jubilant.all_active, timeout=900)

        # Juju secret keys need a minimum length (single-char keys are rejected),
        # so use a real key name here too.
        uri = juju.cli("add-secret", "ungranted-secret", "token=v").strip()
        assert uri.startswith("secret:"), f"unexpected add-secret output: {uri!r}"

        try:
            juju.config(APP, {"calibration-secret": uri})
        except jubilant.CLIError:
            # A future Juju may enforce the grant at config-set time; app untouched.
            juju.wait(jubilant.all_active, timeout=60)
            assert _unit(juju).is_active
            return

        # 4.0 behaviour: accepted at set, charm Blocks at read with a permission error.
        juju.wait(jubilant.any_blocked, timeout=180)
        unit = _unit(juju)
        assert unit.is_blocked, (
            f"ungranted secret left status active: {unit.workload_status.message!r}"
        )
        assert "secret" in unit.workload_status.message.lower(), (
            f"block message should cite the secret error: {unit.workload_status.message!r}"
        )
