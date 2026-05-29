"""Workload abstraction module for the juju-norma machine calibration charm.

This module has ZERO dependency on the ops framework. It handles systemd unit
construction (the machine analogue of the k8s sibling's Pebble-layer build),
configuration validation, and workload-related constants. All functions are
independently testable with plain pytest (Constitution Principle II).

Kept 3.10-safe for cross-substrate reuse: use ``datetime.timezone.utc`` rather
than ``datetime.UTC``, and avoid 3.11+ stdlib.
"""

import json
import os

# --- Workload identity / paths (machine substrate) ---
# On machines the binary is delivered as a charm file resource and laid down on
# the host, then supervised by a charm-written systemd unit. These are the host
# paths — the machine analogues of the k8s sibling's in-container paths.
SERVICE_NAME = "norma"
DEFAULT_PORT = 8080
BINARY_PATH = "/usr/local/bin/norma"
SYSTEMD_UNIT_PATH = "/etc/systemd/system/norma.service"

STORAGE_PATH = "/var/lib/norma"
MARKER_FILE = "calibration-marker.json"
HEALTH_FLAG_FILE = "/var/lib/norma/norma-unhealthy"

STORAGE_CONFIG = {
    "data": {"path": STORAGE_PATH, "marker": MARKER_FILE},
}

# Test ledger + defer flag. Kept under /tmp (writable without root) so unit
# tests are host-safe and behaviour matches the k8s sibling. Note (KB4): on a
# machine these survive agent restarts but not a host reboot — contrast with
# the k8s sibling where they reset on every pod recreation.
LEDGER_FILE = "/tmp/norma-event-ledger.json"
DEFER_FLAG_FILE = "/tmp/norma-defer-armed"

# Events the defer-gate (F18) never defers: update-status is high-frequency, and
# relation-broken re-emission fails because the relation is already gone.
DEFER_SKIP_EVENTS = ("update-status", "relation-broken")


def read_event_ledger() -> list[dict]:
    """Read the event ledger from the host filesystem.

    Returns an empty list if the file doesn't exist or is corrupt.
    """
    try:
        with open(LEDGER_FILE) as f:
            return json.loads(f.read())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def write_event_ledger(ledger: list[dict]) -> None:
    """Write the event ledger to the host filesystem."""
    os.makedirs(os.path.dirname(LEDGER_FILE), exist_ok=True)
    with open(LEDGER_FILE, "w") as f:
        f.write(json.dumps(ledger))


def read_defer_armed() -> bool:
    """Read the deferral arming flag from disk."""
    try:
        with open(DEFER_FLAG_FILE) as f:
            return f.read().strip() == "true"
    except FileNotFoundError:
        return False


def write_defer_armed(armed: bool) -> None:
    """Persist the deferral arming flag to disk."""
    os.makedirs(os.path.dirname(DEFER_FLAG_FILE), exist_ok=True)
    with open(DEFER_FLAG_FILE, "w") as f:
        f.write("true" if armed else "false")


def validate_config(config: dict) -> tuple[bool, str]:
    """Validate charm configuration values.

    Returns ``(True, "")`` if valid, or ``(False, error_message)`` if invalid.
    """
    cal_string = config.get("calibration_string", "default")
    if not cal_string:
        return False, "calibration-string must not be empty"

    port = config.get("calibration_int", DEFAULT_PORT)
    if not (1 <= port <= 65535):
        return False, f"calibration-int must be between 1 and 65535, got {port}"

    cal_float = config.get("calibration_float", 1.0)
    if cal_float <= 0.0:
        return False, f"calibration-float must be > 0.0, got {cal_float}"

    return True, ""


def build_systemd_unit(port: int, version: str, env: dict[str, str] | None = None) -> str:
    """Build the systemd unit-file text for the Norma workload.

    This is the machine analogue of the k8s sibling's ``build_pebble_layer``:
    a pure function turning primitives (port, version, env) into the unit the
    charm writes to ``SYSTEMD_UNIT_PATH``. No ops, no host I/O.

    Args:
        port: TCP port the workload HTTP server listens on.
        version: Charm/workload version string, exported as ``VERSION``.
        env: Extra environment variables to inject into the service.

    Returns:
        The full unit-file contents as a string.
    """
    environment = {
        "PORT": str(port),
        "VERSION": version,
        "HEALTH_FLAG_FILE": HEALTH_FLAG_FILE,
        **(env or {}),
    }
    env_lines = "\n".join(f'Environment="{k}={v}"' for k, v in environment.items())
    return (
        "[Unit]\n"
        "Description=Norma calibration workload\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={BINARY_PATH} --port {port}\n"
        f"{env_lines}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
