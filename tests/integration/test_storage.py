"""F11 storage: filesystem marker + dynamic add/detach/attach (jubilant, LXD).

Block storage is a documented LXD LIMITATION (provider rejects block charm
storage) — covered by an xfail rather than skipped, so a future MAAS run flips it.
"""

import json

import jubilant
import pytest

from .conftest import APP


class TestFilesystemStorage:
    def test_check_storage_data_attached(self, juju: jubilant.Juju):
        task = juju.run(f"{APP}/leader", "check-storage", params={"name": "data"})
        assert task.results["attached"] == "true"
        assert task.results["mount-point"]
        assert task.results["marker-exists"] == "true"
        assert task.results["writable"] == "true"


class TestDynamicStorage:
    def test_add_detach_attach_cycle(self, juju: jubilant.Juju):
        """F11: the IAAS-only dynamic storage cycle on the filesystem store.

        data has multiple range 1-5, so a second instance can be added then
        detached and re-attached.
        """
        unit = f"{APP}/0"
        juju.cli("add-storage", unit, "data=1G")

        def _data_count(s) -> int:
            return juju.cli("storage", "--format", "json", include_model=True).count('"data/')

        # Wait until a second data instance is attached to our unit.
        def _two_instances(_s) -> bool:
            out = json.loads(juju.cli("storage", "--format", "json", include_model=True))
            storage = out.get("storage", {})
            mine = [k for k, v in storage.items() if k.startswith("data/")]
            return len(mine) >= 2

        juju.wait(_two_instances, timeout=300)

        out = json.loads(juju.cli("storage", "--format", "json", include_model=True))
        new_id = sorted(
            (k for k in out.get("storage", {}) if k.startswith("data/")),
            key=lambda k: int(k.split("/")[1]),
        )[-1]

        juju.cli("detach-storage", new_id)
        juju.cli("attach-storage", unit, new_id)
        juju.wait(jubilant.all_active, timeout=300)


@pytest.mark.xfail(
    reason="LXD provider rejects block charm storage (needs MAAS/cloud)", strict=False
)
class TestBlockStorage:
    def test_add_block_storage(self, juju: jubilant.Juju):
        juju.cli("add-storage", f"{APP}/0", "blk=lxd,1G")
        juju.wait(jubilant.all_active, timeout=300)
        task = juju.run(f"{APP}/leader", "check-storage", params={"name": "blk"})
        assert task.results["attached"] == "true"
