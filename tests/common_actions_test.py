"""Unit tests for sonic_platform_base.sonic_thermal_control.common_actions."""

import pytest

from sonic_platform_base.sonic_thermal_control import common_actions
from sonic_platform_base.sonic_thermal_control.common_actions import (
    FanControlError, SetFanSpeedAction, SetMaxFanSpeedAction, ThermalControlAlgorithmAction,
    get_fans, set_all_fan_speeds)
from sonic_platform_base.sonic_thermal_control.common_infos import (
    FanDrawerInfo, FanInfo, ThermalInfo)
from sonic_platform_base.sonic_thermal_control.thermal_json_object import ThermalJsonObject

CONFIG = {
    "fan_limits": {"min": 30, "max": 100},
    "pid_domains": {"asic": {"KP": 10, "KI": 0.1, "KD": 5}},
}


class FakeFan:
    def __init__(self, max_speed=100, accepts=True, raises=False):
        self.speed = None
        self.max_speed_set = None
        self._max_speed, self._accepts, self._raises = max_speed, accepts, raises

    def set_speed(self, speed):
        if self._raises:
            raise RuntimeError("i2c timeout")
        self.speed = speed
        return self._accepts

    def get_max_speed(self):
        return self._max_speed

    def set_max_speed(self, value):
        self.max_speed_set = value


class FakeThermal:
    def __init__(self, temperature=40.0, setpoint=80.0, domain='asic', pid=True, name='t'):
        self._temperature, self._setpoint = temperature, setpoint
        self._domain, self._pid, self._name = domain, pid, name

    def get_temperature(self): return self._temperature
    def get_pid_setpoint(self): return self._setpoint
    def get_pid_domain(self): return self._domain
    def is_controlled_by_pid(self): return self._pid
    def get_name(self): return self._name


class FakeManager:
    def __init__(self, interval=5): self._interval = interval
    def get_interval(self): return self._interval


class FakeDrawerInfo:
    def __init__(self, fans): self._fans = fans
    def get_fans(self): return self._fans


class FakeFanInfo:
    def __init__(self, fans): self._fans = set(fans)
    def get_presence_fans(self): return self._fans


class FakeThermalInfo:
    def __init__(self, thermals, manager=None):
        self._thermals, self._manager = thermals, manager or FakeManager()
    def get_thermals(self): return self._thermals
    def get_thermal_manager(self): return self._manager


def info_dict(fans=None, thermals=None, use_fan_info=False):
    d = {}
    if fans is not None:
        key = FanInfo.INFO_NAME if use_fan_info else FanDrawerInfo.INFO_NAME
        d[key] = FakeFanInfo(fans) if use_fan_info else FakeDrawerInfo(fans)
    if thermals is not None:
        d[ThermalInfo.INFO_NAME] = FakeThermalInfo(thermals)
    return d


@pytest.fixture
def algo(monkeypatch):
    monkeypatch.setattr(common_actions, 'load_thermal_config', lambda *a, **k: CONFIG)
    action = ThermalControlAlgorithmAction()
    action.load_from_json({})
    return action


# --- registration -----------------------------------------------------------------------

@pytest.mark.parametrize("type_name, cls", [
    ('fan.all.set_speed', SetFanSpeedAction),
    ('fan.set_max_speed', SetMaxFanSpeedAction),
    ('thermal.control_algo', ThermalControlAlgorithmAction),
])
def test_action_is_registered(type_name, cls):
    assert ThermalJsonObject.get_type({'type': type_name}) is cls


# --- fan source -------------------------------------------------------------------------

def test_prefers_fan_drawer_info():
    fan = FakeFan()
    assert get_fans(info_dict(fans=[fan])) == [fan]


def test_falls_back_to_fan_info():
    fan = FakeFan()
    assert get_fans(info_dict(fans=[fan], use_fan_info=True)) == [fan]


def test_raises_when_no_fan_info_collected():
    with pytest.raises(FanControlError, match='add one to info_types'):
        get_fans({})


# --- set_all_fan_speeds -----------------------------------------------------------------

def test_sets_every_fan():
    fans = [FakeFan(), FakeFan()]
    set_all_fan_speeds(fans, 55)
    assert [f.speed for f in fans] == [55, 55]


def test_one_failing_fan_does_not_stop_the_others():
    good, bad = FakeFan(), FakeFan(raises=True)
    set_all_fan_speeds([bad, good], 70)
    assert good.speed == 70


def test_raises_when_no_fan_accepts():
    with pytest.raises(FanControlError, match='no fan accepted'):
        set_all_fan_speeds([FakeFan(accepts=False)], 70)


def test_raises_on_empty_fan_list():
    with pytest.raises(FanControlError, match='no fans available'):
        set_all_fan_speeds([], 70)


