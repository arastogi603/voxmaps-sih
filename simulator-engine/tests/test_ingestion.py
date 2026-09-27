from __future__ import annotations

import json

import pandas as pd
import pytest

from voxmaps_sim.ingestion import (
    FEET_TO_METRES,
    MILES_PER_HOUR_TO_METRES_PER_SECOND,
    FlightDataError,
    load_flight_csv,
)


def test_csv_parsing_column_mapping_and_unit_normalization(tmp_path):
    source = tmp_path / "mapped.csv"
    pd.DataFrame(
        {
            "clock_ms": [1_000, 2_000, 3_000],
            "when": [
                "2026-04-14 00:00:01",
                "2026-04-14 00:00:02",
                "2026-04-14 00:00:03",
            ],
            "lat_custom": [28.0, 28.0001, 28.0002],
            "lon_custom": [77.0, 77.0001, 77.0002],
            "height_ft": [10.0, 20.0, 30.0],
            "air_mph": [10.0, 10.0, 10.0],
            "air_from": [270.0, 270.0, 270.0],
        }
    ).to_csv(source, index=False)

    result = load_flight_csv(
        source,
        mapping={
            "elapsed_time": "clock_ms",
            "timestamp_utc": "when",
            "latitude": "lat_custom",
            "longitude": "lon_custom",
            "altitude": "height_ft",
            "wind_speed": "air_mph",
            "wind_direction": "air_from",
        },
    )

    assert result.mapping["latitude"] == "lat_custom"
    assert result.normalized_flight["elapsed_time_s"].tolist() == [0.0, 1.0, 2.0]
    assert result.normalized_flight.loc[0, "altitude_m"] == pytest.approx(
        10.0 * FEET_TO_METRES
    )
    assert result.normalized_flight.loc[0, "wind_speed_mps"] == pytest.approx(
        10.0 * MILES_PER_HOUR_TO_METRES_PER_SECOND
    )
    assert result.report["wind"]["measured_wind_usable"] is True


def test_placeholder_wind_is_rejected_and_drone_velocity_is_not_used(tmp_path):
    source = tmp_path / "placeholder.csv"
    pd.DataFrame(
        {
            "time(millisecond)": [0, 100, 200],
            "datetime(utc)": [
                "2026-04-14 00:00:00",
                "2026-04-14 00:00:00.1",
                "2026-04-14 00:00:00.2",
            ],
            "latitude": [28.0, 28.0, 28.0],
            "longitude": [77.0, 77.0, 77.0],
            "height_above_takeoff(feet)": [0.0, 1.0, 2.0],
            "wind_speed(mph)": [
                "Available with Enterprise subscription",
                "Available with Enterprise subscription",
                "Available with Enterprise subscription",
            ],
            "wind_direction(degrees)": [None, None, None],
            " xSpeed(mph)": [5.0, 6.0, 7.0],
            " ySpeed(mph)": [2.0, 2.0, 2.0],
            " compass_heading(degrees)": [90.0, 90.0, 90.0],
        }
    ).to_csv(source, index=False)

    result = load_flight_csv(source)

    assert result.report["wind"]["speed"]["placeholder_count"] == 3
    assert result.report["wind"]["paired_numeric_coverage"] == 0.0
    assert result.report["wind"]["measured_wind_usable"] is False
    assert result.report["wind"]["drone_velocity_used_as_wind"] is False
    assert result.normalized_flight["wind_speed_mps"].isna().all()
    assert result.mapping["wind_speed"] == "wind_speed(mph)"


def test_explicit_drone_velocity_wind_mapping_is_refused(tmp_path):
    source = tmp_path / "bad-mapping.csv"
    pd.DataFrame(
        {
            "time(millisecond)": [0, 100],
            "latitude": [28.0, 28.0],
            "longitude": [77.0, 77.0],
            "height_above_takeoff(feet)": [0.0, 1.0],
            " xSpeed(mph)": [5.0, 6.0],
            " compass_heading(degrees)": [90.0, 90.0],
        }
    ).to_csv(source, index=False)

    with pytest.raises(FlightDataError, match="not atmospheric"):
        load_flight_csv(
            source,
            mapping={
                "wind_speed": "xSpeed(mph)",
                "wind_direction": "compass_heading(degrees)",
            },
        )


