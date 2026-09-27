from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from voxmaps_sim.config import SimulationConfig
from voxmaps_sim.particles import COARSE, FINE, ParticleSet
from voxmaps_sim.trace import (
    SENSOR_LOG_COLUMNS,
    SimulationCancelled,
    load_playback_frame,
    read_trace_summary,
    reconstruct_sensor_row,
    run_trace_simulation,
    sample_sensor_region,
)


def _flight(path: Path, *, measured_wind: bool = False) -> Path:
    rows = 6
    frame: dict[str, object] = {
        "time(millisecond)": np.arange(rows) * 1_000,
        "datetime(utc)": pd.date_range(
            "2026-04-14T09:28:00Z", periods=rows, freq="1s"
        ).strftime("%Y-%m-%d %H:%M:%S"),
        "latitude": np.full(rows, 28.5685),
        "longitude": np.full(rows, 77.2773),
        "height_above_takeoff(feet)": np.full(rows, 4.0 / 0.3048),
        "altitude_above_seaLevel(feet)": np.full(rows, 220.0 / 0.3048),
        "speed(mph)": np.full(rows, 3.0 / 0.44704),
        " zSpeed(mph)": np.zeros(rows),
        " compass_heading(degrees)": np.full(rows, 90.0),
        " pitch(degrees)": np.zeros(rows),
        " roll(degrees)": np.zeros(rows),
        "satellites": np.full(rows, 17),
        "gpslevel": np.full(rows, 5),
        "flycState": np.full(rows, "GPS_Atti"),
        "rc_throttle": np.zeros(rows),
    }
    if measured_wind:
        frame["wind_speed(mph)"] = np.full(rows, 2.0 / 0.44704)
        frame["wind_direction(degrees)"] = np.full(rows, 270.0)
    pd.DataFrame(frame).to_csv(path, index=False)
    return path


def _config(*, seed: int = 7, source_on: bool = True) -> SimulationConfig:
    return SimulationConfig.model_validate(
        {
            "project": {"scenario_id": "trace-test", "random_seed": seed},
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
                "name": "test-stack",
                "offset_east_from_flight_centre_m": 0.0,
                "offset_north_from_flight_centre_m": 0.0,
                "stack_height_m": 4.0,
                "plume_rise_m": 0.0,
                "pm25_emission_g_s": 0.2 if source_on else 0.0,
                "pm10_total_emission_g_s": 0.5 if source_on else 0.0,
            },
            "simulation": {
                "timestep_s": 1.0,
                "particles_per_timestep": 4,
                "horizontal_padding_m": 20.0,
                "vertical_max_m": 20.0,
                "maximum_particles": 1_000,
                "playback_frame_interval_s": 1.0,
                "visualization_parcel_limit": 100,
            },
            "dispersion": {
                "horizontal_diffusivity_m2_s": 0.0,
                "vertical_diffusivity_m2_s": 0.0,
                "fine_settling_velocity_mps": 0.0,
                "coarse_settling_velocity_mps": 0.0,
                "fine_deposition_velocity_mps": 0.0,
                "coarse_deposition_velocity_mps": 0.0,
            },
            "background": {"pm25_ug_m3": 5.0, "coarse_pm_ug_m3": 3.0},
            "sensor": {
                "sample_interval_s": 1.0,
                "sampling_region_size_x_m": 20.0,
                "sampling_region_size_y_m": 20.0,
                "sampling_region_size_z_m": 10.0,
                "response_time_pm25_s": 0.0,
                "response_time_pm10_s": 0.0,
                "transport_delay_s": 0.0,
                "additive_noise_std_ug_m3": 0.0,
                "multiplicative_noise_std_fraction": 0.0,
                "quantization_ug_m3": 0.0,
            },
        }
    )


def test_rectangular_sensor_region_uses_exact_contributors_and_pm10_identity() -> None:
    particles = ParticleSet.from_arrays(
        [[0, 0, 0], [4.9, 4.9, 4.9], [5.1, 0, 0]],
        [0.001, 0.002, 9.0],
        [FINE, COARSE, FINE],
        parcel_id=[10, 11, 12],
    )
    sample = sample_sensor_region(
        particles,
        [0, 0, 0],
        [10, 10, 10],
        background_pm25_ug_m3=1.0,
        background_coarse_pm_ug_m3=2.0,
    )
    assert sample.parcel_id.tolist() == [10, 11]
    assert sample.fine_count == sample.coarse_count == 1
    assert sample.pm25_true_ug_m3 == pytest.approx(2.0)
    assert sample.coarse_pm_true_ug_m3 == pytest.approx(4.0)
    assert sample.pm10_true_ug_m3 == pytest.approx(
        sample.pm25_true_ug_m3 + sample.coarse_pm_true_ug_m3
    )


