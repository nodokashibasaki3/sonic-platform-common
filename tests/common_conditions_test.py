"""Unit tests for sonic_platform_base.sonic_thermal_control.common_conditions."""

import pytest

from sonic_platform_base.sonic_thermal_control.common_conditions import (
    DefaultCondition, FanDrawerPresenceCondition, FanPresenceCondition, OPERATORS,
    PsuPresenceCondition, ThermalOverThresholdCondition)
from sonic_platform_base.sonic_thermal_control.common_infos import (
    FanDrawerInfo, FanInfo, PsuInfo, ThermalInfo)
from sonic_platform_base.sonic_thermal_control.thermal_json_object import ThermalJsonObject


class FakeDrawerInfo:
    def __init__(self, present): self._present = present
    def get_num_present_fan_drawers(self): return self._present


class FakeFanInfo:
    def __init__(self, present): self._present = present
    def get_num_present_fans(self): return self._present


class FakePsuInfo:
    def __init__(self, present): self._present = present
    def get_num_present_psus(self): return self._present


class FakeThermalInfo:
    def __init__(self, high=False, critical=False): self._high, self._critical = high, critical
    def is_over_high_threshold(self): return self._high
    def is_over_high_critical_threshold(self): return self._critical


def drawers(n):
    return {FanDrawerInfo.INFO_NAME: FakeDrawerInfo(n)}


def loaded(cls, **json_obj):
    condition = cls()
    condition.load_from_json(json_obj)
    return condition


# --- registration -----------------------------------------------------------------------

@pytest.mark.parametrize("type_name, cls", [
    ('fandrawer.presence', FanDrawerPresenceCondition),
    ('fan.presence', FanPresenceCondition),
    ('psu.presence', PsuPresenceCondition),
    ('thermal.over.threshold', ThermalOverThresholdCondition),
    ('default.operation', DefaultCondition),
])
def test_condition_is_registered(type_name, cls):
    assert ThermalJsonObject.get_type({'type': type_name}) is cls


# --- the whole point: one class replaces nine ---------------------------------------------

@pytest.mark.parametrize("present, expected", [(0, True), (1, True), (2, True), (3, False)])
def test_two_or_fewer_drawers(present, expected):
    condition = loaded(FanDrawerPresenceCondition, op='<=', count=2)
    assert condition.is_match(drawers(present)) is expected


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 7, 8])
def test_exact_count_replaces_the_per_count_classes(count):
    condition = loaded(FanDrawerPresenceCondition, op='==', count=count)
    assert condition.is_match(drawers(count)) is True
    assert condition.is_match(drawers(count + 1)) is False


@pytest.mark.parametrize("op", sorted(OPERATORS))
def test_every_operator_is_usable(op):
    condition = loaded(FanDrawerPresenceCondition, op=op, count=4)
    assert condition.is_match(drawers(4)) is OPERATORS[op](4, 4)
    assert condition.is_match(drawers(9)) is OPERATORS[op](9, 4)


def test_zero_present_is_covered():
    assert loaded(FanDrawerPresenceCondition, op='==', count=0).is_match(drawers(0)) is True


# --- validation -------------------------------------------------------------------------

def test_rejects_unknown_operator():
    with pytest.raises(ValueError, match='unknown op'):
        loaded(FanDrawerPresenceCondition, op='=<', count=2)


@pytest.mark.parametrize("count", [-1, 1.5, "2", None, True])
def test_rejects_bad_count(count):
    with pytest.raises(ValueError, match='non-negative integer'):
        loaded(FanDrawerPresenceCondition, op='<=', count=count)


@pytest.mark.parametrize("json_obj", [{'op': '<='}, {'count': 2}, {}])
def test_rejects_missing_fields(json_obj):
    with pytest.raises(ValueError, match='requires'):
        FanDrawerPresenceCondition().load_from_json(json_obj)


def test_unloaded_condition_refuses_to_match():
    with pytest.raises(ValueError, match='not loaded'):
        FanDrawerPresenceCondition().is_match(drawers(1))


def test_missing_info_type_raises_rather_than_silently_not_matching():
    condition = loaded(FanDrawerPresenceCondition, op='<=', count=2)
    with pytest.raises(ValueError, match='add it to info_types'):
        condition.is_match({})


# --- other condition types ----------------------------------------------------------------

def test_fan_and_psu_presence():
    assert loaded(FanPresenceCondition, op='==', count=0).is_match(
        {FanInfo.INFO_NAME: FakeFanInfo(0)}) is True
    assert loaded(PsuPresenceCondition, op='<', count=2).is_match(
        {PsuInfo.INFO_NAME: FakePsuInfo(1)}) is True


@pytest.mark.parametrize("threshold, high, critical, expected", [
    ('high', True, False, True),
    ('high', False, True, False),
    ('high_critical', False, True, True),
    ('high_critical', True, False, False),
])
def test_thermal_over_threshold(threshold, high, critical, expected):
    condition = loaded(ThermalOverThresholdCondition, threshold=threshold)
    info = {ThermalInfo.INFO_NAME: FakeThermalInfo(high=high, critical=critical)}
    assert condition.is_match(info) is expected


def test_threshold_defaults_to_high_not_critical():
    condition = loaded(ThermalOverThresholdCondition)
    info = {ThermalInfo.INFO_NAME: FakeThermalInfo(high=True, critical=False)}
    assert condition.is_match(info) is True


def test_rejects_unknown_threshold():
    with pytest.raises(ValueError, match='unknown threshold'):
        loaded(ThermalOverThresholdCondition, threshold='toasty')


def test_thermal_condition_needs_its_info_type():
    with pytest.raises(ValueError, match='add it to info_types'):
        loaded(ThermalOverThresholdCondition).is_match({})


def test_default_condition_always_matches():
    assert DefaultCondition().is_match({}) is True
    assert DefaultCondition().is_match(drawers(0)) is True


# --- equality, which the policy manager relies on to spot duplicate policies -------------

def _load(cls, json_obj):
    condition = cls()
    condition.load_from_json(json_obj)
    return condition


def test_presence_condition_differs_from_another_type_with_the_same_parameters():
    drawers = _load(FanDrawerPresenceCondition, {"op": "<=", "count": 2})
    psus = _load(PsuPresenceCondition, {"op": "<=", "count": 2})
    assert drawers != psus


def test_equal_presence_conditions_hash_alike():
    first = _load(FanDrawerPresenceCondition, {"op": "<=", "count": 2})
    second = _load(FanDrawerPresenceCondition, {"op": "<=", "count": 2})
    assert first == second
    assert len({first, second}) == 1


def test_threshold_conditions_compare_by_threshold():
    high = _load(ThermalOverThresholdCondition, {"threshold": "high"})
    also_high = _load(ThermalOverThresholdCondition, {})
    critical = _load(ThermalOverThresholdCondition, {"threshold": "high_critical"})
    assert high == also_high
    assert high != critical
    assert len({high, also_high, critical}) == 2


def test_threshold_condition_differs_from_another_type():
    assert _load(ThermalOverThresholdCondition, {}) != DefaultCondition()
