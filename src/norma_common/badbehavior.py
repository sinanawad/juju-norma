"""Bad-behavior test-bed: ops-free status-mode decisioning.

Ported (class-A) from the k8s sibling and made ops-free so it lives in
norma_common and is plain-pytest testable. The charm maps the returned
``(kind, message)`` to an ``ops`` status class; this module never imports ops.

Modes deliberately violate status/teardown conventions so the charm can be
deployed under each mode to calibrate Juju's handling. ``none`` is the
well-behaved baseline.
"""

import logging

logger = logging.getLogger(__name__)

BAD_BEHAVIOR_MODES: tuple[str, ...] = (
    "none",
    "active-with-message",
    "blocked-no-message",
    "stuck-maintenance",
    "status-churn",
    "hook-error",
    "secret-in-relation",
    "stuck-dying",
)

# Modes that drive a deliberate unit-status violation. The remainder
# (hook-error, secret-in-relation, stuck-dying) act on the reconcile/relation/
# teardown paths instead and leave unit status at the compliant baseline.
STATUS_OVERRIDE_MODES = frozenset(
    {"active-with-message", "blocked-no-message", "stuck-maintenance", "status-churn"}
)


def normalize_mode(raw: str | None) -> str:
    """Return a recognised mode, mapping unknown values to 'none' with a warning."""
    mode = str(raw or "none").strip()
    if mode not in BAD_BEHAVIOR_MODES:
        logger.warning(
            "Unknown bad-behavior-mode %r; falling back to 'none'. Valid: %s",
            mode,
            ", ".join(BAD_BEHAVIOR_MODES),
        )
        return "none"
    return mode


def status_for_mode(mode: str, port: int, ledger_len: int) -> tuple[str, str] | None:
    """Return the deliberately-bad ``(kind, message)`` for a status-override mode.

    ``kind`` is one of ``active|blocked|maintenance|waiting``. Returns ``None``
    when the mode does not override unit status (compliant baseline applies).

    Args:
        mode: the normalised bad-behavior mode.
        port: workload port (used by active-with-message to look "useful").
        ledger_len: event-ledger length, used as a free parity counter for churn.
    """
    if mode == "active-with-message":
        # Violation: active conventionally carries NO message.
        return "active", f"serving on port {port}"
    if mode == "blocked-no-message":
        # Violation: blocked MUST carry an actionable message.
        return "blocked", ""
    if mode == "stuck-maintenance":
        # Violation: maintenance is for bounded work, not an idle steady state.
        return "maintenance", "preparing calibration suite"
    if mode == "status-churn":
        # Violation: declared-state stability. Flip parity each reconcile.
        if ledger_len % 2 == 0:
            return "active", ""
        return "waiting", "waiting for nothing in particular"
    return None