def test_two_file_hdf5_contract_reload_and_reconstruction(tmp_path: Path) -> None:
    flight = _flight(tmp_path / "flight.csv")
    output = tmp_path / "scenario"
    result = run_trace_simulation(flight, _config(), output)

    assert {path.name for path in output.iterdir()} == {
        "trace-test_simulation_trace.h5",
        "trace-test_simulated_drone_sensor_log.csv",
    }
    saved = pd.read_csv(result.csv_path)
    assert list(saved.columns) == SENSOR_LOG_COLUMNS
    np.testing.assert_allclose(
        saved["pm10_true_ug_m3"],
        saved["pm25_true_ug_m3"] + saved["coarse_pm_true_ug_m3"],
    )
    assert set(saved["wind_source"]) == {"synthetic_fallback"}
    assert saved["source_row_index"].tolist() == list(range(6))
    assert saved["uptime_ms"].tolist() == list(np.arange(6) * 1_000)

    with h5py.File(result.h5_path, "r") as handle:
        assert handle["playback/position_m"].compression == "gzip"
        assert handle["contributors/mass_g"].compression == "gzip"
        assert handle["sensor/pm25_true_ug_m3"].attrs["units"] == "ug/m3"
        assert len(handle["parcels/static/parcel_id"]) > 0
        assert np.all(handle["parcels/static/parcel_id"][...] >= 0)
        assert "speed_text" in handle["wind/raw"]
        assert handle["wind/used"].attrs["interpolation_method"] == "linear_vector"

    summary = read_trace_summary(result.h5_path)
    assert summary["simulation_id"] == result.simulation_id
    assert summary["sample_count"] == 6
    assert summary["duration_s"] == pytest.approx(5.0)
    assert len(summary["flight_path"]) == 6
    assert summary["source"]["stack_height_m"] == pytest.approx(4.0)
    frame = load_playback_frame(result.h5_path, result.frame_count - 1)
    assert frame["elapsed_time_s"] == pytest.approx(5.0)
    assert frame["drone"]["z"] == pytest.approx(4.0)
    assert frame["wind_source"] == "synthetic_fallback"
    assert frame["numerical_particle_count"] >= frame["rendered_particle_count"]
    reconstructed = reconstruct_sensor_row(result.h5_path, 4)
    assert reconstructed["matches"] is True


def test_trace_is_deterministic_for_same_seed(tmp_path: Path) -> None:
    flight = _flight(tmp_path / "flight.csv")
    first = run_trace_simulation(flight, _config(seed=91), tmp_path / "first")
    second = run_trace_simulation(flight, _config(seed=91), tmp_path / "second")
    assert first.simulation_id == second.simulation_id
    assert_frame_equal(first.sensor_log, second.sensor_log, check_exact=True)
    assert first.mass_balance == second.mass_balance


def test_cancellation_leaves_no_partial_output(tmp_path: Path) -> None:
    flight = _flight(tmp_path / "flight.csv")
    output = tmp_path / "cancelled"
    output.mkdir()
    calls = 0

    def cancel() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 4

    with pytest.raises(SimulationCancelled):
        run_trace_simulation(flight, _config(), output, cancel_check=cancel)
    assert list(output.iterdir()) == []


def test_source_off_trace_is_valid_and_background_only(tmp_path: Path) -> None:
    result = run_trace_simulation(
        _flight(tmp_path / "flight.csv"),
        _config(source_on=False),
        tmp_path / "off",
    )
    np.testing.assert_allclose(result.sensor_log["pm25_true_ug_m3"], 5.0)
    np.testing.assert_allclose(result.sensor_log["coarse_pm_true_ug_m3"], 3.0)
    assert reconstruct_sensor_row(result.h5_path, 0)["matches"] is True


