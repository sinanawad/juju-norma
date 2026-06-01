"""F17 upgrade-charm + version stamping (jubilant, LXD)."""

import jubilant

from .conftest import APP


class TestUpgrade:
    def test_refresh_runs_upgrade_charm(self, juju: jubilant.Juju, charm_path, workload_bin):
        """F17: juju refresh bumps the revision and fires upgrade-charm."""
        before = juju.run(
            f"{APP}/leader", "get-event-log", params={"event-filter": "upgrade-charm"}
        )
        before_count = int(before.results["count"])

        juju.cli(
            "refresh",
            APP,
            "--path",
            str(charm_path),
            "--resource",
            f"norma-bin={workload_bin}",
            include_model=True,
        )
        juju.wait(jubilant.all_active, timeout=300)

        after = juju.run(
            f"{APP}/leader", "get-event-log", params={"event-filter": "upgrade-charm"}
        )
        assert int(after.results["count"]) > before_count

        # Workload version is (re)stamped after the upgrade.
        ver = juju.run(f"{APP}/leader", "get-version")
        assert ver.results["charm-version"]
