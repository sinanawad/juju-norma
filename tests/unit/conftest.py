"""Shared guards for the unit suite.

CRITICAL host-safety invariant: unit tests MUST NEVER touch real host systemd.
The charm constructs the real ``SystemdDriver`` (whose default paths are host
paths), and handlers like ``_on_stop``/``_on_remove`` call ``systemctl``. Firing
those events in Scenario would otherwise run ``systemctl stop/disable norma`` on
the developer's machine and trip a polkit prompt for ``norma.service``.

This autouse fixture stubs ``subprocess.run`` so no real ``systemctl`` can run
under any test, and isolates the on-disk ledger/defer files to a tmp dir.
Driver tests that assert on ``systemctl`` argv re-patch ``subprocess.run``
themselves, which overrides this default for the duration of that test.
"""

import types

import pytest

import norma
import workload_driver


@pytest.fixture(autouse=True)
def _no_host_side_effects(tmp_path, monkeypatch):
    def _fake_run(argv, **kwargs):
        return types.SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(workload_driver.subprocess, "run", _fake_run)
    monkeypatch.setattr(norma, "LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setattr(norma, "DEFER_FLAG_FILE", str(tmp_path / "defer"))
