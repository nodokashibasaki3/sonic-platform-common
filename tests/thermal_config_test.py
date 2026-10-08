"""Unit tests for sonic_platform_base.sonic_thermal_control.thermal_config."""

import json
import os

import pytest

from sonic_platform_base.sonic_thermal_control import thermal_config
from sonic_platform_base.sonic_thermal_control.thermal_config import (
    ThermalConfigError,
    get_domain_setpoint,
    get_fan_limits,
    get_interval,
    get_pid_domains,
    load_thermal_config,
)

VALID = {
    "interval": 5,
    "fan_limits": {"min": 30, "max": 100},
    "pid_domains": {
        "asic": {"KP": 10, "KI": 0.1, "KD": 5, "setpoint": 85},
        "main": {"KP": 5, "KI": 0.05, "KD": 2.5},
    },
}


def write_config(tmp_path, config):
    path = tmp_path / thermal_config.CONFIG_FILE_NAME
    path.write_text(config if isinstance(config, str) else json.dumps(config))
    return str(tmp_path)


def test_missing_file_returns_empty_dict(tmp_path):
    assert load_thermal_config(str(tmp_path)) == {}


def test_missing_file_is_not_an_error(tmp_path):
    config = load_thermal_config(str(tmp_path))
    assert get_interval(config) == thermal_config.DEFAULT_INTERVAL
    assert get_fan_limits(config) == (thermal_config.DEFAULT_FAN_MIN_SPEED,
                                      thermal_config.DEFAULT_FAN_MAX_SPEED)
    assert get_pid_domains(config) == {}


def test_loads_a_valid_config(tmp_path):
    config = load_thermal_config(write_config(tmp_path, VALID))
    assert get_interval(config) == 5
    assert get_fan_limits(config) == (30, 100)
    assert sorted(get_pid_domains(config)) == ["asic", "main"]


def test_setpoint_override_and_absence(tmp_path):
    config = load_thermal_config(write_config(tmp_path, VALID))
    assert get_domain_setpoint(config, "asic") == 85
    assert get_domain_setpoint(config, "main") is None
    assert get_domain_setpoint(config, "nonexistent") is None


def test_config_path_uses_default_dir():
    assert thermal_config.config_path().startswith(thermal_config.DEFAULT_CONFIG_DIR)


def test_malformed_json_is_an_error(tmp_path):
    with pytest.raises(ThermalConfigError):
        load_thermal_config(write_config(tmp_path, "{not json"))


def test_top_level_must_be_an_object(tmp_path):
    with pytest.raises(ThermalConfigError):
        load_thermal_config(write_config(tmp_path, [1, 2, 3]))


@pytest.mark.parametrize("interval", [0, -1, "fast"])
def test_rejects_bad_interval(tmp_path, interval):
    with pytest.raises(ThermalConfigError):
        load_thermal_config(write_config(tmp_path, {"interval": interval}))


def test_rejects_inverted_fan_limits(tmp_path):
    with pytest.raises(ThermalConfigError):
        load_thermal_config(write_config(tmp_path, {"fan_limits": {"min": 90, "max": 40}}))


def test_rejects_non_numeric_fan_limit(tmp_path):
    with pytest.raises(ThermalConfigError):
        load_thermal_config(write_config(tmp_path, {"fan_limits": {"min": "low"}}))


def test_rejects_missing_gain(tmp_path):
    bad = {"pid_domains": {"asic": {"KP": 1, "KI": 1}}}
    with pytest.raises(ThermalConfigError):
        load_thermal_config(write_config(tmp_path, bad))


def test_rejects_negative_gain(tmp_path):
    bad = {"pid_domains": {"asic": {"KP": -1, "KI": 1, "KD": 1}}}
    with pytest.raises(ThermalConfigError):
        load_thermal_config(write_config(tmp_path, bad))


def test_rejects_all_zero_gains(tmp_path):
    bad = {"pid_domains": {"asic": {"KP": 0, "KI": 0, "KD": 0}}}
    with pytest.raises(ThermalConfigError):
        load_thermal_config(write_config(tmp_path, bad))


def test_rejects_non_numeric_setpoint(tmp_path):
    bad = {"pid_domains": {"asic": {"KP": 1, "KI": 1, "KD": 1, "setpoint": "hot"}}}
    with pytest.raises(ThermalConfigError):
        load_thermal_config(write_config(tmp_path, bad))


def test_partial_config_keeps_defaults_for_absent_keys(tmp_path):
    config = load_thermal_config(write_config(tmp_path, {"interval": 5}))
    assert get_interval(config) == 5
    assert get_fan_limits(config) == (thermal_config.DEFAULT_FAN_MIN_SPEED,
                                      thermal_config.DEFAULT_FAN_MAX_SPEED)


# JSON true/false load as Python bool, which is a subclass of int; a typo like "KP": true
# must not pass for the number 1.

@pytest.mark.parametrize("config", [
    {"interval": True},
    {"fan_limits": {"min": False, "max": 100}},
    {"fan_limits": {"min": 30, "max": True}},
    {"pid_domains": {"asic": {"KP": True, "KI": 0.1, "KD": 5}}},
    {"pid_domains": {"asic": {"KP": True, "KI": False, "KD": False}}},
    {"pid_domains": {"asic": {"KP": 10, "KI": 0.1, "KD": 5, "setpoint": True}}},
], ids=["interval", "fan-min", "fan-max", "one-gain", "all-gains", "setpoint"])
def test_rejects_booleans_where_numbers_are_expected(tmp_path, config):
    with pytest.raises(ThermalConfigError):
        load_thermal_config(write_config(tmp_path, config))
