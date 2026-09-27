from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
import pytest
from typer.testing import CliRunner

from voxmaps_sim.cli import app
from voxmaps_sim.config import SimulationConfig, save_resolved_config
from voxmaps_sim.pipeline import run_simulation


def _flight_csv(path: Path, *, include_wind: bool = False) -> Path:
    rows = 5
    frame: dict[str, object] = {
        "time(millisecond)": np.arange(rows) * 1_000,
        "datetime(utc)": pd.date_range(
            "2026-04-14T00:00:00Z", periods=rows, freq="1s"
        ).strftime("%Y-%m-%d %H:%M:%S"),
        "latitude": np.full(rows, 28.5685),
        "longitude": np.full(rows, 77.2773),
        # Four metres above the local flat-ground reference.
        "height_above_takeoff(feet)": np.full(rows, 4.0 / 0.3048),
    }
    if include_wind:
        frame["wind_speed(mph)"] = np.full(rows, 2.0 / 0.44704)
        frame["wind_direction(degrees)"] = np.full(rows, 270.0)
    pd.DataFrame(frame).to_csv(path, index=False)
    return path


def _small_config(
    *,
    seed: int = 123,
    pm25_emission_g_s: float = 0.2,
    pm10_total_emission_g_s: float = 0.5,
    wind_mode: str = "synthetic",
) -> SimulationConfig:
    return SimulationConfig.model_validate(
        {
            "project": {
                "name": "voxmaps-pipeline-test",
                "scenario_id": "pipeline-test",
                "random_seed": seed,
            },
            "wind": {
                "mode": wind_mode,
                "minimum_numeric_coverage": 0.8,
                "maximum_interpolation_gap_s": 2.0,
                "synthetic": {
                    "base_speed_mps": 0.0,
                    "base_direction_from_deg": 270.0,
                    "speed_variability_mps": 0.0,
                    "direction_variability_deg": 0.0,
                    "gust_period_s": 60.0,
                },
            },
            "source": {
                "name": "test-stack",
                "offset_east_from_flight_centre_m": 0.0,
                "offset_north_from_flight_centre_m": 0.0,
                "stack_height_m": 4.0,
                "plume_rise_m": 0.0,
                "pm25_emission_g_s": pm25_emission_g_s,
                "pm10_total_emission_g_s": pm10_total_emission_g_s,
                "emission_start_s": 0.0,
                "emission_end_s": None,
            },
            "simulation": {
                "timestep_s": 1.0,
                "trajectory_sample_interval_s": 1.0,
                "particles_per_timestep": 4,
                "horizontal_padding_m": 20.0,
                "vertical_min_m": 0.0,
                "vertical_max_m": 20.0,
                "maximum_particles": 1_000,
            },
            "dispersion": {
                "horizontal_diffusivity_m2_s": 0.0,
                "vertical_diffusivity_m2_s": 0.0,
                "fine_settling_velocity_mps": 0.0,
                "coarse_settling_velocity_mps": 0.0,
                "fine_deposition_velocity_mps": 0.0,
                "coarse_deposition_velocity_mps": 0.0,
                "fine_decay_rate_s": 0.0,
                "coarse_decay_rate_s": 0.0,
            },
            "voxel": {"size_x_m": 20.0, "size_y_m": 20.0, "size_z_m": 10.0},
            "background": {"pm25_ug_m3": 5.0, "coarse_pm_ug_m3": 3.0},
            "sensor": {
                "sample_interval_s": 1.0,
                "response_time_pm25_s": 0.0,
                "response_time_pm10_s": 0.0,
                "transport_delay_s": 0.0,
                "additive_noise_std_ug_m3": 0.25,
                "multiplicative_noise_std_fraction": 0.01,
                "dropout_probability": 0.0,
                "quantization_ug_m3": 0.1,
            },
        }
    )


def test_same_seed_produces_identical_pipeline_samples(tmp_path):
    flight = _flight_csv(tmp_path / "flight.csv")
    config = _small_config(seed=314)

    first = run_simulation(
        flight, config, tmp_path / "first", write_visualization=False
    )
    second = run_simulation(
        flight, config, tmp_path / "second", write_visualization=False
    )

    assert_frame_equal(first.samples, second.samples, check_exact=True)
    assert_frame_equal(first.ground_truth_voxels, second.ground_truth_voxels)
    assert first.mass_balance == second.mass_balance
    # The pipeline resolves generated coordinates on a deep copy.
    assert config.source.latitude is None
    assert config.source.longitude is None


