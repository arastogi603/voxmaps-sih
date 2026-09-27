from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from voxmaps_sim.config import SyntheticWindConfig, WindConfig
from voxmaps_sim.wind import (
    AutoWindSelectionRequiredError,
    MeasuredWindUnavailableError,
    assess_measured_wind,
    build_wind_series,
    generate_synthetic_wind,
    interpolate_measured_wind,
    meteorological_to_uv,
)


def test_wind_from_west_moves_pollution_east():
    east, north = meteorological_to_uv(5.0, 270.0)
    assert east == pytest.approx(5.0)
    assert north == pytest.approx(0.0, abs=1e-12)


def test_wind_from_north_moves_pollution_south():
    east, north = meteorological_to_uv(5.0, 0.0)
    assert east == pytest.approx(0.0, abs=1e-12)
    assert north == pytest.approx(-5.0)


def test_auto_mode_refuses_silent_synthetic_fallback():
    flight = pd.DataFrame(
        {
            "elapsed_time_s": [0.0, 1.0, 2.0],
            "wind_speed_mps": [np.nan, np.nan, np.nan],
            "wind_direction_from_deg": [np.nan, np.nan, np.nan],
            "xSpeed(mph)": [4.0, 5.0, 6.0],
        }
    )
    with pytest.raises(AutoWindSelectionRequiredError, match="will not silently switch"):
        build_wind_series(flight, WindConfig(mode="auto"))


def test_measured_mode_requires_coverage():
    flight = pd.DataFrame(
        {
            "elapsed_time_s": [0.0, 1.0, 2.0, 3.0],
            "wind_speed_mps": [2.0, np.nan, np.nan, 2.0],
            "wind_direction_from_deg": [270.0, np.nan, np.nan, 270.0],
        }
    )
    assessment = assess_measured_wind(flight, 0.8)
    assert assessment.paired_numeric_coverage == pytest.approx(0.5)
    assert assessment.usable is False
    with pytest.raises(MeasuredWindUnavailableError, match="50.0%"):
        build_wind_series(flight, WindConfig(mode="measured"))


def test_measured_interpolated_uses_sparse_logged_timeline_without_synthetic():
    flight = pd.DataFrame(
        {
            "elapsed_time_s": [0.0, 10.0, 20.0, 30.0],
            "wind_speed_mps": [np.nan, 4.0, 4.0, np.nan],
            "wind_direction_from_deg": [np.nan, 270.0, 180.0, np.nan],
        }
    )
    target = np.array([0.0, 10.0, 15.0, 20.0, 30.0])
    wind = build_wind_series(
        flight,
        WindConfig(
            mode="measured_interpolated",
            minimum_numeric_coverage=0.8,
            maximum_interpolation_gap_s=2.0,
        ),
        target,
    )

    assert np.isfinite(wind["wind_speed_mps"]).all()
    assert set(wind["wind_source"]) == {"measured"}
    assert wind["wind_interpolated"].tolist() == [True, False, True, False, True]
    assert wind.loc[0, "wind_direction_from_deg"] == pytest.approx(270.0)
    assert wind.loc[1, "wind_direction_from_deg"] == pytest.approx(270.0)
    assert wind.loc[2, "wind_direction_from_deg"] == pytest.approx(225.0)
    assert wind.loc[3, "wind_direction_from_deg"] == pytest.approx(180.0)
    assert wind.loc[4, "wind_direction_from_deg"] == pytest.approx(180.0)
    assert set(wind.loc[wind["wind_interpolated"], "quality_flags"]) == {
        "interpolation"
    }


def test_measured_timestamp_rounding_keeps_exact_observation_provenance():
    logged_time = np.nextafter(15.3, -np.inf)
    flight = pd.DataFrame(
        {
            "elapsed_time_s": [logged_time, 15.4],
            "wind_speed_mps": [2.0, 3.0],
            "wind_direction_from_deg": [270.0, 280.0],
        }
    )
    wind = build_wind_series(
        flight,
        WindConfig(mode="measured_interpolated"),
        np.array([15.3]),
    )

    assert wind.loc[0, "wind_interpolated"] == np.bool_(False)
    assert wind.loc[0, "quality_flags"] == ""
    assert wind.loc[0, "wind_speed_mps"] == pytest.approx(2.0)
    assert wind.loc[0, "wind_direction_from_deg"] == pytest.approx(270.0)


def test_long_interpolation_gap_remains_missing_and_is_flagged():
    flight = pd.DataFrame(
        {
            "elapsed_time_s": [0.0, 100.0],
            "wind_speed_mps": [3.0, 3.0],
            "wind_direction_from_deg": [270.0, 270.0],
        }
    )
    wind = interpolate_measured_wind(
        flight, np.array([0.0, 50.0, 100.0]), maximum_interpolation_gap_s=30.0
    )
    assert np.isnan(wind.loc[1, "wind_speed_mps"])
    assert wind.loc[1, "wind_gap_exceeded"]
    assert "interpolation_gap_exceeded" in wind.loc[1, "quality_flags"]
    assert "missing_wind" in wind.loc[1, "quality_flags"]


def test_short_gap_interpolates_components_and_flags_row():
    flight = pd.DataFrame(
        {
            "elapsed_time_s": [0.0, 10.0],
            "wind_speed_mps": [4.0, 4.0],
            "wind_direction_from_deg": [350.0, 10.0],
        }
    )
    wind = interpolate_measured_wind(
        flight, np.array([5.0]), maximum_interpolation_gap_s=30.0
    )
    assert wind.loc[0, "wind_speed_mps"] > 3.9
    assert wind.loc[0, "wind_direction_from_deg"] == pytest.approx(0.0, abs=1e-8)
    assert wind.loc[0, "wind_interpolated"]
    assert wind.loc[0, "quality_flags"] == "interpolation"


def test_synthetic_wind_is_deterministic_and_explicitly_labelled():
    times = np.arange(5.0)
    config = SyntheticWindConfig(
        base_speed_mps=3.0,
        base_direction_from_deg=270.0,
        speed_variability_mps=0.5,
        direction_variability_deg=10.0,
        gust_period_s=60.0,
    )
    first = generate_synthetic_wind(times, config, random_seed=7)
    second = generate_synthetic_wind(times, config, random_seed=7)
    pd.testing.assert_frame_equal(first, second)
    assert set(first["wind_source"]) == {"synthetic"}
    assert set(first["quality_flags"]) == {"synthetic_wind"}