def test_preflight_hold_uses_first_nonnegative_wind(tmp_path: Path) -> None:
    config = _config()
    config.simulation.warmup_s = 1.0
    config.simulation.preflight_wind_policy = "hold_first_wind"
    config.wind.synthetic.base_speed_mps = 4.0
    config.wind.synthetic.speed_variability_mps = 2.0
    config.wind.synthetic.direction_variability_deg = 40.0
    result = run_trace_simulation(
        _flight(tmp_path / "flight.csv"), config, tmp_path / "held"
    )
    with h5py.File(result.h5_path, "r") as handle:
        used_time = handle["wind/used/elapsed_time_s"][...]
        first_flight = int(np.flatnonzero(used_time >= 0.0)[0])
        expected_offset = np.array(
            [
                handle["wind/used/wind_u_used_mps"][first_flight],
                handle["wind/used/wind_v_used_mps"][first_flight],
                0.0,
            ]
        )
        first_frame_end = int(handle["playback/offsets"][1])
        positions = handle["playback/position_m"][:first_frame_end]
        parcel_ids = handle["playback/parcel_id"][:first_frame_end]
        initial = handle["parcels/static/initial_position_m"][parcel_ids]
    np.testing.assert_allclose(
        positions - initial,
        np.repeat(expected_offset[None, :], len(positions), axis=0),
        rtol=1e-6,
        atol=1e-6,
    )


def test_default_trace_samples_every_irregular_flight_row_with_exact_linkage(
    tmp_path: Path,
) -> None:
    flight = tmp_path / "irregular.csv"
    uptime_ms = [0, 120, 470, 470, 1300]
    pd.DataFrame(
        {
            "time(millisecond)": uptime_ms,
            "datetime(utc)": [
                "2026-04-14T09:28:00.000Z",
                "2026-04-14T09:28:00.120Z",
                "2026-04-14T09:28:00.470Z",
                "2026-04-14T09:28:00.470Z",
                "2026-04-14T09:28:01.300Z",
            ],
            "latitude": [28.56850, 28.56851, 28.56852, 28.56853, 28.56854],
            "longitude": [77.27730, 77.27731, 77.27732, 77.27733, 77.27734],
            "height_above_takeoff(feet)": np.full(5, 4.0 / 0.3048),
        }
    ).to_csv(flight, index=False)

    result = run_trace_simulation(flight, _config(), tmp_path / "irregular-output")
    assert len(result.sensor_log) == 5
    assert result.sensor_log["sample_id"].tolist() == [0, 1, 2, 3, 4]
    assert result.sensor_log["source_row_index"].tolist() == [0, 1, 2, 3, 4]
    assert result.sensor_log["uptime_ms"].tolist() == pytest.approx(uptime_ms)
    assert result.sensor_log["elapsed_time_s"].tolist() == pytest.approx(
        [0.0, 0.12, 0.47, 0.47, 1.3]
    )
    assert result.sensor_log["latitude_deg"].tolist() == pytest.approx(
        [28.56850, 28.56851, 28.56852, 28.56853, 28.56854]
    )
    with h5py.File(result.h5_path, "r") as handle:
        assert handle["sensor"].attrs["output_sampling_mode"] == "every_flight_sample"
        assert handle["trajectory/source_row_index"][...].tolist() == [0, 1, 2, 3, 4]
        # Two configured physics intervals (0-1 s and 1-1.3 s), not one
        # emission batch for each of the five arbitrary telemetry times.
        assert len(handle["parcels/static/parcel_id"]) == 8


def test_hdf5_preserves_exact_raw_wind_placeholder_text(tmp_path: Path) -> None:
    flight = tmp_path / "placeholder-wind.csv"
    exact_speed_cells = [
        "NA",
        "Available with Enterprise Subscription",
        " placeholder MixedCase ",
        "",
    ]
    exact_direction_cells = ["270", "271", "not available", "  273  "]
    pd.DataFrame(
        {
            "time(millisecond)": [0, 1000, 2000, 3000],
            "datetime(utc)": pd.date_range(
                "2026-04-14T09:28:00Z", periods=4, freq="1s"
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "latitude": np.full(4, 28.5685),
            "longitude": np.full(4, 77.2773),
            "height_above_takeoff(feet)": np.full(4, 10.0),
            "wind_speed(mph)": exact_speed_cells,
            "wind_direction(degrees)": exact_direction_cells,
        }
    ).to_csv(flight, index=False)

    result = run_trace_simulation(flight, _config(), tmp_path / "placeholder-output")
    with h5py.File(result.h5_path, "r") as handle:
        stored_speed = [value.decode("utf-8") for value in handle["wind/raw/speed_text"][...]]
        stored_direction = [
            value.decode("utf-8") for value in handle["wind/raw/direction_text"][...]
        ]
        assert stored_speed == exact_speed_cells
        assert stored_direction == exact_direction_cells
        assert np.isnan(handle["wind/raw/wind_speed_mps"][...]).all()
        assert not handle["wind/raw/paired_valid"][...].any()
