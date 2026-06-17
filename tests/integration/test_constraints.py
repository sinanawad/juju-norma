"""F13 machine constraints + placement, and F15 lxd-profile (jubilant, LXD).

Machine-distinct calibration (roadmap P3-1). Constraints the LXD provider honors
are asserted both STORED (``juju constraints``) and APPLIED (machine
hardware-characteristics). Code-verified vs 4.0 (internal/provider/lxd/
environ_policy.go + internal/container/lxd/container.go): LXD APPLIES
``arch, cores, mem, virt-type, instance-type`` (and root-disk/root-disk-source
when a matching storage pool exists); it marks exactly five UNSUPPORTED —
``cpu-power, tags, container, allocate-public-ip, image-id`` — which it warns +
drops (stored on the app, never applied). ``instance-role``/``zones`` pass the
validator but are no-ops on standalone LXD.

What's calibrated here: the honored set (arch/cores/mem applied) + the
unsupported-drop (cpu-power stored-not-applied) deterministically in one app;
and two LXD-limitation traps as ``xfail`` (strict=False, so a MAAS / real-cloud /
non-nested-host run flips them loudly) — root-disk's storage-pool dependence
(FINDINGS 3c) and lxd-profile *application* (FINDINGS A.3).

Cost note: the honored-set test deploys a dedicated short-lived app (one machine
provision) and force-removes it, so it never pollutes the shared session model.
Full-suite only (no ``smoke`` marker) — machine provisioning is too slow per-PR.
"""

import json

import jubilant
import pytest

from .conftest import APP, RESOURCE_NAME

# Dedicated, short-lived app so constraints are exercised at DEPLOY (where the
# provider applies them to the new machine) without disturbing the session app.
CSTR_APP = "norma-cstr"

# Kept modest for CI reliability: the Go workload is tiny, so 1 core / 512M is
# ample and provisions fast. The point is calibrating that the provider HONORS
# the values, not the values themselves.
CORES = 1
MEM_MB = 512


def _status(juju: jubilant.Juju) -> dict:
    return json.loads(juju.cli("status", "--format", "json", include_model=True))


def _machine_hardware_for(juju: jubilant.Juju, app: str, unit: str) -> str:
    """The hardware-characteristics string of the machine hosting ``unit``."""
    st = _status(juju)
    machine_id = st["applications"][app]["units"][unit]["machine"]
    return st["machines"][machine_id].get("hardware", "")


def _parse_hw(hardware: str) -> dict[str, str]:
    """Parse 'arch=amd64 cores=1 mem=512M ...' into a dict."""
    out: dict[str, str] = {}
    for tok in hardware.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


@pytest.mark.mutates
class TestConstraintsHonored:
    """F13: LXD honors arch/cores/mem at deploy; drops cpu-power (warned, stored
    but not applied). Code-verified vs 4.0: validator marks cpu-power unsupported
    (environ_policy.go:28-34); arch/cores/mem/virt-type/instance-type are applied
    in ContainerSpec.ApplyConstraints (container.go:53-98)."""

    def test_deploy_constraints_stored_and_applied(
        self, juju: jubilant.Juju, charm_path, workload_bin
    ):
        # cpu-power is in the same deploy on purpose: LXD's validator WARNS + drops
        # it (it is one of the 5 unsupported: cpu-power, tags, container,
        # allocate-public-ip, image-id), so the deploy still SUCCEEDS and `juju
        # constraints` still STORES it, but it never reaches the machine hardware.
        # Calibrates "honored-applied" and "unsupported-stored-not-applied" in one app.
        constraints = f"arch=amd64 cores={CORES} mem={MEM_MB}M cpu-power=100"
        juju.cli(
            "deploy",
            str(charm_path),
            CSTR_APP,
            "--resource",
            f"{RESOURCE_NAME}={workload_bin}",
            "--constraints",
            constraints,
            include_model=True,
        )
        try:
            juju.wait(
                lambda s: (
                    CSTR_APP in s.apps
                    and len(s.apps[CSTR_APP].units) >= 1
                    and jubilant.all_active(s)
                ),
                timeout=900,
            )

            # STORED: the model echoes the constraints back on the application.
            stored = juju.cli("constraints", CSTR_APP, include_model=True)
            assert f"cores={CORES}" in stored, f"cores not stored: {stored!r}"
            assert "mem=512" in stored, f"mem not stored: {stored!r}"
            assert "arch=amd64" in stored, f"arch not stored: {stored!r}"

            # APPLIED: the provisioned machine's hardware reflects them. mem in
            # hardware-characteristics is reported in MB as a bare number or
            # with an 'M' suffix depending on version — compare numerically with
            # a tolerance (the provider may round up to a flavour/page boundary).
            unit = f"{CSTR_APP}/0"
            hw = _parse_hw(_machine_hardware_for(juju, CSTR_APP, unit))
            assert "cores" in hw, f"no cores in hardware: {hw}"
            assert int(hw["cores"]) >= CORES, f"cores not applied: {hw}"
            mem_val = int(hw.get("mem", "0").rstrip("M") or 0)
            assert mem_val >= MEM_MB, f"mem not applied (got {mem_val}M, want >= {MEM_MB}M): {hw}"
            assert hw.get("arch") == "amd64", f"arch not applied: {hw}"
            # cpu-power is UNSUPPORTED on LXD: warned + dropped, never applied to
            # the machine (contrast cores, which IS limits.cpu). Stored on the app
            # but absent from hardware-characteristics.
            assert "cpu-power" not in hw, f"cpu-power unexpectedly applied on LXD: {hw}"
        finally:
            # Force-remove so a wedged/oversized unit can never strand cleanup or
            # pollute later suites on the shared session model.
            juju.cli(
                "remove-application",
                CSTR_APP,
                "--no-prompt",
                "--force",
                "--destroy-storage",
                include_model=True,
            )
            juju.wait(lambda s: CSTR_APP not in s.apps, timeout=300)


