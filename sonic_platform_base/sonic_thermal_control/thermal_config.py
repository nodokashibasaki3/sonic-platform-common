"""
Per-platform thermal tunables, loaded separately from the policy file.

The policy file describes structure - which conditions trigger which actions - and changes
rarely. Gains, limits and setpoints are tuned per platform and change often, so they live
here instead of inline in the policy's action blocks.
"""

import json
import os

DEFAULT_CONFIG_DIR = '/usr/share/sonic/platform'
CONFIG_FILE_NAME = 'thermal_config.json'

DEFAULT_INTERVAL = 60
DEFAULT_FAN_MIN_SPEED = 30.0
DEFAULT_FAN_MAX_SPEED = 100.0

_REQUIRED_GAINS = ('KP', 'KI', 'KD')


class ThermalConfigError(ValueError):
    """Raised when a thermal config file is present but unusable."""


def config_path(config_dir=None):
    return os.path.join(config_dir or DEFAULT_CONFIG_DIR, CONFIG_FILE_NAME)


def load_thermal_config(config_dir=None):
    """
    Return the platform's thermal tunables, or an empty dict when no config file exists.

    An absent file is not an error: callers fall back to whatever the policy file or their
    own defaults provide. A file that exists but is malformed is an error, since silently
    running on defaults would hide a mistuned platform.

    Raises:
        ThermalConfigError: On unreadable JSON or a failed validation check.
    """
    path = config_path(config_dir)
    if not os.path.isfile(path):
        return {}

    try:
        with open(path) as config_file:
            config = json.load(config_file)
    except (OSError, ValueError) as exc:
        raise ThermalConfigError('{}: {}'.format(path, exc))

    if not isinstance(config, dict):
        raise ThermalConfigError('{}: top level must be an object'.format(path))

    _validate(config, path)
    return config


def get_interval(config, default=DEFAULT_INTERVAL):
    return config.get('interval', default)


def get_fan_limits(config):
    limits = config.get('fan_limits', {})
    return (limits.get('min', DEFAULT_FAN_MIN_SPEED),
            limits.get('max', DEFAULT_FAN_MAX_SPEED))


def get_pid_domains(config):
    return config.get('pid_domains', {})


def get_domain_setpoint(config, domain):
    """Explicit target temperature for a domain, or None to derive it from the sensors."""
    return get_pid_domains(config).get(domain, {}).get('setpoint')


def _is_number(value):
    # JSON true/false load as bool, which is a subclass of int and would pass for 1 and 0.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate(config, path):
    def fail(msg):
        raise ThermalConfigError('{}: {}'.format(path, msg))

    interval = config.get('interval')
    if interval is not None:
        if not _is_number(interval) or interval <= 0:
            fail('interval must be a positive number, got {!r}'.format(interval))

    limits = config.get('fan_limits')
    if limits is not None:
        if not isinstance(limits, dict):
            fail('fan_limits must be an object')
        low = limits.get('min', DEFAULT_FAN_MIN_SPEED)
        high = limits.get('max', DEFAULT_FAN_MAX_SPEED)
        for name, value in (('min', low), ('max', high)):
            if not _is_number(value):
                fail('fan_limits.{} must be a number, got {!r}'.format(name, value))
        if low > high:
            fail('fan_limits.min {} exceeds fan_limits.max {}'.format(low, high))

    domains = config.get('pid_domains')
    if domains is None:
        return
    if not isinstance(domains, dict):
        fail('pid_domains must be an object')
    for domain, domain_config in domains.items():
        if not isinstance(domain_config, dict):
            fail("pid_domains.{} must be an object".format(domain))
        for gain in _REQUIRED_GAINS:
            if gain not in domain_config:
                fail("pid_domains.{} is missing {}".format(domain, gain))
            value = domain_config[gain]
            if not _is_number(value) or value < 0:
                fail("pid_domains.{}.{} must be a non-negative number, got {!r}".format(
                    domain, gain, value))
        if not any(domain_config[gain] for gain in _REQUIRED_GAINS):
            fail("pid_domains.{} has all-zero gains".format(domain))
        setpoint = domain_config.get('setpoint')
        if setpoint is not None and not _is_number(setpoint):
            fail("pid_domains.{}.setpoint must be a number, got {!r}".format(domain, setpoint))
