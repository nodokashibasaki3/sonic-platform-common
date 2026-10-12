"""
Loading a real multi-policy file through ThermalManagerBase.

The per-condition tests exercise conditions in isolation, which is not enough: the manager
rejects two policies whose conditions compare equal, and the base class compares conditions
by type alone. A single parameterised condition class therefore has to define equality
itself. These tests load whole policy files the way thermalctld does.
"""

import json

import pytest

from sonic_platform_base.sonic_thermal_control import (   # noqa: F401  (registers types)
    common_actions, common_conditions, common_infos)
from sonic_platform_base.sonic_thermal_control.common_conditions import (
    FanDrawerPresenceCondition, ThermalOverThresholdCondition)
from sonic_platform_base.sonic_thermal_control.thermal_manager_base import ThermalManagerBase


class Manager(ThermalManagerBase):
    pass


def load(tmp_path, policies):
    Manager._policy_dict = {}
    Manager._thermal_info_dict = {}
    path = tmp_path / "thermal_policy.json"
    path.write_text(json.dumps({
        "interval": 5,
        "info_types": [{"type": "fan_drawer_info"}, {"type": "thermal_info"}],
        "policies": policies,
    }))
    Manager.load(str(path))
    return Manager


def drawer_policy(name, op, count, speed):
    return {"name": name,
            "conditions": [{"type": "fandrawer.presence", "op": op, "count": count}],
            "actions": [{"type": "fan.all.set_speed", "speed": speed}]}


def test_graduated_policy_loads(tmp_path):
    """The shape this platform actually ships: one policy per drawer count."""
    manager = load(tmp_path, [
        drawer_policy("One fan drawer present", "==", 1, 100),
        drawer_policy("Two fan drawers present", "==", 2, 95),
        drawer_policy("Three fan drawers present", "==", 3, 80),
        drawer_policy("Four fan drawers present", "==", 4, 75),
        drawer_policy("More than two", ">", 2, 50),
    ])
    assert len(manager._policy_dict) == 5


def test_same_count_different_operator_is_not_a_duplicate(tmp_path):
    manager = load(tmp_path, [drawer_policy("at most two", "<=", 2, 100),
                              drawer_policy("exactly two", "==", 2, 95)])
    assert len(manager._policy_dict) == 2


def test_genuinely_identical_conditions_are_still_rejected(tmp_path):
    """The duplicate check must keep working - two policies cannot guard on the same thing."""
    with pytest.raises(Exception, match="duplicate conditions"):
        load(tmp_path, [drawer_policy("first", "==", 2, 100),
                        drawer_policy("second", "==", 2, 95)])


def test_threshold_conditions_differ_by_threshold(tmp_path):
    manager = load(tmp_path, [
        {"name": "high", "conditions": [{"type": "thermal.over.threshold",
                                         "threshold": "high"}],
         "actions": [{"type": "fan.all.set_speed", "speed": 80}]},
        {"name": "critical", "conditions": [{"type": "thermal.over.threshold",
                                             "threshold": "high_critical"}],
         "actions": [{"type": "fan.all.set_speed", "speed": 100}]},
    ])
    assert len(manager._policy_dict) == 2


# --- the equality contract the manager depends on --------------------------------------

def make(cls, **json_obj):
    obj = cls()
    obj.load_from_json(json_obj)
    return obj


def test_equality_distinguishes_count():
    assert make(FanDrawerPresenceCondition, op="==", count=1) != \
           make(FanDrawerPresenceCondition, op="==", count=2)


def test_equality_distinguishes_operator():
    assert make(FanDrawerPresenceCondition, op="<=", count=2) != \
           make(FanDrawerPresenceCondition, op="==", count=2)


def test_equality_matches_on_identical_parameters():
    a = make(FanDrawerPresenceCondition, op="<=", count=2)
    b = make(FanDrawerPresenceCondition, op="<=", count=2)
    assert a == b and hash(a) == hash(b)


def test_threshold_equality():
    assert make(ThermalOverThresholdCondition, threshold="high") != \
           make(ThermalOverThresholdCondition, threshold="high_critical")
    assert make(ThermalOverThresholdCondition, threshold="high") == \
           make(ThermalOverThresholdCondition, threshold="high")


def test_policy_loads_even_when_the_thermal_config_is_missing(tmp_path, monkeypatch):
    """
    On hardware a missing thermal_config.json aborted thermal manager init, so thermalctld
    ran with no policies at all: RUNNING, fans at their last value, nothing cooling. The
    policy must still load so the control action can hold the fans up instead.
    """
    from sonic_platform_base.sonic_thermal_control import common_actions
    monkeypatch.setattr(common_actions, 'load_thermal_config', lambda *a, **k: {})

    manager = load(tmp_path, [
        drawer_policy("One fan drawer present", "==", 1, 100),
        {"name": "thermal control algorithm",
         "conditions": [{"type": "fandrawer.presence", "op": ">", "count": 2}],
         "actions": [{"type": "thermal.control_algo"}]},
    ])
    assert len(manager._policy_dict) == 2