@pytest.mark.xfail(
    reason="root-disk on localhost LXD is storage-pool-dependent: ApplyConstraints "
    "pins a root device to a pool whose name defaults to the literal 'default' "
    "(container.go:69-72) and Juju does not create/validate it, so LXD rejects at "
    "create-time ('Storage pool not found') unless a pool named 'default' exists "
    "(FINDINGS 3c). Stored either way; APPLIED is environment-dependent → flaky. A "
    "configured-pool / real-cloud run flips this.",
    strict=False,
)
@pytest.mark.mutates
class TestRootDiskConstraint:
    def test_root_disk_applied(self, juju: jubilant.Juju, charm_path, workload_bin):
        app = "norma-rootdisk"
        juju.cli(
            "deploy",
            str(charm_path),
            app,
            "--resource",
            f"{RESOURCE_NAME}={workload_bin}",
            "--constraints",
            "root-disk=8G",
            include_model=True,
        )
        try:
            juju.wait(
                lambda s: app in s.apps and len(s.apps[app].units) >= 1 and jubilant.all_active(s),
                timeout=900,
            )
            hw = _parse_hw(_machine_hardware_for(juju, app, f"{app}/0"))
            # XFAIL target: machine hardware reflects an >= 8G root disk. On a
            # pool-less localhost LXD the deploy errors before this; where a pool
            # exists it xpasses (strict=False tolerates both).
            assert "root-disk" in hw, f"no root-disk in hardware: {hw}"
        finally:
            juju.cli(
                "remove-application",
                app,
                "--no-prompt",
                "--force",
                "--destroy-storage",
                include_model=True,
            )
            juju.wait(lambda s: app not in s.apps, timeout=300)


# NOTE: a "cloud-only constraint" xfail trap was intentionally dropped. The
# obvious candidate (instance-type) is the WRONG example — code-verified vs 4.0,
# LXD DOES read instance-type into c.InstanceType (container.go:54-56). The
# genuinely-unsupported set (cpu-power, tags, container, allocate-public-ip,
# image-id) is dropped-not-applied, and "applied" for those is not observable via
# hardware-characteristics — so the unsupported case is calibrated deterministically
# inside TestConstraintsHonored (cpu-power stored-but-not-applied) instead.


@pytest.mark.xfail(
    reason="F15: charm lxd-profile.yaml is NOT applied to top-level instances on the "
    "localhost LXD cloud, and nested `--to lxd:N` did not provision (FINDINGS A.3). "
    "A non-nested LXD host / MAAS flips this. The charm ships lxd-profile.yaml as a "
    "ready regression trap for when 4.0 re-implements profile application.",
    strict=False,
)
class TestLxdProfileApplied:
    def test_profile_on_existing_machine(self, juju: jubilant.Juju):
        # Reuses the session app's machine — no new deploy. juju status reports
        # applied charm profiles under the machine's `lxd-profiles`.
        st = _status(juju)
        unit = next(iter(st["applications"][APP]["units"]))
        machine_id = st["applications"][APP]["units"][unit]["machine"]
        profiles = st["machines"][machine_id].get("lxd-profiles", {})
        # XFAIL target: a juju-<model>-<app> profile is present. Not applied on
        # localhost LXD today, so this fails here and flips when 4.0 re-implements.
        assert any(APP in name for name in profiles), (
            f"no charm lxd-profile applied on machine {machine_id}: {profiles}"
        )
