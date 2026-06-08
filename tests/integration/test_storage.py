"""F11 storage: filesystem marker + dynamic add/detach/attach (jubilant, LXD).

Block storage is a documented LXD LIMITATION (provider rejects block charm
storage) — covered by an xfail rather than skipped, so a future MAAS run flips it.
"""

import json
import time

import jubilant
import pytest

from .conftest import APP


# The read-only filesystem-store check is container-safe + fast (LXD provisions
# filesystem storage) — per-PR smoke. The dynamic add/detach/attach cycle and
# block-storage cases are slower / not LXD-verifiable, so they stay full-suite-only.
@pytest.mark.smoke
class TestFilesystemStorage:
    def test_check_storage_data_attached(self, juju: jubilant.Juju):
        task = juju.run(f"{APP}/leader", "check-storage", params={"name": "data"})
        assert task.results["attached"] == "true"
        assert task.results["mount-point"]
        assert task.results["marker-exists"] == "true"
        assert task.results["writable"] == "true"


@pytest.mark.mutates
class TestDynamicStorage:
    def test_add_detach_attach_cycle(self, juju: jubilant.Juju):
        """F11: the IAAS-only dynamic storage cycle on the filesystem store.

        data has multiple range 1-5, so a second instance can be added then
        detached and re-attached.
        """
        unit = f"{APP}/0"

        def _data_ids() -> list[str]:
            out = json.loads(juju.cli("storage", "--format", "json", include_model=True))
            return [k for k in out.get("storage", {}) if k.startswith("data/")]

        def _data_status(sid: str) -> str:
            out = json.loads(juju.cli("storage", "--format", "json", include_model=True))
            return out.get("storage", {}).get(sid, {}).get("status", {}).get("current", "gone")

        juju.cli("add-storage", unit, "data=1G")
        # Identify the new instance once it exists...
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and len(_data_ids()) < 2:
            time.sleep(8)
        ids = _data_ids()
        assert len(ids) >= 2, f"second data store never appeared: {ids}"
        new_id = sorted(ids, key=lambda k: int(k.split("/")[1]))[-1]

        # ...then wait for it to fully ATTACH before detaching. add-storage is
        # async: the instance appears as `attaching`/`pending` first, and
        # detaching a not-yet-attached store leaves it wedged so the later
        # re-attach fails "already attached".
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and _data_status(new_id) != "attached":
            time.sleep(8)
        assert _data_status(new_id) == "attached", (
            f"{new_id} never attached: {_data_status(new_id)}"
        )

        # Detach is async — wait until it is actually detached before re-attaching.
        juju.cli("detach-storage", new_id)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline and _data_status(new_id) == "attached":
            time.sleep(8)
        assert _data_status(new_id) != "attached", f"{new_id} never detached"

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
