"""Unit tests for sonic_platform_base.sonic_thermal_control.pid_controller."""

import pytest

from sonic_platform_base.sonic_thermal_control.pid_controller import PIDController


def make_controller(**kwargs):
    """A proportional-only controller over 0-100 with a 50 operating point."""
    params = dict(kp=2.0, ki=0.0, kd=0.0, output_min=0.0, output_max=100.0,
                  interval=5.0, setpoint_output=50.0)
    params.update(kwargs)
    return PIDController(**params)


@pytest.mark.parametrize("bad", [{'kp': -1.0}, {'ki': -0.1}, {'kd': -0.5}])
def test_rejects_negative_gain(bad):
    with pytest.raises(ValueError):
        make_controller(**bad)


def test_rejects_all_zero_gains():
    with pytest.raises(ValueError):
        make_controller(kp=0.0, ki=0.0, kd=0.0)


def test_rejects_inverted_output_range():
    with pytest.raises(ValueError):
        make_controller(output_min=90.0, output_max=10.0)


def test_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        make_controller(interval=0.0)


def test_zero_integral_gain_is_supported():
    """Ki=0 must not raise; a P-only controller is a legitimate configuration."""
    controller = make_controller(ki=0.0)
    assert controller.compute(0.0) == 50.0
    assert controller.compute(10.0) == 70.0


def test_zero_error_holds_the_operating_point():
    assert make_controller().compute(0.0) == 50.0


def test_exposes_output_limits():
    controller = make_controller()
    assert (controller.output_min, controller.output_max) == (0.0, 100.0)


def test_output_follows_error_sign():
    assert make_controller().compute(10.0) == 70.0
    assert make_controller().compute(-10.0) == 30.0


def test_output_is_clamped():
    assert make_controller().compute(1000.0) == 100.0
    assert make_controller().compute(-1000.0) == 0.0


def test_integral_accumulates_with_dt():
    controller = make_controller(kp=1.0, ki=0.5, setpoint_output=0.0, output_min=0.0)
    assert controller.compute(10.0, dt=2.0) == 20.0    # 1*10 + 0.5*(10*2)
    assert controller.compute(10.0, dt=2.0) == 30.0    # 1*10 + 0.5*(20+20)


def test_dt_defaults_to_nominal_interval():
    controller = make_controller(kp=0.0, ki=1.0, setpoint_output=0.0, interval=4.0)
    assert controller.compute(2.0) == 8.0              # 2 * 4s


def test_integral_freezes_while_saturated_then_unwinds():
    controller = make_controller(kp=1.0, ki=1.0, kd=0.0, output_min=0.0,
                                 output_max=10.0, interval=1.0, setpoint_output=0.0)
    assert controller.compute(5.0, dt=1.0) == 10.0
    frozen = controller._integral
    # Still against the ceiling, so the integral must not keep growing.
    assert controller.compute(5.0, dt=1.0) == 10.0
    assert controller._integral == frozen
    # A negative error unwinds it promptly rather than after a long delay.
    assert controller.compute(-2.0, dt=1.0) == 1.0
    assert controller._integral < frozen


def test_derivative_suppressed_on_first_call():
    """A cold start has no previous sample, so it must not produce a derivative kick."""
    controller = make_controller(kp=0.0, ki=0.0, kd=10.0, interval=1.0)
    assert controller.compute(7.0, dt=1.0) == 50.0


def test_derivative_responds_to_change():
    controller = make_controller(kp=0.0, ki=0.0, kd=10.0, interval=1.0)
    controller.compute(0.0, dt=1.0)
    assert controller.compute(3.0, dt=1.0) == 80.0     # 10 * (3 / 1s)


def test_implausibly_large_dt_is_clamped_and_suppresses_derivative():
    controller = make_controller(kp=0.0, ki=1.0, kd=10.0, setpoint_output=0.0,
                                 output_min=-1000.0, output_max=1000.0, interval=5.0)
    controller.compute(1.0, dt=5.0)
    before = controller._integral
    # A 600s gap must advance the integral by the clamped bound (3 * 5s), and the stale
    # sample must not generate a derivative term.
    controller.compute(1.0, dt=600.0)
    assert controller._integral == pytest.approx(before + 15.0)


def test_implausibly_small_dt_is_clamped():
    controller = make_controller(kp=0.0, ki=0.0, kd=10.0, interval=5.0)
    controller.compute(0.0, dt=5.0)
    # dt is floored at 0.25 * 5s, so D = 10 * (3 / 1.25s), not 10 * (3 / 0.01s).
    assert controller.compute(3.0, dt=0.01) == pytest.approx(74.0)


def test_non_positive_dt_falls_back_to_interval():
    controller = make_controller(kp=0.0, ki=1.0, setpoint_output=0.0, interval=5.0)
    assert controller.compute(2.0, dt=0.0) == 10.0
    assert controller.compute(2.0, dt=-3.0) == 20.0


def test_reset_with_current_output_is_bumpless():
    controller = make_controller(kp=1.0, ki=0.5)
    controller.compute(20.0)
    controller.reset(current_output=80.0)
    assert controller.compute(0.0) == pytest.approx(80.0)


def test_reset_without_output_clears_integral():
    controller = make_controller(kp=1.0, ki=0.5)
    controller.compute(20.0)
    controller.reset()
    assert controller.compute(0.0) == 50.0


def test_set_output_limits_reclamps_integral():
    controller = make_controller(kp=0.0, ki=1.0, setpoint_output=0.0,
                                 output_min=0.0, output_max=100.0, interval=1.0)
    controller.compute(100.0, dt=1.0)
    assert controller.compute(0.0, dt=1.0) == 100.0
    controller.set_output_limits(output_max=40.0)
    assert controller.compute(0.0, dt=1.0) == 40.0
    assert controller.output_max == 40.0


def test_set_output_limits_rejects_inversion():
    controller = make_controller()
    with pytest.raises(ValueError):
        controller.set_output_limits(output_min=90.0, output_max=10.0)
