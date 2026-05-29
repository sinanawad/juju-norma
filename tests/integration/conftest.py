"""Integration test fixtures for the juju-norma MACHINE charm (jubilant-based).

Machine-substrate sibling of juju-norma-k8s's conftest: the workload is a charm
FILE RESOURCE (norma-bin) on an LXD controller, not an OCI image on microk8s.
There is no OCI-deploy race, so no deploy retry. Targets Juju 4.0+.

Usage modes:
    1. Existing deployment (fastest local iteration):
       JUJU_MODEL=norma-test make integration
    2. Fresh temp model (default; requires a working juju + LXD controller):
       make integration
    3. Custom juju binary with a named controller:
       JUJU_CLI=~/go/bin/juju JUJU_CONTROLLER=lxd make integration
    4. Full auto-setup on a fresh machine:
       SETUP_ENVIRONMENT=1 make integration

Environment variables:
    SETUP_ENVIRONMENT   "1" → auto-install snaps (lxd, juju) + bootstrap controller.
    FRESH_CONTROLLER    "1" → destroy + re-bootstrap the controller first.
    JUJU_CHANNEL        Juju snap channel (default "4.0/stable").
    LXD_CHANNEL         LXD snap channel (default "5.21/stable").
    JUJU_CLI            Path to juju binary (default "juju").
    JUJU_MODEL          Reuse an existing model (skip deploy).
    JUJU_CONTROLLER     Controller name (default "lxd").
    JUJU_CLOUD          Cloud name for add-model (default "localhost").
    CHARM_PATH          Path to the principal .charm (or a dir containing one).
    SUBORDINATE_CHARM_PATH  Path to the subordinate .charm (or a dir).
    NORMA_BIN           Path to the built workload binary (default <repo>/norma).
    KEEP_MODEL          "1" → keep the model after tests (debugging).
"""

import logging
import os
import pathlib
import uuid

import jubilant
import pytest

from .setup_env import (
    SetupError,
    bootstrap_controller,
    check_prerequisites,
    ensure_environment,
)

logger = logging.getLogger(__name__)

APP = "juju-norma"
SUBORDINATE_APP = "juju-norma-subordinate"
RESOURCE_NAME = "norma-bin"
CHARM_FILE_GLOB = "juju-norma_*.charm"
SUBORDINATE_GLOB = "juju-norma-subordinate_*.charm"
CONTROLLER_DEFAULT = "lxd"
CLOUD_DEFAULT = "localhost"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def pytest_addoption(parser):
    parser.addoption(
        "--run-destructive",
        action="store_true",
        default=False,
        help="Run destructive tests (force-remove application, teardown).",
    )


# ------------------------------------------------------------------
# Artifact discovery
# ------------------------------------------------------------------


def _find_charm(env_var: str, glob: str, search_root: pathlib.Path) -> pathlib.Path:
    override = _env(env_var)
    if override:
        p = pathlib.Path(override)
        if p.is_dir():
            matches = sorted(p.glob(glob))
            assert matches, f"No {glob} found in {p}"
            return matches[-1]
        return p
    matches = sorted(search_root.glob(glob))
    assert matches, f"No {glob} found under {search_root}; run charmcraft pack"
    return matches[-1]


@pytest.fixture(scope="session")
def charm_path() -> pathlib.Path:
    """Locate the built principal .charm."""
    return _find_charm("CHARM_PATH", CHARM_FILE_GLOB, REPO_ROOT)


@pytest.fixture(scope="session")
def subordinate_charm_path() -> pathlib.Path | None:
    """Locate the built subordinate .charm, or None if not packed."""
    override = _env("SUBORDINATE_CHARM_PATH")
    search = pathlib.Path(override) if override else (REPO_ROOT / "subordinate")
    if search.is_file():
        return search
    matches = sorted(search.glob(SUBORDINATE_GLOB)) if search.is_dir() else []
    return matches[-1] if matches else None


@pytest.fixture(scope="session")
def workload_bin() -> pathlib.Path:
    """Locate the built workload binary (the norma-bin file resource)."""
    p = pathlib.Path(_env("NORMA_BIN", str(REPO_ROOT / "norma")))
    assert p.exists(), f"workload binary not found at {p}; run make build-workload"
    return p