def test_source_off_produces_background_only_ground_truth(tmp_path):
    flight = _flight_csv(tmp_path / "flight.csv")
    config = _small_config(pm25_emission_g_s=0.0, pm10_total_emission_g_s=0.0)

    result = run_simulation(
        flight, config, tmp_path / "source-off", write_visualization=False
    )

    np.testing.assert_allclose(result.samples["pm25_true_ug_m3"], 5.0)
    np.testing.assert_allclose(result.samples["coarse_pm_true_ug_m3"], 3.0)
    np.testing.assert_allclose(result.samples["pm10_true_ug_m3"], 8.0)
    assert result.mass_balance["total_emitted_fine_mass_g"] == 0.0
    assert result.mass_balance["total_emitted_coarse_mass_g"] == 0.0
    assert result.mass_balance["numerical_mass_balance_error_g"] == 0.0


def test_pipeline_writes_required_artifacts_and_balances_mass(tmp_path):
    flight = _flight_csv(tmp_path / "flight.csv")
    output = tmp_path / "complete-run"

    result = run_simulation(flight, _small_config(), output, write_visualization=True)

    required = {
        "data_quality_report.json",
        "column_mapping.json",
        "normalized_flight.csv",
        "drone_sensor_samples.csv",
        "voxel_observations.csv",
        "voxel_map.geojson",
        "flight_path.geojson",
        "ground_truth_voxels.parquet",
        "visualization.html",
        "source_label.json",
        "scenario_metadata.json",
        "mass_balance.json",
        "run_report.json",
        "artifact_manifest.json",
    }
    assert required.issubset({path.name for path in output.iterdir()})
    assert abs(result.mass_balance["numerical_mass_balance_error_g"]) < 1e-12
    assert result.run_report["mass_balance_within_tolerance"] is True
    assert result.mass_balance["total_emitted_fine_mass_g"] == pytest.approx(0.8)
    assert result.mass_balance["total_emitted_coarse_mass_g"] == pytest.approx(1.2)
    assert (result.samples["pm10_true_ug_m3"] >= result.samples["pm25_true_ug_m3"]).all()
    saved_report = json.loads((output / "run_report.json").read_text(encoding="utf-8"))
    assert saved_report["status"] == "success"
    assert saved_report["wind_source_used"] == "synthetic"


def test_preflight_hold_first_wind_advects_warmup_while_none_is_calm(tmp_path):
    flight = _flight_csv(tmp_path / "flight.csv")
    calm_config = _small_config()
    calm_config.simulation.warmup_s = 1.0
    calm_config.simulation.preflight_wind_policy = "none"
    calm_config.simulation.horizontal_padding_m = 40.0
    calm_config.wind.synthetic.base_speed_mps = 20.0

    held_config = calm_config.model_copy(deep=True)
    held_config.simulation.preflight_wind_policy = "hold_first_wind"

    calm = run_simulation(
        flight, calm_config, tmp_path / "calm-warmup", write_visualization=False
    )
    held = run_simulation(
        flight, held_config, tmp_path / "held-wind-warmup", write_visualization=False
    )

    # Both runs emit one extra second of mass before the first flight sample.
    assert calm.mass_balance["total_emitted_fine_mass_g"] == pytest.approx(1.0)
    assert held.mass_balance["total_emitted_fine_mass_g"] == pytest.approx(1.0)
    # Calm preflight air leaves the warmup parcels in the source/drone voxel;
    # hold_first_wind moves those parcels one 20 m voxel east before t=0.
    assert calm.samples.loc[0, "fine_parcel_count"] > 0
    assert held.samples.loc[0, "fine_parcel_count"] == 0
    assert calm.metadata["simulation"]["preflight_wind_policy"] == "none"
    assert held.metadata["simulation"]["preflight_wind_policy"] == "hold_first_wind"


def test_cli_measured_wind_failure_returns_nonzero(tmp_path):
    flight = _flight_csv(tmp_path / "flight-without-wind.csv")
    config_path = save_resolved_config(
        _small_config(wind_mode="measured"), tmp_path / "measured.yaml"
    )
    output = tmp_path / "measured-output"

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--flight",
            str(flight),
            "--config",
            str(config_path),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 2
    assert "Simulation failed" in result.output
    assert "Measured atmospheric wind is unavailable" in result.output
    assert "not atmospheric wind" in result.output
