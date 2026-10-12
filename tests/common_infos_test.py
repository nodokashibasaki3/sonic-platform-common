"""Unit tests for sonic_platform_base.sonic_thermal_control.common_infos."""

import pytest

from sonic_platform_base.sonic_thermal_control.common_infos import (
    ChassisInfo, FanDrawerInfo, FanInfo, PsuInfo, ThermalInfo)
from sonic_platform_base.sonic_thermal_control.thermal_json_object import ThermalJsonObject


class FakeFan:
    def __init__(self, present=True, healthy=True):
        self._present, self._healthy = present, healthy

    def get_presence(self):
        return self._present

    def get_status(self):
        return self._healthy


class FakeDrawer:
    def __init__(self, present=True):
        self._present = present

    def get_presence(self):
        return self._present


class FakeThermal:
    def __init__(self, temperature=40.0, high=80.0, critical=95.0):
        self._temperature, self._high, self._critical = temperature, high, critical

    def get_temperature(self):
        return self._temperature

    def get_high_threshold(self):
        return self._high

    def get_high_critical_threshold(self):
        return self._critical


class FakeSfp:
    def __init__(self, thermals=()):
        self._thermals = list(thermals)

    def get_all_thermals(self):
        return self._thermals


class FakePsu:
    def __init__(self, present=True, thermals=(), fans=()):
        self._present, self._thermals, self._fans = present, list(thermals), list(fans)

    def get_presence(self):
        return self._present

    def get_all_thermals(self):
        return self._thermals

    def get_all_fans(self):
        return self._fans


class FakeChassis:
    def __init__(self, fans=(), drawers=(), thermals=(), sfps=(), psus=(), manager=None):
        self._fans, self._drawers = list(fans), list(drawers)
        self._thermals, self._sfps, self._psus = list(thermals), list(sfps), list(psus)
        self._manager = manager

    def get_all_fans(self):
        return self._fans

    def get_all_fan_drawers(self):
        return self._drawers

    def get_all_thermals(self):
        return self._thermals

    def get_all_sfps(self):
        return self._sfps

    def get_all_psus(self):
        return self._psus

    def get_thermal_manager(self):
        return self._manager


# --- registration -----------------------------------------------------------------------

@pytest.mark.parametrize("type_name, cls", [
    ('fan_info', FanInfo),
    ('fan_drawer_info', FanDrawerInfo),
    ('thermal_info', ThermalInfo),
    ('psu_info', PsuInfo),
    ('chassis_info', ChassisInfo),
])
def test_info_type_is_registered(type_name, cls):
    assert ThermalJsonObject.get_type({'type': type_name}) is cls


@pytest.mark.parametrize("cls", [FanInfo, FanDrawerInfo, ThermalInfo, PsuInfo, ChassisInfo])
def test_info_name_and_type_alias_agree(cls):
    assert cls.INFO_NAME == cls.INFO_TYPE


def test_registers_exactly_the_five_info_types():
    # Scoped to this module's own classes: the registry is global, so other common modules
    # imported in the same session legitimately add to it.
    from sonic_platform_base.sonic_thermal_control import common_infos
    registered = {name for name, cls in ThermalJsonObject._object_type_dict.items()
                  if getattr(cls, '__module__', None) == common_infos.__name__}
    assert registered == {'fan_info', 'fan_drawer_info', 'thermal_info',
                          'psu_info', 'chassis_info'}


# --- FanInfo ----------------------------------------------------------------------------

def test_fan_info_splits_presence_and_faults():
    present, absent, faulty = FakeFan(), FakeFan(present=False), FakeFan(healthy=False)
    info = FanInfo()
    info.collect(FakeChassis(fans=[present, absent, faulty]))

    assert info.get_presence_fans() == {present, faulty}
    assert info.get_absence_fans() == {absent}
    assert info.get_fault_fans() == {faulty}
    assert info.get_num_present_fans() == 2
    assert info.is_presence_changed() is True
    assert info.is_status_changed() is True


def test_fan_info_reports_no_change_on_a_steady_second_pass():
    chassis = FakeChassis(fans=[FakeFan(), FakeFan(present=False)])
    info = FanInfo()
    info.collect(chassis)
    info.collect(chassis)
    assert info.is_presence_changed() is False
    assert info.is_status_changed() is False