# ------------------------------------------------------------------
# Environment / controller
# ------------------------------------------------------------------


@pytest.fixture(scope="session")
def environment_ready():
    """Ensure prerequisites; optionally auto-install + bootstrap."""
    juju_cli = _env("JUJU_CLI", "juju")
    controller = _env("JUJU_CONTROLLER", CONTROLLER_DEFAULT)

    if _env("SETUP_ENVIRONMENT") == "1":
        try:
            ensure_environment(
                juju_channel=_env("JUJU_CHANNEL", "4.0/stable"),
                lxd_channel=_env("LXD_CHANNEL", "5.21/stable"),
                controller=controller,
                juju_cli=juju_cli,
            )
        except SetupError as exc:
            pytest.fail(f"Environment setup failed: {exc}")
    else:
        missing = check_prerequisites(juju_cli)
        if missing:
            pytest.skip(f"Prerequisites missing (set SETUP_ENVIRONMENT=1): {', '.join(missing)}")

    if _env("FRESH_CONTROLLER") == "1":
        bootstrap_controller(
            controller=controller,
            cloud=_env("JUJU_CLOUD", CLOUD_DEFAULT),
            juju_cli=juju_cli,
            force_fresh=True,
        )


# ------------------------------------------------------------------
# Session-scoped Juju with the charm deployed
# ------------------------------------------------------------------


@pytest.fixture(scope="session")
def juju(environment_ready, charm_path, workload_bin):
    """A jubilant.Juju with the principal charm deployed and active/idle.

    Modes mirror the sibling: reuse JUJU_MODEL, manual lifecycle for a custom
    JUJU_CLI (temp_model ignores cli_binary), else jubilant.temp_model().
    """
    model = _env("JUJU_MODEL")
    cli = _env("JUJU_CLI")
    controller = _env("JUJU_CONTROLLER", CONTROLLER_DEFAULT)
    cloud = _env("JUJU_CLOUD", CLOUD_DEFAULT)
    keep = _env("KEEP_MODEL") == "1"
    resources = {RESOURCE_NAME: str(workload_bin)}

    if model:
        j = jubilant.Juju(model=model, cli_binary=cli or "juju")
        assert APP in j.status().apps, f"{APP} not found in model {model}"
        yield j
        return

    # Manual model lifecycle. Used whenever a non-default juju binary is given,
    # OR for the default path too — jubilant.temp_model()'s teardown does a plain
    # destroy-model, which HANGS forever if a unit is wedged in error (the F20/F21
    # bad-behavior tests deliberately drive errors). We always tear down with
    # --force --no-wait so an errored unit can never wedge cleanup.
    binary = cli or "juju"
    model_name = f"norma-{uuid.uuid4().hex[:8]}"
    no_model = jubilant.Juju(cli_binary=binary)
    no_model.cli("add-model", model_name, cloud, "-c", controller, include_model=False)
    j = jubilant.Juju(model=model_name, cli_binary=binary)
    try:
        j.deploy(str(charm_path), app=APP, resources=resources)
        j.wait(jubilant.all_active, timeout=600)
        yield j
    finally:
        if not keep:
            no_model.cli(
                "destroy-model",
                model_name,
                "--no-prompt",
                "--force",
                "--no-wait",
                "--destroy-storage",
                include_model=False,
            )


@pytest.fixture(autouse=True)
def _no_cascade(juju, request):
    """Fail-fast + self-heal so one wedged test does not cascade.

    The session ``juju`` model is shared, so a test that leaves the unit
    not-active (e.g. a config-recovery that didn't reconverge) would otherwise
    cause unrelated downstream tests to fail with confusing assertions. After
    each test we ensure the unit is active again, resetting the config knobs the
    suites toggle if needed. Tests that intentionally end in non-active state
    mark themselves ``@pytest.mark.mutates`` to opt out.
    """
    yield
    if "mutates" in request.keywords:
        return
    try:
        juju.wait(jubilant.all_active, timeout=120)
    except jubilant.TimeoutError:
        juju.config(APP, reset=["calibration-int", "calibration-string", "bad-behavior-mode"])
        juju.wait(jubilant.all_active, timeout=180)
