"""Immutable AirData CSV/SFD ingestion and canonical flight normalization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .quality import (
    dataframe_quality_summary,
    numeric_column_quality,
    write_data_quality_artifacts,
)


FEET_TO_METRES = 0.3048
MILES_PER_HOUR_TO_METRES_PER_SECOND = 0.44704
SUPPORTED_FLIGHT_SUFFIXES = {".csv", ".xlsx"}
SFD_SHEET_NAME = "Complete Wind Data"


class FlightDataError(ValueError):
    """Raised when a flight CSV cannot be normalized safely."""


CANONICAL_CANDIDATES: dict[str, tuple[str, ...]] = {
    "elapsed_time": (
        "time(millisecond)",
        "time(milliseconds)",
        "time_ms",
        "elapsed_time_ms",
        "elapsed_time_s",
        "time(s)",
        "time_seconds",
    ),
    "timestamp_utc": (
        "datetime(utc)",
        "datetime_utc",
        "timestamp_utc",
        "utc_datetime",
        "timestamp",
    ),
    "latitude": ("latitude", "latitude(degrees)", "lat", "gps_latitude"),
    "longitude": ("longitude", "longitude(degrees)", "lon", "lng", "gps_longitude"),
    "altitude": (
        "height_above_takeoff(feet)",
        "height_above_takeoff(m)",
        "height_above_takeoff_m",
        "altitude_m",
        "altitude(feet)",
        "altitude",
    ),
    "wind_speed": (
        "wind_speed_complete(mph)",
        "wind_speed(mph)",
        "wind_speed(m/s)",
        "wind_speed_mps",
        "atmospheric_wind_speed_mps",
        "wind speed",
    ),
    "wind_direction": (
        "wind_direction_complete_from(degrees)",
        "wind_direction(degrees)",
        "wind_direction_from_deg",
        "atmospheric_wind_direction_deg",
        "wind direction",
    ),
    "temperature": (
        "temperature(c)",
        "temperature_c",
        "air_temperature_c",
        "temperature(f)",
    ),
    "relative_humidity": (
        "relative_humidity(%)",
        "relative_humidity_pct",
        "humidity(%)",
        "humidity",
    ),
    "pressure": (
        "pressure(hpa)",
        "pressure_hpa",
        "pressure(pa)",
        "barometric_pressure_hpa",
    ),
}


def read_flight_table(
    path: str | Path,
    *,
    preserve_text: bool = False,
) -> pd.DataFrame:
    """Read a supported flight table without modifying its source file.

    CSV inputs use their only table. Excel inputs are accepted only when they
    contain the documented SFD ``Complete Wind Data`` worksheet.
    """

    source = Path(path).expanduser()
    suffix = source.suffix.casefold()
    if suffix not in SUPPORTED_FLIGHT_SUFFIXES:
        raise FlightDataError(
            f"flight input must be a CSV or SFD .xlsx file: {source}"
        )
    try:
        if suffix == ".csv":
            options: dict[str, Any] = {
                "encoding": "utf-8-sig",
                "low_memory": False,
            }
            if preserve_text:
                options.update(dtype=str, keep_default_na=False, na_filter=False)
            return pd.read_csv(source, **options)

        workbook = pd.ExcelFile(source, engine="openpyxl")
        if SFD_SHEET_NAME not in workbook.sheet_names:
            raise FlightDataError(
                f"SFD workbook requires a {SFD_SHEET_NAME!r} worksheet; "
                f"found: {', '.join(workbook.sheet_names)}"
            )
        options = {
            "sheet_name": SFD_SHEET_NAME,
            "engine": "openpyxl",
        }
        if preserve_text:
            options.update(dtype=str, keep_default_na=False)
        return pd.read_excel(source, **options)
    except FlightDataError:
        raise
    except Exception as exc:
        kind = "SFD workbook" if suffix == ".xlsx" else "flight CSV"
        raise FlightDataError(f"could not read {kind} {source}: {exc}") from exc


def _sfd_default_mapping(columns: pd.Index) -> dict[str, str] | None:
    available = {str(column) for column in columns}
    expected = {
        "elapsed_time_ms",
        "utc_datetime",
        "latitude",
        "longitude",
        "altitude(feet)",
        "wind_speed_complete(mph)",
        "wind_direction_complete_from(degrees)",
    }
    if not expected.issubset(available):
        return None
    return {
        "elapsed_time": "elapsed_time_ms",
        "timestamp_utc": "utc_datetime",
        "latitude": "latitude",
        "longitude": "longitude",
        "altitude": "altitude(feet)",
        "wind_speed": "wind_speed_complete(mph)",
        "wind_direction": "wind_direction_complete_from(degrees)",
    }


_CANONICAL_ALIASES = {
    "elapsed_time_s": "elapsed_time",
    "time": "elapsed_time",
    "time_s": "elapsed_time",
    "datetime": "timestamp_utc",
    "timestamp": "timestamp_utc",
    "latitude_deg": "latitude",
    "longitude_deg": "longitude",
    "altitude_m": "altitude",
    "wind_speed_mps": "wind_speed",
    "wind_direction_from_deg": "wind_direction",
    "temperature_c": "temperature",
    "relative_humidity_pct": "relative_humidity",
    "pressure_hpa": "pressure",
}


_FORBIDDEN_WIND_INPUTS = {
    "xspeedmph",
    "yspeedmph",
    "zspeedmph",
    "speedmph",
    "compassheadingdegrees",
    "gimbalheadingdegrees",
    "pitchdegrees",
    "rolldegrees",
}


def _header_key(value: str) -> str:
    return "".join(character for character in value.strip().casefold() if character.isalnum())


def _is_forbidden_wind_input(value: str) -> bool:
    key = _header_key(value)
    return key in _FORBIDDEN_WIND_INPUTS or key.startswith(
        (
            "xspeed",
            "yspeed",
            "zspeed",
            "compassheading",
            "gimbalheading",
            "pitch",
            "roll",
        )
    ) or (key.startswith("speed") and not key.startswith("windspeed"))


def _canonical_key(value: str) -> str | None:
    normalized = value.strip().casefold()
    if normalized in CANONICAL_CANDIDATES:
        return normalized
    return _CANONICAL_ALIASES.get(normalized)


def _match_header(requested: str, columns: list[str]) -> str:
    if requested in columns:
        return requested
    key = _header_key(requested)
    matches = [column for column in columns if _header_key(column) == key]
    if not matches:
        raise FlightDataError(f"mapped source column does not exist: {requested!r}")
    if len(matches) > 1:
        raise FlightDataError(
            f"mapped source column {requested!r} is ambiguous after header normalization: {matches}"
        )
    return matches[0]


def resolve_column_mapping(
    columns: list[str] | pd.Index,
    mapping: Mapping[str, str] | None = None,
) -> dict[str, str | None]:
    """Resolve canonical fields to exact source headers.

    User mappings may be written either canonical-to-source (preferred) or
    source-to-canonical. Header matching ignores surrounding whitespace and
    punctuation, but returned values preserve the exact CSV header.
    """

    source_columns = [str(column) for column in columns]
    resolved: dict[str, str | None] = {name: None for name in CANONICAL_CANDIDATES}
    provided: dict[str, str] = {}
    for left, right in (mapping or {}).items():
        left_canonical = _canonical_key(str(left))
        right_canonical = _canonical_key(str(right))
        if left_canonical is not None:
            canonical, source = left_canonical, str(right)
        elif right_canonical is not None:
            canonical, source = right_canonical, str(left)
        else:
            raise FlightDataError(
                f"column mapping entry {left!r}: {right!r} does not identify a canonical field"
            )
        if canonical in provided and provided[canonical] != source:
            raise FlightDataError(f"canonical field {canonical!r} is mapped more than once")
        provided[canonical] = source

    normalized_headers: dict[str, list[str]] = {}
    for source in source_columns:
        normalized_headers.setdefault(_header_key(source), []).append(source)
    for canonical, candidates in CANONICAL_CANDIDATES.items():
        if canonical in provided:
            resolved[canonical] = _match_header(provided[canonical], source_columns)
            continue
        for candidate in candidates:
            matches = normalized_headers.get(_header_key(candidate), [])
            if len(matches) == 1:
                resolved[canonical] = matches[0]
                break
            if len(matches) > 1:
                raise FlightDataError(
                    f"multiple source columns match canonical field {canonical!r}: {matches}"
                )

    for canonical in ("wind_speed", "wind_direction"):
        source = resolved[canonical]
        if source is not None and _is_forbidden_wind_input(source):
            raise FlightDataError(
                f"{source!r} is drone motion/orientation, not atmospheric {canonical}; "
                "it cannot be used as a wind mapping"
            )
    return resolved


def _unit_factor(header: str | None, quantity: str) -> float:
    key = (header or "").strip().casefold().replace(" ", "")
    if quantity == "time":
        return 0.001 if "millisecond" in key or key.endswith("_ms") else 1.0
    if quantity == "altitude":
        return (
            FEET_TO_METRES
            if "feet" in key or "(ft)" in key or key.endswith("_ft")
            else 1.0
        )
    if quantity == "wind_speed":
        if "mph" in key:
            return MILES_PER_HOUR_TO_METRES_PER_SECOND
        if "km/h" in key or "kph" in key:
            return 1.0 / 3.6
    if quantity == "pressure" and "(pa)" in key and "hpa" not in key:
        return 0.01
    return 1.0


def _temperature_celsius(values: pd.Series, header: str | None) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    key = (header or "").strip().casefold()
    return (numeric - 32.0) * (5.0 / 9.0) if "(f)" in key else numeric


def _empty_series(index: pd.Index) -> pd.Series:
    return pd.Series(np.nan, index=index, dtype=float)


def _wind_gap_summary(
    elapsed_time_s: pd.Series, paired_valid: pd.Series
) -> dict[str, Any]:
    """Summarize consecutive unavailable-wind intervals on the flight timeline."""

    timeline = pd.DataFrame(
        {
            "time": pd.to_numeric(elapsed_time_s, errors="coerce"),
            "valid": paired_valid.astype(bool),
        }
    ).dropna(subset=["time"])
    timeline = timeline.sort_values("time", kind="stable").reset_index(drop=True)
    if timeline.empty:
        return {
            "missing_interval_count": 0,
            "maximum_consecutive_missing_duration_s": None,
            "maximum_bracketed_observation_gap_s": None,
            "missing_intervals": [],
        }
    differences = timeline["time"].diff().dropna()
    positive_differences = differences[differences > 0.0]
    nominal_interval = (
        float(positive_differences.median()) if len(positive_differences) else 0.0
    )
    missing = ~timeline["valid"]
    groups = missing.ne(missing.shift(fill_value=bool(missing.iloc[0]))).cumsum()
    intervals: list[dict[str, Any]] = []
    for _, block in timeline.loc[missing].groupby(groups[missing], sort=False):
        first_position = int(block.index[0])
        last_position = int(block.index[-1])
        previous_time = (
            float(timeline.at[first_position - 1, "time"])
            if first_position > 0 and bool(timeline.at[first_position - 1, "valid"])
            else None
        )
        next_time = (
            float(timeline.at[last_position + 1, "time"])
            if last_position + 1 < len(timeline)
            and bool(timeline.at[last_position + 1, "valid"])
            else None
        )
        intervals.append(
            {
                "start_elapsed_time_s": float(block["time"].iloc[0]),
                "end_elapsed_time_s": float(block["time"].iloc[-1]),
                "row_count": int(len(block)),
                "estimated_duration_s": float(
                    block["time"].iloc[-1]
                    - block["time"].iloc[0]
                    + nominal_interval
                ),
                "bracketed_observation_gap_s": (
                    float(next_time - previous_time)
                    if previous_time is not None and next_time is not None
                    else None
                ),
                "leading_or_trailing": previous_time is None or next_time is None,
            }
        )
    durations = [item["estimated_duration_s"] for item in intervals]
    bracketed = [
        item["bracketed_observation_gap_s"]
        for item in intervals
        if item["bracketed_observation_gap_s"] is not None
    ]
    return {
        "nominal_input_interval_s": nominal_interval,
        "missing_interval_count": len(intervals),
        "maximum_consecutive_missing_duration_s": max(durations, default=None),
        "maximum_bracketed_observation_gap_s": max(bracketed, default=None),
        "missing_intervals": intervals,
    }


@dataclass(slots=True)
class FlightDataResult:
    """Normalized trajectory plus auditable validation metadata."""

    normalized_flight: pd.DataFrame
    report: dict[str, Any]
    mapping: dict[str, str | None]

    @property
    def normalized(self) -> pd.DataFrame:
        return self.normalized_flight

    @property
    def data_quality_report(self) -> dict[str, Any]:
        return self.report

    @property
    def column_mapping(self) -> dict[str, str | None]:
        return self.mapping

    def write_artifacts(self, output_dir: str | Path) -> dict[str, Path]:
        return write_data_quality_artifacts(
            self.report, self.mapping, self.normalized_flight, output_dir
        )


def discover_flight_csv(directory: str | Path) -> Path:
    """Find a likely immutable flight CSV in a directory."""

    base = Path(directory)
    candidates = sorted(
        path
        for path in base.glob("*.csv")
        if path.name.casefold()
        not in {
            "normalized_flight.csv",
            "drone_sensor_samples.csv",
            "voxel_observations.csv",
            "dataset_manifest.csv",
        }
    )
    if not candidates:
        raise FlightDataError(f"no input CSV files found in {base.resolve()}")
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise FlightDataError(
            f"multiple candidate CSV files found ({names}); provide --flight explicitly"
        )
    return candidates[0]


def load_flight_csv(
    path: str | Path,
    mapping: Mapping[str, str] | None = None,
    min_wind_coverage: float = 0.8,
    *,
    invalid_row_policy: str = "drop_and_report",
    output_dir: str | Path | None = None,
) -> FlightDataResult:
    """Read and normalize an AirData CSV or SFD workbook read-only."""

    source = Path(path).expanduser()
    if not source.is_file():
        raise FlightDataError(f"flight input does not exist: {source}")
    if source.suffix.casefold() not in SUPPORTED_FLIGHT_SUFFIXES:
        raise FlightDataError(f"flight input must be a CSV or SFD .xlsx file: {source}")
    if not 0.0 <= min_wind_coverage <= 1.0:
        raise FlightDataError("min_wind_coverage must be in [0, 1]")
    if invalid_row_policy not in {"drop_and_report", "fail"}:
        raise FlightDataError(
            "invalid_row_policy must be 'drop_and_report' or 'fail'"
        )

    raw = read_flight_table(source)
    if raw.empty:
        raise FlightDataError(f"flight input contains no data rows: {source}")
    if raw.columns.duplicated().any():
        duplicated = raw.columns[raw.columns.duplicated()].astype(str).tolist()
        raise FlightDataError(f"flight CSV contains duplicate column names: {duplicated}")

    sfd_defaults = _sfd_default_mapping(raw.columns)
    effective_mapping = mapping if mapping is not None else sfd_defaults
    resolved = resolve_column_mapping(raw.columns, effective_mapping)
    missing_position = [
        canonical
        for canonical in ("latitude", "longitude", "altitude")
        if resolved[canonical] is None
    ]
    if missing_position:
        raise FlightDataError(
            f"could not map required flight fields: {', '.join(missing_position)}"
        )
    if resolved["elapsed_time"] is None and resolved["timestamp_utc"] is None:
        raise FlightDataError(
            "flight input requires relative elapsed time or a parseable UTC datetime column"
        )

    index = raw.index
    latitude = pd.to_numeric(raw[resolved["latitude"]], errors="coerce")
    longitude = pd.to_numeric(raw[resolved["longitude"]], errors="coerce")
    altitude = (
        pd.to_numeric(raw[resolved["altitude"]], errors="coerce")
        * _unit_factor(resolved["altitude"], "altitude")
    )
    takeoff_height_columns = [
        str(column)
        for column in raw.columns
        if _header_key(str(column))
        in {
            _header_key("height_above_takeoff(feet)"),
            _header_key("height_above_takeoff(m)"),
            _header_key("height_above_takeoff_m"),
        }
    ]
    if len(takeoff_height_columns) == 1:
        takeoff_height_column = takeoff_height_columns[0]
        height_above_takeoff = (
            pd.to_numeric(raw[takeoff_height_column], errors="coerce")
            * _unit_factor(takeoff_height_column, "altitude")
        )
    elif "takeoff" in _header_key(str(resolved["altitude"])):
        height_above_takeoff = altitude.copy()
    else:
        height_above_takeoff = _empty_series(raw.index)
    timestamp = (
        pd.to_datetime(raw[resolved["timestamp_utc"]], errors="coerce", utc=True)
        if resolved["timestamp_utc"] is not None
        else pd.Series(pd.NaT, index=index, dtype="datetime64[ns, UTC]")
    )

    relative_source = (
        pd.to_numeric(raw[resolved["elapsed_time"]], errors="coerce")
        * _unit_factor(resolved["elapsed_time"], "time")
        if resolved["elapsed_time"] is not None
        else _empty_series(index)
    )
    relative_coverage = float(relative_source.notna().mean())
    datetime_coverage = float(timestamp.notna().mean())
    if relative_source.notna().sum() >= 2 and relative_coverage >= 0.8:
        elapsed = relative_source - relative_source.min()
        time_source = "relative_time"
    elif timestamp.notna().sum() >= 2:
        elapsed = (timestamp - timestamp.min()).dt.total_seconds()
        time_source = "utc_datetime_fallback"
    else:
        raise FlightDataError(
            "neither relative time nor UTC datetime has enough parseable observations"
        )

    def numeric_optional(canonical: str, quantity: str | None = None) -> pd.Series:
        source_column = resolved[canonical]
        if source_column is None:
            return _empty_series(index)
        values = pd.to_numeric(raw[source_column], errors="coerce")
        return values * (_unit_factor(source_column, quantity) if quantity else 1.0)

    wind_speed = numeric_optional("wind_speed", "wind_speed")
    wind_direction = numeric_optional("wind_direction")
    wind_speed = wind_speed.where(wind_speed.ge(0.0))
    wind_direction = wind_direction.where(wind_direction.between(0.0, 360.0)) % 360.0
    temperature = (
        _temperature_celsius(raw[resolved["temperature"]], resolved["temperature"])
        if resolved["temperature"] is not None
        else _empty_series(index)
    )
    humidity = numeric_optional("relative_humidity")
    pressure = numeric_optional("pressure", "pressure")
    is_sfd = source.suffix.casefold() == ".xlsx" and sfd_defaults is not None
    original_wind_speed = (
        pd.to_numeric(raw["wind_speed_original(mph)"], errors="coerce")
        * MILES_PER_HOUR_TO_METRES_PER_SECOND
        if is_sfd and "wind_speed_original(mph)" in raw
        else _empty_series(index)
    )
    original_wind_direction = (
        pd.to_numeric(
            raw["wind_direction_original_from(degrees)"], errors="coerce"
        )
        if is_sfd and "wind_direction_original_from(degrees)" in raw
        else _empty_series(index)
    )
    input_wind_source = (
        raw["wind_data_source"].fillna("").astype("string")
        if is_sfd and "wind_data_source" in raw
        else pd.Series("", index=index, dtype="string")
    )
    input_wind_confidence = (
        pd.to_numeric(raw["wind_confidence"], errors="coerce")
        if is_sfd and "wind_confidence" in raw
        else _empty_series(index)
    )
    input_wind_calm = (
        raw["calm_flag"]
        .astype("string")
        .str.strip()
        .str.casefold()
        .isin({"true", "1", "yes"})
        if is_sfd and "calm_flag" in raw
        else pd.Series(False, index=index, dtype=bool)
    )
    input_wind_gap_id = (
        raw["wind_gap_id"].fillna("").astype("string")
        if is_sfd and "wind_gap_id" in raw
        else pd.Series("", index=index, dtype="string")
    )

    invalid_reasons = pd.DataFrame(
        {
            "invalid_time": elapsed.isna() | ~np.isfinite(elapsed),
            "invalid_latitude": latitude.isna()
            | ~np.isfinite(latitude)
            | ~latitude.between(-90.0, 90.0),
            "invalid_longitude": longitude.isna()
            | ~np.isfinite(longitude)
            | ~longitude.between(-180.0, 180.0),
            "invalid_altitude": altitude.isna() | ~np.isfinite(altitude),
        },
        index=index,
    )
    invalid = invalid_reasons.any(axis=1)
    invalid_count = int(invalid.sum())
    if invalid_count and invalid_row_policy == "fail":
        example_rows = raw.index[invalid].tolist()[:10]
        raise FlightDataError(
            f"flight input contains {invalid_count} invalid core trajectory rows; "
            f"example zero-based row indices: {example_rows}"
        )

    normalized = pd.DataFrame(
        {
            "source_row_index": raw.index.astype(int),
            "original_timestamp": (
                raw[resolved["timestamp_utc"]].astype("string")
                if resolved["timestamp_utc"] is not None
                else pd.Series(pd.NA, index=index, dtype="string")
            ),
            "timestamp_utc": timestamp,
            "elapsed_time_s": elapsed,
            "latitude": latitude,
            "longitude": longitude,
            "altitude_m": altitude,
            "height_above_takeoff_m": height_above_takeoff,
            "wind_speed_mps": wind_speed,
            "wind_direction_from_deg": wind_direction,
            "wind_speed_original_mps": original_wind_speed,
            "wind_direction_original_from_deg": original_wind_direction,
            "wind_input_source": input_wind_source,
            "wind_input_confidence": input_wind_confidence,
            "wind_input_calm_flag": input_wind_calm,
            "wind_input_gap_id": input_wind_gap_id,
            "temperature_c": temperature,
            "relative_humidity_pct": humidity,
            "pressure_hpa": pressure,
            "quality_flags": "",
        },
        index=index,
    )
    normalized = (
        normalized.loc[~invalid]
        .sort_values(["elapsed_time_s", "source_row_index"], kind="stable")
        .reset_index(drop=True)
    )

    speed_quality = (
        numeric_column_quality(raw[resolved["wind_speed"]])
        if resolved["wind_speed"] is not None
        else numeric_column_quality(_empty_series(index))
    )
    direction_quality = (
        numeric_column_quality(raw[resolved["wind_direction"]])
        if resolved["wind_direction"] is not None
        else numeric_column_quality(_empty_series(index))
    )
    paired_wind = wind_speed.notna() & wind_direction.notna()
    paired_coverage_all_rows = float(paired_wind.mean())
    eligible_paired_wind = paired_wind.loc[~invalid]
    paired_coverage = (
        float(eligible_paired_wind.mean()) if len(eligible_paired_wind) else 0.0
    )
    measured_usable = (
        int(eligible_paired_wind.sum()) >= 2
        and paired_coverage >= min_wind_coverage
    )
    wind_gap_summary = _wind_gap_summary(elapsed, paired_wind)
    valid_speed = wind_speed[paired_wind]
    valid_direction = wind_direction[paired_wind]
    sfd_source_counts = (
        {
            str(name): int(count)
            for name, count in input_wind_source.value_counts(dropna=False).items()
            if str(name)
        }
        if is_sfd
        else {}
    )
    sfd_original_rows = int(
        input_wind_source.eq("original_airdata_estimate").sum()
    )
    sfd_generated_rows = int(len(raw) - sfd_original_rows) if is_sfd else 0
    warnings: list[str] = []
    if not measured_usable:
        warnings.append(
            f"Measured atmospheric wind is unavailable at the configured threshold: "
            f"paired numeric coverage is {paired_coverage:.1%}, minimum is "
            f"{min_wind_coverage:.1%}. Explicit synthetic mode is required; no "
            "drone-velocity fallback is permitted."
        )
    if int((altitude < 0.0).sum()):
        warnings.append(
            "Height above takeoff contains negative values; they are retained because "
            "small takeoff-relative deviations can be valid telemetry."
        )
    if any(str(column) != str(column).strip() for column in raw.columns):
        warnings.append(
            "Some source headers contain surrounding whitespace; mapping uses normalized "
            "matching while preserving exact source headers."
        )
    if is_sfd and sfd_generated_rows:
        warnings.append(
            "SFD complete wind is available for every row, but "
            f"{sfd_generated_rows:,} of {len(raw):,} rows are generated or interpolated. "
            "Row-level wind_data_source and wind_confidence are retained."
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "source_file": {
            "path": str(source.resolve()),
            "name": source.name,
            "size_bytes": int(source.stat().st_size),
            "modified_time_utc": pd.Timestamp(
                source.stat().st_mtime, unit="s", tz="UTC"
            ).isoformat(),
            "source_was_modified": False,
            "input_format": "sfd_xlsx" if is_sfd else "airdata_csv",
            "worksheet": SFD_SHEET_NAME if is_sfd else None,
        },
        "input": {
            "row_count": int(len(raw)),
            "column_count": int(len(raw.columns)),
            "headers": raw.columns.astype(str).tolist(),
            "headers_with_surrounding_whitespace": [
                str(column)
                for column in raw.columns
                if str(column) != str(column).strip()
            ],
            "duplicate_row_count": int(raw.duplicated().sum()),
            "column_quality": dataframe_quality_summary(raw),
        },
        "column_mapping": resolved,
        "trajectory": {
            "normalized_row_count": int(len(normalized)),
            "invalid_row_count": invalid_count,
            "dropped_row_count": invalid_count,
            "invalid_row_policy": invalid_row_policy,
            "invalid_row_indices_zero_based": raw.index[invalid].astype(int).tolist(),
            "invalid_reason_counts": {
                name: int(invalid_reasons[name].sum()) for name in invalid_reasons
            },
            "time_source": time_source,
            "relative_time_numeric_coverage": relative_coverage,
            "utc_datetime_parse_coverage": datetime_coverage,
            "relative_time_decreasing_step_count": int(
                (relative_source.dropna().diff() < 0.0).sum()
            ),
            "relative_time_duplicate_count": int(relative_source.dropna().duplicated().sum()),
            "start_elapsed_time_s": (
                float(normalized["elapsed_time_s"].min()) if len(normalized) else None
            ),
            "end_elapsed_time_s": (
                float(normalized["elapsed_time_s"].max()) if len(normalized) else None
            ),
            "negative_altitude_row_count": int((altitude < 0.0).sum()),
            "altitude_source": resolved["altitude"],
            "altitude_unit": "metres",
            "altitude_reference": (
                "height above takeoff"
                if "takeoff" in _header_key(str(resolved["altitude"]))
                else "source-column datum"
            ),
            "flat_ground_assumption": "takeoff"
            in _header_key(str(resolved["altitude"])),
        },
        "wind": {
            "speed": speed_quality,
            "direction": direction_quality,
            "paired_numeric_count": int(eligible_paired_wind.sum()),
            "paired_numeric_coverage": paired_coverage,
            "paired_numeric_coverage_all_input_rows": paired_coverage_all_rows,
            "minimum_numeric_coverage": float(min_wind_coverage),
            "measured_wind_usable": measured_usable,
            "speed_mps_minimum": (
                float(valid_speed.min()) if len(valid_speed) else None
            ),
            "speed_mps_maximum": (
                float(valid_speed.max()) if len(valid_speed) else None
            ),
            "direction_from_deg_minimum": (
                float(valid_direction.min()) if len(valid_direction) else None
            ),
            "direction_from_deg_maximum": (
                float(valid_direction.max()) if len(valid_direction) else None
            ),
            **wind_gap_summary,
            "source_if_used": (
                "SFD complete wind fields with row-level provenance"
                if is_sfd
                else "measured atmospheric AirData fields"
            ),
            "sfd_original_row_count": sfd_original_rows,
            "sfd_generated_or_interpolated_row_count": sfd_generated_rows,
            "sfd_source_counts": sfd_source_counts,
            "drone_velocity_used_as_wind": False,
            "compass_heading_used_as_wind_direction": False,
        },
        "warnings": warnings,
    }
    result = FlightDataResult(normalized, report, resolved)
    if output_dir is not None:
        result.write_artifacts(output_dir)
    return result


def validate_flight_csv(
    path: str | Path,
    mapping: Mapping[str, str] | None = None,
    min_wind_coverage: float = 0.8,
    *,
    invalid_row_policy: str = "drop_and_report",
    output_dir: str | Path | None = None,
) -> FlightDataResult:
    """Validate and normalize a supported flight input, optionally writing audits."""

    return load_flight_csv(
        path,
        mapping=mapping,
        min_wind_coverage=min_wind_coverage,
        invalid_row_policy=invalid_row_policy,
        output_dir=output_dir,
    )


__all__ = [
    "CANONICAL_CANDIDATES",
    "FEET_TO_METRES",
    "FlightDataError",
    "FlightDataResult",
    "MILES_PER_HOUR_TO_METRES_PER_SECOND",
    "SFD_SHEET_NAME",
    "SUPPORTED_FLIGHT_SUFFIXES",
    "discover_flight_csv",
    "load_flight_csv",
    "read_flight_table",
    "resolve_column_mapping",
    "validate_flight_csv",
]