def test_validation_artifacts_are_written(tmp_path):
    source = tmp_path / "flight.csv"
    output = tmp_path / "validation"
    pd.DataFrame(
        {
            "time(millisecond)": [1000, 1100],
            "latitude": [28.0, 28.0001],
            "longitude": [77.0, 77.0001],
            "height_above_takeoff(feet)": [0.0, 10.0],
        }
    ).to_csv(source, index=False)

    result = load_flight_csv(source, output_dir=output)

    assert (output / "normalized_flight.csv").is_file()
    mapping = json.loads((output / "column_mapping.json").read_text(encoding="utf-8"))
    report = json.loads(
        (output / "data_quality_report.json").read_text(encoding="utf-8")
    )
    assert mapping["elapsed_time"] == "time(millisecond)"
    assert report["source_file"]["source_was_modified"] is False
    assert len(result.normalized_flight) == 2


def test_sfd_workbook_uses_complete_wind_and_preserves_provenance(tmp_path):
    source = tmp_path / "flight-sfd.xlsx"
    frame = pd.DataFrame(
        {
            "elapsed_time_ms": [1100, 1200, 1300],
            "utc_datetime": [
                "2026-04-14T03:58:29Z",
                "2026-04-14T03:58:29.100Z",
                "2026-04-14T03:58:29.200Z",
            ],
            "latitude": [28.5685, 28.5686, 28.5687],
            "longitude": [77.2773, 77.2774, 77.2775],
            "altitude(feet)": [750.0, 751.0, 752.0],
            "height_above_takeoff(feet)": [0.0, 1.0, 2.0],
            "wind_speed_original(mph)": [None, 5.0, None],
            "wind_direction_original_from(degrees)": [None, 270.0, None],
            "wind_speed_complete(mph)": [4.0, 5.0, 6.0],
            "wind_direction_complete_from(degrees)": [260.0, 270.0, 280.0],
            "wind_data_source": [
                "simulated_leading_gap",
                "original_airdata_estimate",
                "interpolated_short_gap",
            ],
            "wind_confidence": [0.25, 1.0, 0.8],
            "wind_gap_id": ["G001", "", "G002"],
            "calm_flag": [False, False, False],
        }
    )
    with pd.ExcelWriter(source, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Complete Wind Data", index=False)

    result = load_flight_csv(source, min_wind_coverage=0.8)

    assert result.mapping["altitude"] == "altitude(feet)"
    assert result.mapping["wind_speed"] == "wind_speed_complete(mph)"
    assert result.mapping["wind_direction"] == "wind_direction_complete_from(degrees)"
    assert result.report["source_file"]["input_format"] == "sfd_xlsx"
    assert result.report["source_file"]["worksheet"] == "Complete Wind Data"
    assert result.report["wind"]["paired_numeric_coverage"] == 1.0
    assert result.report["wind"]["sfd_original_row_count"] == 1
    assert result.report["wind"]["sfd_generated_or_interpolated_row_count"] == 2
    assert result.normalized_flight["wind_speed_original_mps"].isna().sum() == 2
    assert result.normalized_flight["wind_input_source"].tolist() == frame[
        "wind_data_source"
    ].tolist()
    assert result.normalized_flight["altitude_m"].iloc[0] == pytest.approx(
        750.0 * FEET_TO_METRES
    )


def test_xlsx_without_sfd_sheet_is_rejected(tmp_path):
    source = tmp_path / "not-sfd.xlsx"
    with pd.ExcelWriter(source, engine="openpyxl") as writer:
        pd.DataFrame({"value": [1]}).to_excel(writer, sheet_name="Other", index=False)

    with pytest.raises(FlightDataError, match="Complete Wind Data"):
        load_flight_csv(source)
