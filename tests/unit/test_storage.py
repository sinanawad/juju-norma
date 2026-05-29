"""Unit tests for F11 storage actions/collectors (filesystem + block).

ops.testing (Scenario). Marker-write and writability against a real mount are
covered by the live-LXD acceptance; here we cover the action/collector logic and
the pure block-device helper.
"""

import ops
import ops.testing

import charm as charm_mod
from charm import NormaCharm


class TestCheckStorageAction:
    def test_unknown_name_raises(self):
        import pytest

        ctx = ops.testing.Context(NormaCharm)
        with pytest.raises(ops.testing.ActionFailed) as exc:
            ctx.run(ctx.on.action("check-storage", params={"name": "nope"}), ops.testing.State())
        assert "Unknown storage name" in str(exc.value)

    def test_data_not_attached(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("check-storage", params={"name": "data"}), ops.testing.State())
        assert ctx.action_results["attached"] == "false"
        assert ctx.action_results["mount-point"] == ""

    def test_blk_not_attached(self):
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("check-storage", params={"name": "blk"}), ops.testing.State())
        assert ctx.action_results["attached"] == "false"

    def test_data_attached_reports_mount(self, tmp_path):
        storage = ops.testing.Storage(name="data")
        ctx = ops.testing.Context(NormaCharm)
        ctx.run(
            ctx.on.action("check-storage", params={"name": "data"}),
            ops.testing.State(storages={storage}),
        )
        assert ctx.action_results["attached"] == "true"
        assert ctx.action_results["mount-point"]  # non-empty location string
        # filesystem keys present
        assert "marker-exists" in ctx.action_results
        assert "writable" in ctx.action_results


class TestBlockDeviceHelper:
    def test_regular_file_is_not_block(self, tmp_path):
        f = tmp_path / "regular"
        f.write_text("x")
        assert charm_mod._is_block_device(str(f)) is False

    def test_missing_path_is_not_block(self, tmp_path):
        assert charm_mod._is_block_device(str(tmp_path / "missing")) is False


class TestStorageCollector:
    def test_collects_both_via_introspect(self):
        import json

        ctx = ops.testing.Context(NormaCharm)
        ctx.run(ctx.on.action("introspect", params={"sections": "storage"}), ops.testing.State())
        storage = json.loads(ctx.action_results["storage"])
        assert "data" in storage
        assert "blk" in storage
        assert storage["data"]["attached"] is False
        assert storage["blk"]["attached"] is False