def test_fan_info_moves_a_fan_between_sets_when_it_is_removed():
    fan = FakeFan()
    chassis = FakeChassis(fans=[fan])
    info = FanInfo()
    info.collect(chassis)
    fan._present = False
    info.collect(chassis)
    assert info.get_presence_fans() == set()
    assert info.get_absence_fans() == {fan}
    assert info.is_presence_changed() is True


# --- FanDrawerInfo ----------------------------------------------------------------------

def test_fan_drawer_info_counts_only_present_drawers():
    info = FanDrawerInfo()
    info.collect(FakeChassis(fans=[FakeFan()],
                             drawers=[FakeDrawer(), FakeDrawer(present=False), FakeDrawer()]))
    assert info.get_num_present_fan_drawers() == 2
    assert len(info.get_fan_drawers()) == 3
    assert len(info.get_fans()) == 1


def test_fan_drawer_info_with_no_drawers():
    info = FanDrawerInfo()
    info.collect(FakeChassis())
    assert info.get_num_present_fan_drawers() == 0


# --- ThermalInfo ------------------------------------------------------------------------

def test_thermal_info_includes_sfp_thermals():
    info = ThermalInfo()
    info.collect(FakeChassis(thermals=[FakeThermal()],
                             sfps=[FakeSfp([FakeThermal(), FakeThermal()])]))
    assert len(info.get_thermals()) == 3


def test_thermal_info_exposes_the_manager():
    manager = object()
    info = ThermalInfo()
    info.collect(FakeChassis(manager=manager))
    assert info.get_thermal_manager() is manager


def test_thermal_info_flags_are_clear_when_cool():
    info = ThermalInfo()
    info.collect(FakeChassis(thermals=[FakeThermal(temperature=40.0)]))
    assert info.is_over_high_threshold() is False
    assert info.is_over_high_critical_threshold() is False
    assert info.is_over_threshold() is False


def test_thermal_info_flags_high_but_not_critical():
    info = ThermalInfo()
    info.collect(FakeChassis(thermals=[FakeThermal(temperature=85.0)]))
    assert info.is_over_high_threshold() is True
    assert info.is_over_high_critical_threshold() is False
    assert info.is_over_threshold() is True


def test_thermal_info_flags_critical():
    info = ThermalInfo()
    info.collect(FakeChassis(thermals=[FakeThermal(temperature=99.0)]))
    assert info.is_over_high_critical_threshold() is True
    assert info.is_over_threshold() is True


def test_thermal_info_ignores_sensors_with_no_reading():
    info = ThermalInfo()
    info.collect(FakeChassis(thermals=[FakeThermal(temperature=None)]))
    assert info.is_over_threshold() is False


def test_thermal_info_ignores_sensors_with_no_thresholds():
    info = ThermalInfo()
    info.collect(FakeChassis(thermals=[FakeThermal(temperature=200.0, high=None, critical=None)]))
    assert info.is_over_threshold() is False


def test_thermal_info_clears_flags_when_the_sensor_cools():
    thermal = FakeThermal(temperature=99.0)
    chassis = FakeChassis(thermals=[thermal])
    info = ThermalInfo()
    info.collect(chassis)
    assert info.is_over_threshold() is True
    thermal._temperature = 40.0
    info.collect(chassis)
    assert info.is_over_threshold() is False


# --- PsuInfo / ChassisInfo --------------------------------------------------------------

def test_psu_info_gathers_presence_thermals_and_fans():
    present = FakePsu(thermals=[FakeThermal()], fans=[FakeFan()])
    absent = FakePsu(present=False)
    info = PsuInfo()
    info.collect(FakeChassis(psus=[present, absent]))

    assert info.get_presence_psus() == {present}
    assert info.get_absence_psus() == {absent}
    assert info.get_num_present_psus() == 1
    assert len(info.get_thermals()) == 1
    assert len(info.get_fans()) == 1


def test_psu_info_recomputes_presence_each_pass():
    psu = FakePsu()
    chassis = FakeChassis(psus=[psu])
    info = PsuInfo()
    info.collect(chassis)
    psu._present = False
    info.collect(chassis)
    assert info.get_presence_psus() == set()
    assert info.get_absence_psus() == {psu}


def test_chassis_info_returns_the_chassis():
    chassis = FakeChassis()
    info = ChassisInfo()
    info.collect(chassis)
    assert info.get_chassis() is chassis
