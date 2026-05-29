#!/usr/bin/env python3
"""Minimal machine SUBORDINATE calibration charm (F14).

Deploys onto a principal's machine via the container-scoped ``juju-info``
relation and reports its principal. Holistic reconciler; status reflects whether
the principal relation is established.
"""

import logging

import ops

logger = logging.getLogger(__name__)


class NormaSubordinateCharm(ops.CharmBase):
    """Subordinate that colocates with a principal via juju-info."""

    def __init__(self, framework: ops.Framework) -> None:
        super().__init__(framework)
        for evt in (
            self.on.install,
            self.on.start,
            self.on.config_changed,
            self.on.update_status,
            self.on.juju_info_relation_created,
            self.on.juju_info_relation_joined,
            self.on.juju_info_relation_changed,
            self.on.juju_info_relation_broken,
        ):
            self.framework.observe(evt, self._reconcile)
        self.framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)
        self.framework.observe(self.on.get_principal_action, self._on_get_principal_action)

    def _reconcile(self, event: ops.EventBase) -> None:
        logger.info("subordinate reconcile on %s (%s)", self.unit.name, type(event).__name__)

    def _on_collect_unit_status(self, event: ops.CollectStatusEvent) -> None:
        if self._principal_unit() is None:
            event.add_status(ops.WaitingStatus("Waiting for principal (juju-info)"))
            return
        event.add_status(ops.ActiveStatus())

    def _on_get_principal_action(self, event: ops.ActionEvent) -> None:
        principal = self._principal_unit()
        event.set_results(
            {
                "subordinate-unit": self.unit.name,
                "principal": principal or "none",
                "related": str(principal is not None).lower(),
            }
        )

    def _principal_unit(self) -> str | None:
        relation = self.model.get_relation("juju-info")
        if not relation:
            return None
        for unit in relation.units:
            return unit.name
        return None


if __name__ == "__main__":  # pragma: nocover
    ops.main(NormaSubordinateCharm)
