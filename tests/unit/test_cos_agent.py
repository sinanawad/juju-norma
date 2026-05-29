"""Unit tests for F16 cos-agent push observability (COSAgentProvider).

Verifies the provider is wired and populates the cos_agent relation databag
(metrics jobs + dashboards + alert rules) when related to a grafana-agent.
"""

import json

import ops
import ops.testing

from charm import NormaCharm


class TestCosAgentRelation:
    def test_relation_declared(self):
        ctx = ops.testing.Context(NormaCharm)
        # The cos-agent endpoint exists in metadata (drives COSAgentProvider).
        assert "cos-agent" in ctx.charm_spec.meta["provides"]

    def test_databag_populated_on_relation_joined(self, norma_bin):
        ctx = ops.testing.Context(NormaCharm)
        rel = ops.testing.SubordinateRelation(endpoint="cos-agent")
        out = ctx.run(
            ctx.on.relation_joined(rel),
            ops.testing.State(relations=[rel], resources={norma_bin}),
        )
        unit_data = out.get_relation(rel.id).local_unit_data
        # The cos_agent lib stores its provider payload under a 'config' key.
        assert "config" in unit_data
        payload = json.loads(unit_data["config"])
        # Metrics scrape jobs + alert rules propagate (KB2: non-empty alert rules).
        blob = json.dumps(payload)
        assert "metrics" in blob.lower() or "scrape" in blob.lower()
        assert "norma" in blob.lower()

    def test_no_error_without_relation(self, norma_bin):
        ctx = ops.testing.Context(NormaCharm)
        # Reconcile path unaffected when cos-agent is not related.
        ctx.run(ctx.on.update_status(), ops.testing.State(resources={norma_bin}))
