from __future__ import annotations

from math import exp

import numpy as np
import pytest

from voxmaps_sim.config import SimulationConfig
from voxmaps_sim.sensor import VirtualSensor


def _ideal_sensor(**overrides) -> dict:
    sensor = {
        "sample_interval_s": 1.0,
        "response_time_pm25_s": 0.0,
        "response_time_pm10_s": 0.0,
        "transport_delay_s": 0.0,
        "additive_noise_std_ug_m3": 0.0,
        "multiplicative_noise_std_fraction": 0.0,
        "calibration_slope": 1.0,
        "calibration_offset_ug_m3": 0.0,
        "bias_ug_m3": 0.0,
        "quantization_ug_m3": 0.0,
        "lower_detection_limit_ug_m3": 0.0,
        "upper_saturation_limit_ug_m3": 1000.0,
        "dropout_probability": 0.0,
        "rotor_wash_dilution_factor": 1.0,
    }
    sensor.update(overrides)
    return {"project": {"random_seed": 1}, "sensor": sensor}


def test_first_order_response_lag_uses_exact_exponential_alpha() -> None:
    sensor = VirtualSensor(
        _ideal_sensor(response_time_pm25_s=5.0, response_time_pm10_s=5.0), seed=1
    )
    reading = sensor.sample(0.0, 100.0, 150.0)
    alpha = 1.0 - exp(-1.0 / 5.0)
    assert reading.pm25_ug_m3 == pytest.approx(100.0 * alpha)
    assert reading.pm10_ug_m3 == pytest.approx(150.0 * alpha)


def test_transport_delay_holds_initial_truth_then_replays_signal() -> None:
    sensor = VirtualSensor(_ideal_sensor(transport_delay_s=1.0), seed=1)
    first = sensor.sample(0.0, 100.0, 150.0)
    second = sensor.sample(1.0, 200.0, 300.0)
    assert first.pm25_ug_m3 == 0.0
    assert second.pm25_ug_m3 == 100.0
    assert second.pm10_ug_m3 == 150.0


def test_saturation_and_guaranteed_dropout_are_flagged() -> None:
    saturated = VirtualSensor(_ideal_sensor(upper_saturation_limit_ug_m3=50.0), seed=1)
    reading = saturated.sample(0.0, 100.0, 150.0)
    assert reading.pm25_ug_m3 == reading.pm10_ug_m3 == 50.0
    assert "sensor_saturation" in reading.flags

    dropped = VirtualSensor(_ideal_sensor(dropout_probability=1.0), seed=1)
    missing = dropped.sample(0.0, 10.0, 20.0)
    assert np.isnan(missing.pm25_ug_m3) and np.isnan(missing.pm10_ug_m3)
    assert "sensor_dropout" in missing.flags


def test_pm_consistency_is_enforced_after_independent_noise() -> None:
    config = _ideal_sensor(
        additive_noise_std_ug_m3=20.0,
        multiplicative_noise_std_fraction=0.2,
    )
    sensor = VirtualSensor(config, seed=7)
    for time in range(30):
        reading = sensor.sample(float(time), 10.0, 10.0)
        assert reading.pm10_ug_m3 >= reading.pm25_ug_m3


def test_noise_is_deterministic_for_same_seed() -> None:
    config = _ideal_sensor(
        additive_noise_std_ug_m3=2.0,
        multiplicative_noise_std_fraction=0.1,
    )
    left = VirtualSensor(config, seed=99)
    right = VirtualSensor(config, seed=99)
    left_values = [left.sample(float(t), 20.0, 35.0).readings for t in range(10)]
    right_values = [right.sample(float(t), 20.0, 35.0).readings for t in range(10)]
    assert left_values == right_values


def test_calibration_quantization_rotor_wash_and_humidity() -> None:
    config = _ideal_sensor(
        rotor_wash_dilution_factor=0.5,
        humidity_interference_enabled=True,
        humidity_reference_pct=60.0,
        humidity_interference_fraction_per_pct=0.01,
        calibration_slope=2.0,
        calibration_offset_ug_m3=1.0,
        quantization_ug_m3=1.0,
    )
    sensor = VirtualSensor(config, seed=1)
    reading = sensor.sample(0.0, 10.0, 20.0, rh_pct=70.0)
    # inlet: 10 * 0.5 * 1.1 = 5.5; calibration: 5.5 * 2 + 1 = 12
    assert reading.pm25_ug_m3 == 12.0
    assert reading.pm10_ug_m3 == 23.0


def test_sample_interval_returns_a_flagged_hold() -> None:
    sensor = VirtualSensor(_ideal_sensor(sample_interval_s=2.0), seed=1)
    first = sensor.sample(0.0, 10.0, 20.0)
    held = sensor.sample(1.0, 50.0, 80.0)
    assert held.pm25_ug_m3 == first.pm25_ug_m3
    assert "sensor_interval_hold" in held.flags


def test_pydantic_config_field_aliases_are_consumed() -> None:
    config = SimulationConfig.model_validate(
        {
            "sensor": {
                "transport_delay_s": 0.0,
                "response_time_pm25_s": 0.0,
                "response_time_pm10_s": 0.0,
                "additive_noise_std_ug_m3": 0.0,
                "multiplicative_noise_std_fraction": 0.0,
                "calibration_slope_pm25": 2.0,
                "calibration_slope_pm10": 3.0,
                "calibration_offset_pm25_ug_m3": 1.0,
                "calibration_offset_pm10_ug_m3": 2.0,
                "quantization_ug_m3": 0.0,
            }
        }
    )
    reading = VirtualSensor(config, seed=1).sample(0.0, 10.0, 20.0)
    assert reading.pm25_ug_m3 == 21.0
    assert reading.pm10_ug_m3 == 62.0

