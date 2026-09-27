from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from voxmaps_sim.batch import generate_batch, split_scenario_ids
from voxmaps_sim.config import SimulationConfig


def _flight_csv(path: Path) -> Path:
    rows = 4
    pd.DataFrame(
        {
            "time(millisecond)": np.arange(rows) * 1_000,
            "datetime(utc)": pd.date_range(
                "2026-04-14T00:00:00Z", periods=rows, freq="1s"
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "latitude": np.full(rows, 28.5685),
            "longitude": np.full(rows, 77.2773),
            "height_above_takeoff(feet)": np.full(rows, 4.0 / 0.3048),
        }
    ).to_csv(path, index=False)
    return path


def _batch_config() -> SimulationConfig:
    return SimulationConfig.model_validate(
        {
            "project": {
                "name": "voxmaps-batch-test",
                "scenario_id": "batch-test",
                "random_seed": 99,
            },
            "wind": {
                "mode": "synthetic",
                "synthetic": {
                    "base_speed_mps": 0.0,
                    "base_direction_from_deg": 270.0,
                    "speed_variability_mps": 0.0,
                    "direction_variability_deg": 0.0,
                    "gust_period_s": 60.0,
                },
            },
            "source": {
                "offset_east_from_flight_centre_m": 0.0,
                "offset_north_from_flight_centre_m": 0.0,
                "stack_height_m": 4.0,
                "plume_rise_m": 0.0,
                "pm25_emission_g_s": 0.1,
                "pm10_total_emission_g_s": 0.2,
            },
            "simulation": {
                "timestep_s": 1.0,
                "trajectory_sample_interval_s": 1.0,
                "particles_per_timestep": 2,
                "horizontal_padding_m": 10.0,
                "vertical_min_m": 0.0,
                "vertical_max_m": 20.0,
                "maximum_particles": 200,
            },
            "dispersion": {
                "horizontal_diffusivity_m2_s": 0.0,
                "vertical_diffusivity_m2_s": 0.0,
                "fine_settling_velocity_mps": 0.0,
                "coarse_settling_velocity_mps": 0.0,
                "fine_deposition_velocity_mps": 0.0,
                "coarse_deposition_velocity_mps": 0.0,
            },
            "sensor": {
                "response_time_pm25_s": 0.0,
                "response_time_pm10_s": 0.0,
                "transport_delay_s": 0.0,
                "additive_noise_std_ug_m3": 0.0,
                "multiplicative_noise_std_fraction": 0.0,
            },
            "batch": {
                "scenarios": 3,
                "train_fraction": 1 / 3,
                "validation_fraction": 1 / 3,
                "test_fraction": 1 / 3,
                "source_offset_east_m": [0.0, 0.0],
                "source_offset_north_m": [0.0, 0.0],
                "stack_height_m": [4.0, 4.0],
                "pm25_emission_g_s": [0.1, 0.1],
                "pm10_total_emission_g_s": [0.2, 0.2],
                "emission_start_fraction": [0.0, 0.0],
                "emission_end_fraction": [1.0, 1.0],
                "wind_speed_mps": [0.0, 0.0],
                "wind_direction_from_deg": [270.0, 270.0],
                "wind_variability_mps": [0.0, 0.0],
                "horizontal_diffusivity_m2_s": [0.0, 0.0],
                "additive_noise_std_ug_m3": [0.0, 0.0],
                "sensor_bias_ug_m3": [0.0, 0.0],
                "response_lag_s": [0.0, 0.0],
                "background_pm25_ug_m3": [5.0, 5.0],
            },
        }
    )


def test_split_scenario_ids_has_no_leakage_and_is_deterministic():
    scenario_ids = [f"scenario_{index:03d}" for index in range(12)]
    first = split_scenario_ids(scenario_ids, 0.5, 0.25, 0.25, seed=42)
    second = split_scenario_ids(scenario_ids, 0.5, 0.25, 0.25, seed=42)

    assert first == second
    train, validation, test = map(set, first.values())
    assert train.isdisjoint(validation)
    assert train.isdisjoint(test)
    assert validation.isdisjoint(test)
    assert train | validation | test == set(scenario_ids)


def test_generated_batch_manifest_and_files_do_not_leak_scenarios(tmp_path):
    flight = _flight_csv(tmp_path / "flight.csv")
    output = tmp_path / "dataset"

    result = generate_batch(flight, _batch_config(), output, scenarios=3)

    assert len(result.manifest) == 3
    train, validation, test = map(set, result.split_ids.values())
    assert train.isdisjoint(validation)
    assert train.isdisjoint(test)
    assert validation.isdisjoint(test)
    assert train | validation | test == set(result.manifest["scenario_id"])
    assert set(result.manifest["split"]) == {"train", "validation", "test"}

    manifest = pd.read_csv(output / "dataset_manifest.csv")
    for row in manifest.itertuples(index=False):
        scenario_dir = output / row.scenario_directory
        samples = pd.read_csv(scenario_dir / "drone_sensor_samples.csv")
        assert set(samples["scenario_id"]) == {row.scenario_id}
        assert (output / row.drone_sensor_samples).is_file()
        assert (scenario_dir / "source_label.json").is_file()
        assert (scenario_dir / "mass_balance.json").is_file()
        assert (scenario_dir / "run_report.json").is_file()

    for split, expected_ids in result.split_ids.items():
        saved_ids = {
            line
            for line in (output / f"{split}_scenarios.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        }
        assert saved_ids == set(expected_ids)
