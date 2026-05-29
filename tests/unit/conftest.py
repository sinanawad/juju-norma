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

import ops.testing
import pytest

import norma
import workload_driver


@pytest.fixture(autouse=True)
def _no_host_side_effects(tmp_path, monkeypatch):
    def _fake_run(argv, **kwargs):
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(workload_driver.subprocess, "run", _fake_run)
    monkeypatch.setattr(norma, "LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setattr(norma, "DEFER_FLAG_FILE", str(tmp_path / "defer"))


@pytest.fixture
def norma_bin(tmp_path):
    """Empty placeholder norma-bin file resource.

    A deployed charm always has the file resource declared; an empty file is the
    realistic "not yet attached" placeholder (resource-get returns a zero-byte
    file), which the charm treats as "binary not delivered". Any event that runs
    the reconciler calls ``resources.fetch("norma-bin")``, so State for such an
    event must declare this resource.
    """
    placeholder = tmp_path / "norma-bin"
    placeholder.write_bytes(b"")
    return ops.testing.Resource(name="norma-bin", path=placeholder)
