from __future__ import annotations

import json

import pandas as pd
import pytest

from voxmaps_sim.exports import aggregate_voxel_observations, export_run


@pytest.fixture
def sample_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "elapsed_time_s": [0.0, 1.0, 2.0],
            "latitude": [28.5, 28.5001, 28.5002],
            "longitude": [77.2, 77.2001, 77.2002],
            "altitude_m": [10.0, 11.0, 12.0],
            "enu_x_m": [0.0, 10.0, 20.0],
            "enu_y_m": [0.0, 10.0, 20.0],
            "enu_z_m": [10.0, 11.0, 12.0],
            "voxel_id": ["0:0:1", "0:0:1", None],
            "voxel_x_index": [0, 0, -1],
            "voxel_y_index": [0, 0, -1],
            "voxel_z_index": [1, 1, -1],
            "voxel_center_x_m": [5.0, 5.0, float("nan")],
            "voxel_center_y_m": [5.0, 5.0, float("nan")],
            "voxel_center_z_m": [15.0, 15.0, float("nan")],
            "wind_speed_mps": [3.0, 3.0, 3.0],
            "wind_direction_from_deg": [270.0, 270.0, 270.0],
            "wind_source": ["synthetic"] * 3,
            "pm25_true_ug_m3": [20.0, 22.0, 21.0],
            "coarse_pm_true_ug_m3": [15.0, 16.0, 15.0],
            "pm10_true_ug_m3": [35.0, 38.0, 36.0],
            "pm25_sensor_ug_m3": [19.0, 21.0, 20.0],
            "pm10_sensor_ug_m3": [34.0, 37.0, 35.0],
            "quality_flags": ["synthetic_wind"] * 3,
            "data_quality_score": [0.85] * 3,
            "scenario_id": ["test"] * 3,
        }
    )


def test_voxel_aggregation_has_required_statistics_and_excludes_no_voxel(
    sample_rows: pd.DataFrame,
) -> None:
    voxels = aggregate_voxel_observations(sample_rows)
    assert list(voxels["voxel_id"]) == ["0:0:1"]
    row = voxels.iloc[0]
    assert row["observation_count"] == 2
    assert row["pm25_true_ug_m3_mean"] == pytest.approx(21.0)
    assert row["pm10_true_ug_m3_percentile_95"] == pytest.approx(37.85)
    assert row["data_quality_score"] == pytest.approx(0.85)
    assert 0.0 < row["confidence_score"] < row["data_quality_score"]


def test_export_run_writes_real_parquet_geojson_and_self_contained_html(
    tmp_path, sample_rows: pd.DataFrame
) -> None:
    paths = export_run(
        tmp_path,
        sample_rows,
        source_label={
            "source_latitude": 28.5,
            "source_longitude": 77.2,
            "source_enu_x_m": -10.0,
            "source_enu_y_m": 0.0,
            "effective_release_height_m": 70.0,
        },
        scenario_metadata={
            "scenario_id": "test",
            "coordinates": {"reference_latitude": 28.5, "reference_longitude": 77.2},
        },
    )

    required = {
        "drone_sensor_samples.csv",
        "voxel_observations.csv",
        "voxel_map.geojson",
        "flight_path.geojson",
        "ground_truth_voxels.parquet",
        "source_label.json",
        "scenario_metadata.json",
        "mass_balance.json",
        "run_report.json",
        "visualization.html",
        "artifact_manifest.json",
    }
    assert required <= {path.name for path in tmp_path.iterdir()}
    assert pd.read_parquet(paths["ground_truth_voxels"]).shape[0] == 1

    voxel_geojson = json.loads(paths["voxel_map"].read_text(encoding="utf-8"))
    assert voxel_geojson["type"] == "FeatureCollection"
    assert len(voxel_geojson["features"]) == 1
    lon, lat, _ = voxel_geojson["features"][0]["geometry"]["coordinates"]
    assert lon == pytest.approx(77.20005)
    assert lat == pytest.approx(28.50005)

    html = paths["visualization"].read_text(encoding="utf-8")
    assert "Plotly.newPlot" in html
    # The minified inline Plotly bundle contains CDN defaults as string data,
    # but the document must not load its runtime through an external script.
    assert '<script src="https://cdn.plot.ly' not in html
    assert "Research output" in html