# --- simple actions ---------------------------------------------------------------------

def test_set_speed_action():
    action = SetFanSpeedAction()
    action.load_from_json({'speed': 100})
    fans = [FakeFan()]
    action.execute(info_dict(fans=fans))
    assert fans[0].speed == 100


@pytest.mark.parametrize("speed", [-1, 101, "fast", None])
def test_set_speed_rejects_bad_values(speed):
    with pytest.raises(ValueError):
        SetFanSpeedAction().load_from_json({'speed': speed})


def test_set_speed_requires_the_field():
    with pytest.raises(ValueError, match='missing speed'):
        SetFanSpeedAction().load_from_json({})


def test_set_max_speed_action_caps_without_driving():
    action = SetMaxFanSpeedAction()
    action.load_from_json({'max_speed': 75})
    fans = [FakeFan()]
    action.execute(info_dict(fans=fans))
    assert fans[0].max_speed_set == 75
    assert fans[0].speed is None


# --- PID control algorithm ----------------------------------------------------------------

def test_requires_configured_domains(monkeypatch):
    monkeypatch.setattr(common_actions, 'load_thermal_config', lambda *a, **k: {})
    with pytest.raises(ValueError, match='no pid_domains'):
        ThermalControlAlgorithmAction().load_from_json({})


def test_cool_switch_settles_at_the_minimum(algo):
    fans = [FakeFan()]
    algo.execute(info_dict(fans=fans, thermals=[FakeThermal(temperature=40.0, setpoint=80.0)]))
    assert fans[0].speed == 30


def test_hot_switch_drives_fans_up(algo):
    fans = [FakeFan()]
    algo.execute(info_dict(fans=fans, thermals=[FakeThermal(temperature=95.0, setpoint=80.0)]))
    assert fans[0].speed > 30


def test_honours_the_fan_max_speed_cap(algo):
    fans = [FakeFan(max_speed=75)]
    algo.execute(info_dict(fans=fans, thermals=[FakeThermal(temperature=150.0, setpoint=80.0)]))
    assert fans[0].speed <= 75


def test_ramp_converges_without_windup(algo):
    """A sustained overshoot then a return to target must come back down, not stay pinned."""
    fans = [FakeFan()]
    thermal = FakeThermal(temperature=95.0, setpoint=80.0)
    d = info_dict(fans=fans, thermals=[thermal])
    for _ in range(20):
        algo.execute(d)
    hot_speed = fans[0].speed

    thermal._temperature = 60.0
    for _ in range(40):
        algo.execute(d)
    assert fans[0].speed < hot_speed
    assert fans[0].speed == 30


def test_config_setpoint_overrides_the_sensor(monkeypatch):
    config = dict(CONFIG)
    config['pid_domains'] = {"asic": {"KP": 10, "KI": 0.1, "KD": 5, "setpoint": 50}}
    monkeypatch.setattr(common_actions, 'load_thermal_config', lambda *a, **k: config)
    action = ThermalControlAlgorithmAction()
    action.load_from_json({})

    fans = [FakeFan()]
    # 70C is under the sensor's own 80C target but over the configured 50C one
    action.execute(info_dict(fans=fans, thermals=[FakeThermal(temperature=70.0, setpoint=80.0)]))
    assert fans[0].speed > 30


def test_skips_thermals_not_under_pid_control(algo):
    fans = [FakeFan()]
    hot_but_excluded = FakeThermal(temperature=150.0, setpoint=80.0, pid=False)
    algo.execute(info_dict(fans=fans, thermals=[hot_but_excluded]))
    assert fans[0].speed == 100  # no usable domain -> fail-safe


def test_failure_falls_back_to_max_speed_without_raising(algo):
    fans = [FakeFan()]
    algo.execute(info_dict(fans=fans))  # thermal_info missing
    assert fans[0].speed == 100


def test_sensor_without_a_reading_is_ignored(algo):
    fans = [FakeFan()]
    algo.execute(info_dict(fans=fans, thermals=[
        FakeThermal(temperature=None), FakeThermal(temperature=95.0, setpoint=80.0)]))
    assert fans[0].speed > 30


def test_config_dir_can_be_overridden(tmp_path):
    """Lets the action be exercised off a switch, where the platform dir does not exist."""
    import json
    from sonic_platform_base.sonic_thermal_control import thermal_config
    (tmp_path / thermal_config.CONFIG_FILE_NAME).write_text(json.dumps(CONFIG))

    action = ThermalControlAlgorithmAction()
    action.load_from_json({'config_dir': str(tmp_path)})

    fans = [FakeFan()]
    action.execute(info_dict(fans=fans, thermals=[FakeThermal(temperature=95.0, setpoint=80.0)]))
    assert fans[0].speed > 30
