"""F12 networking: open-port + expose (jubilant, LXD).

Multi-space bindings (F12 spaces) are a documented LXD LIMITATION (alpha-only) —
not exercised here; see docs/FINDINGS.md.
"""

import jubilant

from .conftest import APP


class TestExpose:
    def test_expose_sets_exposed_flag(self, juju: jubilant.Juju):
        juju.cli("expose", APP)

        def _exposed(s) -> bool:
            return s.apps[APP].exposed

        juju.wait(_exposed, timeout=120)
        assert juju.status().apps[APP].exposed
        juju.cli("unexpose", APP)

    def test_port_opened(self, juju: jubilant.Juju):
        status = juju.status()
        unit = next(iter(status.apps[APP].units.values()))
        # The reconciler opens the configured workload port (8080 default).
        assert any("8080" in str(p) for p in (unit.open_ports or []))
        # Cross-check via the action.
        import json

        task = juju.run(f"{APP}/leader", "test-networking")
        ports = json.loads(task.results["opened-ports"])
        assert any(p.startswith("8080") for p in ports)
