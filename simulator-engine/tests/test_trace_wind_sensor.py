from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from voxmaps_sim.config import SensorConfig, SimulationConfig, WindConfig
from voxmaps_sim.dispersion import DispersionModel, DomainBounds
from voxmaps_sim.sensor import VirtualSensor
from voxmaps_sim.source import SourceModel
from voxmaps_sim.wind import build_wind_series


def test_nearest_measured_wind_is_real_and_preserves_provenance() -> None:
    flight = pd.DataFrame(
        {
            "elapsed_time_s": [0.0, 10.0],
            "wind_speed_mps": [2.0, 6.0],
            "wind_direction_from_deg": [270.0, 180.0],
        }
    )
    config = WindConfig(
        mode="measured",
        minimum_numeric_coverage=1.0,
        maximum_interpolation_gap_s=20.0,
        interpolation_method="nearest",
    )
    result = build_wind_series(flight, config, np.array([2.0, 8.0]))
    assert result["wind_speed_mps"].tolist() == pytest.approx([2.0, 6.0])
    assert result["wind_direction_from_deg"].tolist() == pytest.approx([270.0, 180.0])
    assert result["wind_interpolated"].tolist() == [True, True]


def test_separate_pm25_pm10_additive_noise_is_deterministic() -> None:
    sensor_config = SensorConfig(
        response_time_pm25_s=0.0,
        response_time_pm10_s=0.0,
        transport_delay_s=0.0,
        additive_noise_std_ug_m3=99.0,
        additive_noise_std_pm25_ug_m3=0.0,
        additive_noise_std_pm10_ug_m3=2.0,
        multiplicative_noise_std_fraction=0.0,
        quantization_ug_m3=0.0,
    )
    config = SimulationConfig(sensor=sensor_config)
    left = VirtualSensor(config, seed=123)
    right = VirtualSensor(config, seed=123)
    left_reading = left.sample(0.0, 10.0, 20.0)
    right_reading = right.sample(0.0, 10.0, 20.0)
    assert left_reading == right_reading
    assert left_reading.pm25_ug_m3 == pytest.approx(10.0)
    assert left_reading.pm10_ug_m3 != pytest.approx(20.0)


def test_ninety_degree_wind_change_bends_existing_and_new_parcel_cohorts() -> None:
    config = SimulationConfig.model_validate(
        {
            "project": {"random_seed": 10},
            "simulation": {"particles_per_timestep": 4, "maximum_particles": 100},
            "dispersion": {
                "horizontal_diffusivity_m2_s": 0.0,
                "vertical_diffusivity_m2_s": 0.0,
                "fine_settling_velocity_mps": 0.0,
                "coarse_settling_velocity_mps": 0.0,
                "fine_deposition_velocity_mps": 0.0,
                "coarse_deposition_velocity_mps": 0.0,
            },
        }
    )
    source = SourceModel(
        stack_height_m=10.0,
        plume_rise_m=0.0,
        pm25_emission_g_s=0.2,
        pm10_total_emission_g_s=0.5,
    )
    model = DispersionModel(
        config,
        DomainBounds(-100.0, 100.0, -100.0, 100.0, 0.0, 100.0),
    )

    first = model.emit(source, 0.0, 2.0)
    first_ids = first.parcel_id.copy()
    model.step(2.0, 0.0, 2.0)  # east for two seconds
    second = model.emit(source, 2.0, 1.0)
    second_ids = second.parcel_id.copy()
    model.step(0.0, 3.0, 1.0)  # then a 90-degree turn toward north

    old = model.particles.subset(np.isin(model.particles.parcel_id, first_ids))
    new = model.particles.subset(np.isin(model.particles.parcel_id, second_ids))
    np.testing.assert_allclose(
        old.positions_m[:, :2], np.repeat([[4.0, 3.0]], len(old), axis=0)
    )
    np.testing.assert_allclose(
        new.positions_m[:, :2], np.repeat([[0.0, 3.0]], len(new), axis=0)
    )
    # The older cohort retains its eastward displacement and then turns north;
    # the later cohort starts at the source.  This is a bent plume, not a rigid
    # rotation of a completed eastward plume.
    assert old.positions_m[:, 0].mean() > new.positions_m[:, 0].mean()
