"""Idempotent environment setup for integration tests (machine / LXD).

Installs the LXD + juju snaps and bootstraps an LXD controller when
``SETUP_ENVIRONMENT=1`` is set. Pure subprocess orchestration — no pytest or
jubilant dependency, so it can also be used as a standalone script.

Machine-substrate sibling of juju-norma-k8s's setup_env.py: LXD instead of
microk8s (no add-k8s / kubeconfig / registry addons), targeting Juju 4.0+.
"""

import logging
import subprocess

logger = logging.getLogger(__name__)

SNAP_TIMEOUT = 300  # seconds
BOOTSTRAP_TIMEOUT = 1200  # CI runners are slow; bootstrap pulls agent binaries


class SetupError(Exception):
    """Unrecoverable environment setup failure."""


def _run(cmd: list[str], *, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess:
    logger.info("$ %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)


# ------------------------------------------------------------------
# Snap helpers
# ------------------------------------------------------------------


def is_snap_installed(name: str) -> bool:
    """Return True if the snap *name* is installed."""
    result = _run(["snap", "list", name], check=False)
    return result.returncode == 0


def install_snap(name: str, channel: str, *, classic: bool = True) -> None:
    """Install a snap if it is not already present."""
    if is_snap_installed(name):
        logger.info("snap %s already installed", name)
        return
    cmd = ["sudo", "snap", "install", name, "--channel", channel]
    if classic:
        cmd.append("--classic")
    _run(cmd, timeout=SNAP_TIMEOUT)
    logger.info("snap %s installed from %s", name, channel)


# ------------------------------------------------------------------
# LXD
# ------------------------------------------------------------------


def ensure_lxd(channel: str = "5.21/stable") -> None:
    """Install LXD and initialise it non-interactively (idempotent)."""
    install_snap("lxd", channel, classic=False)
    _run(["sudo", "lxd", "waitready"], timeout=SNAP_TIMEOUT)
    # `lxd init --auto` is idempotent — re-running on an initialised daemon is a
    # no-op for the default profile/storage/network.
    _run(["sudo", "lxd", "init", "--auto"], timeout=SNAP_TIMEOUT, check=False)
    logger.info("LXD ready")


# ------------------------------------------------------------------
# Juju controller
# ------------------------------------------------------------------


def is_controller_bootstrapped(controller: str, juju_cli: str = "juju") -> bool:
    """Return True if *controller* already exists."""
    result = _run([juju_cli, "show-controller", controller], check=False)
    return result.returncode == 0


def destroy_controller(controller: str, juju_cli: str = "juju") -> None:
    """Destroy a Juju controller and all its models."""
    if not is_controller_bootstrapped(controller, juju_cli):
        logger.info("controller %s does not exist, nothing to destroy", controller)
        return
    logger.info("destroying controller %s", controller)
    subprocess.run(
        [
            juju_cli,
            "destroy-controller",
            controller,
            "--destroy-all-models",
            "--no-prompt",
            "--destroy-storage",
        ],
        timeout=BOOTSTRAP_TIMEOUT,
        check=True,
    )
    logger.info("controller %s destroyed", controller)


def bootstrap_controller(
    controller: str = "lxd",
    cloud: str = "localhost",
    juju_cli: str = "juju",
    *,
    force_fresh: bool = False,
) -> None:
    """Bootstrap a Juju controller on LXD (cloud name ``localhost``)."""
    if force_fresh:
        destroy_controller(controller, juju_cli)
    elif is_controller_bootstrapped(controller, juju_cli):
        logger.info("controller %s already bootstrapped", controller)
        return
    logger.info("$ %s bootstrap %s %s", juju_cli, cloud, controller)
    subprocess.run(
        [juju_cli, "bootstrap", cloud, controller],
        timeout=BOOTSTRAP_TIMEOUT,
        check=True,
    )
    logger.info("controller %s bootstrapped", controller)


# ------------------------------------------------------------------
# Top-level orchestrator
# ------------------------------------------------------------------


def check_prerequisites(juju_cli: str = "juju") -> list[str]:
    """Return a list of missing prerequisites (empty = all OK)."""
    missing = []
    if not is_snap_installed("lxd"):
        missing.append("lxd snap")
    result = _run([juju_cli, "version"], check=False)
    if result.returncode != 0:
        missing.append(f"juju CLI ({juju_cli})")
    return missing


def ensure_environment(
    *,
    juju_channel: str = "4.0/stable",
    lxd_channel: str = "5.21/stable",
    controller: str = "lxd",
    juju_cli: str = "juju",
) -> str:
    """Set up the full integration test environment. Returns the juju CLI path."""
    try:
        ensure_lxd(lxd_channel)
        install_snap("juju", juju_channel, classic=True)
        bootstrap_controller(controller, juju_cli=juju_cli)
    except subprocess.CalledProcessError as exc:
        raise SetupError(
            f"Command failed: {exc.cmd!r}\nstdout: {exc.stdout}\nstderr: {exc.stderr}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SetupError(f"Command timed out: {exc.cmd!r}") from exc
    return juju_cli
