"""
Thermal policy actions shared by platforms, including a PID control algorithm.

Importing this module registers the types below; see common_infos for why that is opt-in.

Fans are taken from fan_drawer_info when the policy collects it and fan_info otherwise, so
platforms are not forced to declare a drawer info type they have no use for.
"""

import logging
import time

from .common_infos import FanDrawerInfo, FanInfo, ThermalInfo
from .pid_controller import PIDController
from .thermal_action_base import ThermalPolicyActionBase
from .thermal_config import (
    get_domain_setpoint, get_fan_limits, get_pid_domains, load_thermal_config)
from .thermal_json_object import thermal_json_object

JSON_FIELD_SPEED = 'speed'
JSON_FIELD_MAX_SPEED = 'max_speed'
JSON_FIELD_CONFIG_DIR = 'config_dir'

_logger = logging.getLogger(__name__)


class FanControlError(Exception):
    """Raised when fan state needed to run a policy action is unavailable."""


def get_fans(thermal_info_dict):
    """Every fan the policy can drive, from whichever fan info type was collected."""
    drawer_info = thermal_info_dict.get(FanDrawerInfo.INFO_NAME)
    if drawer_info is not None:
        return drawer_info.get_fans()

    fan_info = thermal_info_dict.get(FanInfo.INFO_NAME)
    if fan_info is not None:
        return list(fan_info.get_presence_fans())

    raise FanControlError('neither {} nor {} was collected; add one to info_types'.format(
        FanDrawerInfo.INFO_NAME, FanInfo.INFO_NAME))


def set_all_fan_speeds(fans, speed, logger=None):
    """
    Apply one speed to every fan, tolerating individual failures.

    A fan that refuses the write is logged and skipped rather than aborting the sweep: with
    a drawer part-way out, giving up would leave the remaining fans at their old speed.
    """
    logger = logger or _logger
    if not fans:
        raise FanControlError('no fans available to set speed')

    applied = 0
    for index, fan in enumerate(fans):
        try:
            if fan.set_speed(speed):
                applied += 1
            else:
                logger.warning('fan %d refused speed %.1f%%; it may not be present',
                               index, speed)
        except Exception as exc:
            logger.error('fan %d raised setting speed %.1f%%: %s', index, speed, exc)

    if not applied:
        raise FanControlError('no fan accepted speed {:.1f}%'.format(speed))
    logger.info('applied speed %.1f%% to %d/%d fans', speed, applied, len(fans))


@thermal_json_object('fan.all.set_speed')
class SetFanSpeedAction(ThermalPolicyActionBase):
    """e.g. {"type": "fan.all.set_speed", "speed": 100}"""

    def __init__(self):
        self._speed = None

    def load_from_json(self, json_obj):
        self._speed = _validate_percentage(json_obj, JSON_FIELD_SPEED, type(self).__name__)

    def execute(self, thermal_info_dict):
        set_all_fan_speeds(get_fans(thermal_info_dict), self._speed)


@thermal_json_object('fan.set_max_speed')
class SetMaxFanSpeedAction(ThermalPolicyActionBase):
    """
    e.g. {"type": "fan.set_max_speed", "max_speed": 75}

    Caps the fans rather than driving them. A control algorithm running later in the same
    policy reads the cap back off the fans, so this must be ordered before it.
    """

    def __init__(self):
        self._max_speed = None

    def load_from_json(self, json_obj):
        self._max_speed = _validate_percentage(
            json_obj, JSON_FIELD_MAX_SPEED, type(self).__name__)

    def execute(self, thermal_info_dict):
        for index, fan in enumerate(get_fans(thermal_info_dict)):
            try:
                fan.set_max_speed(self._max_speed)
            except Exception as exc:
                _logger.error('fan %d raised setting max speed %.1f%%: %s',
                              index, self._max_speed, exc)


