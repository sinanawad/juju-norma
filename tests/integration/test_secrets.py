"""F10 secrets lifecycle (jubilant, LXD)."""

import jubilant
import pytest

from .conftest import APP

# Read-only secret introspection — container-safe and fast; per-PR smoke subset.
pytestmark = pytest.mark.smoke


class TestSecrets:
    def test_app_secret_created(self, juju: jubilant.Juju):
        """F10: the leader owns an app secret reported by get-secret-info."""
        task = juju.run(f"{APP}/leader", "get-secret-info")
        assert task.results["secret-id"].startswith("secret:")
        # On failure, surface the charm-side forensic readout (exception class +
        # message, e.g. Juju's "lease not held") — a bare has-content=false is
        # undiagnosable. See the full-suite isolation-artifact investigation.
        assert task.results["has-content"] == "true", (
            f"secret unreadable: error={task.results.get('error', '')!r} "
            f"(secret-id={task.results['secret-id']})"
        )
        assert task.results["rotation"] == "monthly"

    def test_secret_listed_by_juju(self, juju: jubilant.Juju):
        """F10: the secret is visible to `juju secrets`."""
        out = juju.cli("secrets", "--format", "json", include_model=True)
        import json

        secrets = json.loads(out)
        # At least one app-owned secret exists for our app.
        assert any(
            (s.get("owner") == APP or s.get("owner-tag", "").endswith(APP))
            for s in secrets.values()
        ), f"no {APP}-owned secret in: {secrets}"
