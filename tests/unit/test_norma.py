"""Unit tests for the ops-free workload module (src/norma.py).

Plain pytest, zero ops dependency — validates that the workload module is
independently testable per Constitution Principle II.
"""

import norma


class TestConstants:
    """Verify the machine-substrate constants are defined."""

    def test_service_name(self):
        assert norma.SERVICE_NAME == "norma"

    def test_default_port(self):
        assert norma.DEFAULT_PORT == 8080

    def test_binary_path(self):
        assert norma.BINARY_PATH == "/usr/local/bin/norma"

    def test_systemd_unit_path(self):
        assert norma.SYSTEMD_UNIT_PATH == "/etc/systemd/system/norma.service"

    def test_storage_path(self):
        assert norma.STORAGE_PATH == "/var/lib/norma"

    def test_storage_config(self):
        assert norma.STORAGE_CONFIG["data"]["path"] == norma.STORAGE_PATH
        assert norma.STORAGE_CONFIG["data"]["marker"] == norma.MARKER_FILE


class TestValidateConfig:
    """Configuration validation logic."""

    def test_valid_defaults(self):
        valid, msg = norma.validate_config(
            {
                "calibration_string": "default",
                "calibration_int": 8080,
                "calibration_float": 1.0,
                "calibration_bool": True,
            }
        )
        assert valid is True
        assert msg == ""

    def test_empty_string_invalid(self):
        valid, msg = norma.validate_config(
            {"calibration_string": "", "calibration_int": 8080, "calibration_float": 1.0}
        )
        assert valid is False
        assert "calibration-string" in msg

    def test_port_zero_invalid(self):
        valid, msg = norma.validate_config(
            {"calibration_string": "x", "calibration_int": 0, "calibration_float": 1.0}
        )
        assert valid is False
        assert "calibration-int" in msg

    def test_port_too_high_invalid(self):
        valid, msg = norma.validate_config(
            {"calibration_string": "x", "calibration_int": 99999, "calibration_float": 1.0}
        )
        assert valid is False
        assert "65535" in msg

    def test_negative_float_invalid(self):
        valid, msg = norma.validate_config(
            {"calibration_string": "x", "calibration_int": 8080, "calibration_float": -1.0}
        )
        assert valid is False
        assert "calibration-float" in msg

    def test_port_boundaries_valid(self):
        for port in (1, 65535):
            valid, _ = norma.validate_config(
                {"calibration_string": "x", "calibration_int": port, "calibration_float": 1.0}
            )
            assert valid is True


class TestBuildSystemdUnit:
    """Pure systemd unit-file construction (machine analogue of build_pebble_layer)."""

    def test_has_all_sections(self):
        unit = norma.build_systemd_unit(8080, "1.0.0")
        assert "[Unit]" in unit
        assert "[Service]" in unit
        assert "[Install]" in unit

    def test_execstart_uses_binary_and_port(self):
        unit = norma.build_systemd_unit(9090, "1.0.0")
        assert f"ExecStart={norma.BINARY_PATH} --port 9090" in unit

    def test_environment_lines(self):
        unit = norma.build_systemd_unit(8080, "2.3.4")
        assert 'Environment="PORT=8080"' in unit
        assert 'Environment="VERSION=2.3.4"' in unit
        assert f'Environment="HEALTH_FLAG_FILE={norma.HEALTH_FLAG_FILE}"' in unit

    def test_extra_env_injected(self):
        unit = norma.build_systemd_unit(8080, "1.0.0", {"FOO": "bar"})
        assert 'Environment="FOO=bar"' in unit

    def test_restart_policy_and_install_target(self):
        unit = norma.build_systemd_unit(8080, "1.0.0")
        assert "Restart=on-failure" in unit
        assert "WantedBy=multi-user.target" in unit


class TestLedgerPersistence:
    """Event ledger + defer flag file persistence."""

    def test_read_empty_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(norma, "LEDGER_FILE", str(tmp_path / "ledger.json"))
        assert norma.read_event_ledger() == []

    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(norma, "LEDGER_FILE", str(tmp_path / "ledger.json"))
        entries = [{"event_name": "install", "timestamp": "t", "unit_name": "u/0", "extra": {}}]
        norma.write_event_ledger(entries)
        assert norma.read_event_ledger() == entries

    def test_corrupt_json_reads_empty(self, tmp_path, monkeypatch):
        ledger = tmp_path / "ledger.json"
        ledger.write_text("not json{{{")
        monkeypatch.setattr(norma, "LEDGER_FILE", str(ledger))
        assert norma.read_event_ledger() == []

    def test_defer_flag_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(norma, "DEFER_FLAG_FILE", str(tmp_path / "defer"))
        assert norma.read_defer_armed() is False
        norma.write_defer_armed(True)
        assert norma.read_defer_armed() is True
        norma.write_defer_armed(False)
        assert norma.read_defer_armed() is False