@thermal_json_object('thermal.control_algo')
class ThermalControlAlgorithmAction(ThermalPolicyActionBase):
    """
    PID fan control, one controller per thermal domain.

    Gains, limits and setpoints come from the platform's thermal config rather than the
    policy file. Each domain is driven by whichever of its sensors is furthest above its
    target, and the highest domain output wins, so no domain can be starved by another.
    """

    def __init__(self, logger=None):
        self._logger = logger or _logger
        self._config = {}
        self._controllers = {}
        self._fan_min = None
        self._fan_max = None
        self._last_run = None
        self._degraded = None

    def load_from_json(self, json_obj):
        # Defaults to the platform directory; overridable so the action can be exercised
        # off a switch.
        self._config = load_thermal_config(json_obj.get(JSON_FIELD_CONFIG_DIR))
        self._fan_min, self._fan_max = get_fan_limits(self._config)
        if not get_pid_domains(self._config):
            # Raising here aborts the whole policy file, which leaves the daemon running
            # with no policies: healthy to every outward check, and not cooling anything.
            # Load degraded instead and hold the fans up until the config is fixed.
            self._degraded = 'no pid_domains configured'
            self._logger.error('%s: %s; holding fans at %s%%',
                               type(self).__name__, self._degraded, self._fan_max)

    def execute(self, thermal_info_dict):
        if self._degraded:
            self._set_fail_safe_speed(thermal_info_dict)
            return
        try:
            self._run(thermal_info_dict)
        except Exception as exc:
            # Not re-raised: the policy engine has no handler, and aborting would skip every
            # remaining policy this pass. Fall back to full speed instead.
            self._logger.error('thermal control algorithm failed, going to max speed: %s', exc)
            self._set_fail_safe_speed(thermal_info_dict)

    def _set_fail_safe_speed(self, thermal_info_dict):
        try:
            set_all_fan_speeds(get_fans(thermal_info_dict), self._fan_max, self._logger)
        except Exception as exc:
            self._logger.error('fail-safe fan speed also failed: %s', exc)

    def _run(self, thermal_info_dict):
        thermal_info = thermal_info_dict.get(ThermalInfo.INFO_NAME)
        if thermal_info is None:
            raise FanControlError('{} not collected; add it to info_types'.format(
                ThermalInfo.INFO_NAME))

        fans = get_fans(thermal_info_dict)
        max_speed = self._current_max_speed(fans)

        if not self._controllers:
            interval = thermal_info.get_thermal_manager().get_interval()
            self._build_controllers(interval, max_speed)

        # The loop period is not guaranteed to equal the configured interval, so measure it
        # once per pass and share it across domains.
        now = time.monotonic()
        dt = None if self._last_run is None else now - self._last_run
        self._last_run = now

        outputs = []
        for domain, thermals in self._group_by_domain(thermal_info.get_thermals()).items():
            output = self._domain_output(domain, thermals, max_speed, dt)
            if output is not None:
                outputs.append(output)

        if not outputs:
            raise ValueError('no PID output computed; keeping current fan speeds')

        set_all_fan_speeds(fans, max(self._fan_min, min(max_speed, max(outputs))),
                           self._logger)

    def _current_max_speed(self, fans):
        """The lowest ceiling any fan will accept, clamped to the configured range."""
        if not fans:
            raise FanControlError('no fans available to read a max speed from')
        max_speed = min(fan.get_max_speed() for fan in fans)
        if not self._fan_min <= max_speed <= self._fan_max:
            self._logger.error('fan max speed %s outside [%s, %s]; clamping',
                               max_speed, self._fan_min, self._fan_max)
            max_speed = max(self._fan_min, min(max_speed, self._fan_max))
        return max_speed

    def _build_controllers(self, interval, max_speed):
        for domain, domain_config in get_pid_domains(self._config).items():
            self._controllers[domain] = PIDController(
                kp=domain_config['KP'],
                ki=domain_config['KI'],
                kd=domain_config['KD'],
                output_min=self._fan_min,
                output_max=max_speed,
                interval=interval,
                # Gains are tuned around the midpoint of the fan range.
                setpoint_output=(self._fan_min + max_speed) / 2,
                name='pid[{}]'.format(domain),
                logger=self._logger)

    def _group_by_domain(self, thermals):
        domains = {}
        for thermal in thermals:
            if not hasattr(thermal, 'is_controlled_by_pid'):
                self._logger.warning('thermal %s does not define is_controlled_by_pid()',
                                     thermal.get_name())
                continue
            if not thermal.is_controlled_by_pid():
                continue
            domain = thermal.get_pid_domain()
            if domain in self._controllers:
                domains.setdefault(domain, []).append(thermal)
        return domains

    def _domain_output(self, domain, thermals, max_speed, dt):
        controller = self._controllers[domain]
        if max_speed >= controller.output_min:
            controller.set_output_limits(output_max=max_speed)
        else:
            self._logger.error("domain '%s': max speed %s below minimum %s; keeping limit",
                               domain, max_speed, controller.output_min)

        override = get_domain_setpoint(self._config, domain)
        max_error = None
        for thermal in thermals:
            temperature = thermal.get_temperature()
            if temperature is None:
                continue
            setpoint = override if override is not None else thermal.get_pid_setpoint()
            if setpoint is None:
                continue
            error = temperature - setpoint
            if max_error is None or error > max_error:
                max_error = error

        if max_error is None:
            self._logger.info("domain '%s': no usable sensor reading, skipping", domain)
            return None
        return controller.compute(max_error, dt)


def _validate_percentage(json_obj, field, owner):
    try:
        value = float(json_obj[field])
    except KeyError:
        raise ValueError('{}: missing {}'.format(owner, field)) from None
    except (TypeError, ValueError):
        raise ValueError('{}: {} must be a number, got {!r}'.format(
            owner, field, json_obj[field])) from None
    if not 0 <= value <= 100:
        raise ValueError('{}: {} must be within [0, 100], got {}'.format(owner, field, value))
    return value
