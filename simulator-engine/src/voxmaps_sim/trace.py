"""Interactive trace runner and atomic two-file scenario contract.

The legacy pipeline remains available for research exports.  This module is the
desktop application's narrow persistence boundary: one clean sensor CSV plus
one compressed HDF5 audit/playback file.  It deliberately reuses the verified
ingestion, coordinate, wind, dispersion, and sensor components.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Callable, Mapping

import h5py
import numpy as np
from numpy.typing import ArrayLike, NDArray
import pandas as pd

from . import __version__
from .config import SimulationConfig, load_config
from .dispersion import DispersionModel
from .ingestion import (
    FEET_TO_METRES,
    MILES_PER_HOUR_TO_METRES_PER_SECOND,
    load_flight_csv,
    read_flight_table,
)
from .particles import COARSE, FINE, ParticleSet
from .pipeline import (
    _event_times,
    _make_grid_and_domain,
    _make_transformer,
    _resolve_source,
    _sample_times,
)
from .quality import QualityFlag, combine_quality_flags
from .sensor import VirtualSensor
from .wind import assess_measured_wind, build_wind_series, uv_to_meteorological


TraceProgressCallback = Callable[[float, str, str], None]
CancellationCheck = Callable[[], bool]

SENSOR_LOG_COLUMNS = [
    "simulation_id",
    "sample_id",
    "source_row_index",
    "utc_time",
    "uptime_ms",
    "elapsed_time_s",
    "latitude_deg",
    "longitude_deg",
    "altitude_msl_m",
    "height_above_takeoff_m",
    "x_east_m",
    "y_north_m",
    "z_up_m",
    "ground_speed_mps",
    "vertical_speed_mps",
    "heading_deg",
    "pitch_deg",
    "roll_deg",
    "satellite_count",
    "gps_quality",
    "flight_state",
    "wind_speed_raw_mps",
    "wind_direction_raw_deg",
    "wind_speed_used_mps",
    "wind_direction_used_deg",
    "wind_source",
    "sampling_region_id",
    "voxel_id",
    "pm25_true_ug_m3",
    "coarse_pm_true_ug_m3",
    "pm10_true_ug_m3",
    "pm25_sensor_ug_m3",
    "pm10_sensor_ug_m3",
    "fine_contributing_parcels",
    "coarse_contributing_parcels",
    "quality_flags",
]

_UNITS = {
    "uptime_ms": "ms",
    "elapsed_time_s": "s",
    "latitude_deg": "degrees_north",
    "longitude_deg": "degrees_east",
    "altitude_msl_m": "m",
    "height_above_takeoff_m": "m",
    "x_east_m": "m",
    "y_north_m": "m",
    "z_up_m": "m",
    "ground_speed_mps": "m/s",
    "vertical_speed_mps": "m/s",
    "heading_deg": "degrees",
    "pitch_deg": "degrees",
    "roll_deg": "degrees",
    "wind_speed_raw_mps": "m/s",
    "wind_direction_raw_deg": "degrees_from_north",
    "wind_speed_used_mps": "m/s",
    "wind_direction_used_deg": "degrees_from_north",
    "pm25_true_ug_m3": "ug/m3",
    "coarse_pm_true_ug_m3": "ug/m3",
    "pm10_true_ug_m3": "ug/m3",
    "pm25_sensor_ug_m3": "ug/m3",
    "pm10_sensor_ug_m3": "ug/m3",
    "position_m": "m",
    "mass_g": "g",
    "initial_mass_g": "g",
    "final_mass_g": "g",
    "emission_time_s": "s",
    "frame_times_s": "s",
}


class SimulationCancelled(RuntimeError):
    """Raised after a cooperative cancellation request is observed."""


class TraceContractError(RuntimeError):
    """Raised when trace output cannot satisfy the two-file contract."""


@dataclass(frozen=True, slots=True)
class SensorRegionSample:
    """Exact airborne parcel contribution inside an axis-aligned sensor box."""

    parcel_id: NDArray[np.int64]
    mass_g: NDArray[np.float64]
    channel: NDArray[np.uint8]
    fine_mass_g: float
    coarse_mass_g: float
    fine_count: int
    coarse_count: int
    pm25_true_ug_m3: float
    coarse_pm_true_ug_m3: float
    pm10_true_ug_m3: float


@dataclass(frozen=True, slots=True)
class TraceRunResult:
    """Published interactive scenario plus useful in-memory summaries."""

    simulation_id: str
    h5_path: Path
    csv_path: Path
    sensor_log: pd.DataFrame
    mass_balance: dict[str, float]
    warnings: tuple[str, ...]
    frame_count: int

    @property
    def output_paths(self) -> dict[str, Path]:
        return {"simulation_trace": self.h5_path, "sensor_log": self.csv_path}

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "h5_path": str(self.h5_path),
            "csv_path": str(self.csv_path),
            "sample_count": int(len(self.sensor_log)),
            "frame_count": int(self.frame_count),
            "mass_balance": dict(self.mass_balance),
            "warnings": list(self.warnings),
        }


def sample_sensor_region(
    particles: ParticleSet,
    centre_m: ArrayLike,
    size_m: ArrayLike,
    *,
    background_pm25_ug_m3: float = 0.0,
    background_coarse_pm_ug_m3: float = 0.0,
) -> SensorRegionSample:
    """Sample a rectangular numerical volume; no graphical collision is used."""

    centre = np.asarray(centre_m, dtype=float)
    size = np.asarray(size_m, dtype=float)
    backgrounds = np.asarray(
        [background_pm25_ug_m3, background_coarse_pm_ug_m3], dtype=float
    )
    if centre.shape != (3,) or not np.all(np.isfinite(centre)):
        raise ValueError("sensor-region centre must be a finite 3-vector")
    if size.shape != (3,) or not np.all(np.isfinite(size)) or np.any(size <= 0.0):
        raise ValueError("sensor-region size must be a finite positive 3-vector")
    if not np.all(np.isfinite(backgrounds)) or np.any(backgrounds < 0.0):
        raise ValueError("background concentrations must be finite and non-negative")
    if len(particles):
        mask = np.all(np.abs(particles.positions_m - centre) <= size * 0.5, axis=1)
        selected = particles.subset(mask)
    else:
        selected = ParticleSet.empty()
    masses = selected.channel_masses_g()
    counts = selected.channel_counts()
    concentrations = masses * 1_000_000.0 / float(np.prod(size)) + backgrounds
    return SensorRegionSample(
        selected.parcel_id.copy(),
        selected.mass_g.copy(),
        selected.channel.copy(),
        float(masses[FINE]),
        float(masses[COARSE]),
        int(counts[FINE]),
        int(counts[COARSE]),
        float(concentrations[FINE]),
        float(concentrations[COARSE]),
        float(concentrations.sum()),
    )


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="python"))
    return {
        name: getattr(value, name)
        for name in dir(value)
        if not name.startswith("_") and not callable(getattr(value, name))
    }


def _resolve_trace_config(config: Any) -> SimulationConfig:
    """Resolve a bare engine config or the desktop wrapper without a dependency cycle."""

    if isinstance(config, SimulationConfig):
        return config.model_copy(deep=True)
    if isinstance(config, (str, Path)):
        return load_config(config)
    wrapper = _mapping(config)
    engine = getattr(config, "engine", wrapper.get("engine"))
    if engine is None:
        resolved = SimulationConfig.model_validate(wrapper)
    elif isinstance(engine, SimulationConfig):
        resolved = engine.model_copy(deep=True)
    else:
        resolved = SimulationConfig.model_validate(_mapping(engine))

    sensor_region = _mapping(getattr(config, "sensor_region", wrapper.get("sensor_region")))
    aliases = {
        "sampling_region_size_x_m": ("size_x_m", "width_m", "x_m"),
        "sampling_region_size_y_m": ("size_y_m", "depth_m", "y_m"),
        "sampling_region_size_z_m": ("size_z_m", "height_m", "z_m"),
    }
    for target, names in aliases.items():
        for name in names:
            if sensor_region.get(name) is not None:
                setattr(resolved.sensor, target, float(sensor_region[name]))
                break

    playback = _mapping(getattr(config, "playback", wrapper.get("playback")))
    if playback.get("frame_interval_s") is not None:
        resolved.simulation.playback_frame_interval_s = float(playback["frame_interval_s"])
    if playback.get("visualization_parcel_limit") is not None:
        resolved.simulation.visualization_parcel_limit = int(
            playback["visualization_parcel_limit"]
        )

    wind_policy = _mapping(getattr(config, "wind_policy", wrapper.get("wind_policy")))
    interpolation = wind_policy.get("interpolation_method", wind_policy.get("interpolation"))
    if interpolation is not None:
        resolved.wind.interpolation_method = str(interpolation)
    mode = wind_policy.get("mode")
    if mode is not None:
        resolved.wind.mode = {
            "strict_measured": "measured",
            "measured": "measured_interpolated",
            "measured_interpolated": "measured_interpolated",
            "synthetic_fallback": "synthetic",
            "synthetic": "synthetic",
        }.get(str(mode), str(mode))
    for names, target in (
        (("fallback_speed_mps", "speed_mps"), "base_speed_mps"),
        (("fallback_direction_deg", "direction_from_deg"), "base_direction_from_deg"),
    ):
        for name in names:
            if wind_policy.get(name) is not None:
                setattr(resolved.wind.synthetic, target, float(wind_policy[name]))
                break
    return resolved


def _notify(
    callback: TraceProgressCallback | None,
    fraction: float,
    stage: str,
    message: str,
) -> None:
    if callback is not None:
        callback(float(np.clip(fraction, 0.0, 1.0)), stage, message)


def _check_cancel(cancel_check: CancellationCheck | None) -> None:
    if cancel_check is not None and bool(cancel_check()):
        raise SimulationCancelled("simulation cancelled by user")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_scenario_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._")
    if not cleaned:
        raise ValueError("scenario_name must contain at least one letter or digit")
    return cleaned[:100]


def _header_key(value: str) -> str:
    return "".join(character for character in value.strip().casefold() if character.isalnum())


def _find_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    index: dict[str, list[str]] = {}
    for column in columns:
        index.setdefault(_header_key(column), []).append(column)
    for candidate in candidates:
        matches = index.get(_header_key(candidate), [])
        if len(matches) == 1:
            return matches[0]
    return None


def _interpolate(values: ArrayLike, times: ArrayLike, targets: NDArray[np.float64]) -> NDArray[np.float64]:
    numeric = np.asarray(values, dtype=float)
    source_times = np.asarray(times, dtype=float)
    valid = np.isfinite(numeric) & np.isfinite(source_times)
    if not np.any(valid):
        return np.full(len(targets), np.nan)
    collapsed = (
        pd.DataFrame({"time": source_times[valid], "value": numeric[valid]})
        .groupby("time", sort=True, as_index=False)["value"]
        .mean()
    )
    return np.interp(
        targets,
        collapsed["time"].to_numpy(dtype=float),
        collapsed["value"].to_numpy(dtype=float),
    )


def _raw_numeric_for_normalized(
    raw: pd.DataFrame,
    normalized: pd.DataFrame,
    candidates: tuple[str, ...],
    factor: float = 1.0,
) -> tuple[NDArray[np.float64], str | None]:
    column = _find_column(raw.columns.astype(str).tolist(), candidates)
    if column is None:
        return np.full(len(normalized), np.nan), None
    parsed = pd.to_numeric(raw[column], errors="coerce").to_numpy(dtype=float) * factor
    rows = normalized["source_row_index"].to_numpy(dtype=int)
    return parsed[rows], column


def _nearest_indices(source_times: NDArray[np.float64], targets: NDArray[np.float64]) -> NDArray[np.int64]:
    right = np.searchsorted(source_times, targets, side="left")
    right = np.clip(right, 0, len(source_times) - 1)
    left = np.clip(right - 1, 0, len(source_times) - 1)
    choose_left = np.abs(targets - source_times[left]) <= np.abs(source_times[right] - targets)
    return np.where(choose_left, left, right).astype(np.int64)


def _trajectory_samples(
    raw: pd.DataFrame,
    normalized: pd.DataFrame,
    sample_times: NDArray[np.float64],
    normalized_indices: NDArray[np.int64] | None = None,
) -> pd.DataFrame:
    source_times = normalized["elapsed_time_s"].to_numpy(dtype=float)
    exact_rows = normalized_indices is not None
    nearest = (
        np.asarray(normalized_indices, dtype=np.int64)
        if exact_rows
        else _nearest_indices(source_times, sample_times)
    )
    if nearest.shape != sample_times.shape:
        raise ValueError("normalized_indices must align one-to-one with sample_times")
    rows = normalized.iloc[nearest]["source_row_index"].to_numpy(dtype=int)
    result = pd.DataFrame(
        {
            "elapsed_time_s": sample_times,
            "source_row_index": rows,
        }
    )
    for source, target in (
        ("latitude", "latitude_deg"),
        ("longitude", "longitude_deg"),
        ("height_above_takeoff_m", "height_above_takeoff_m"),
        ("enu_x_m", "x_east_m"),
        ("enu_y_m", "y_north_m"),
        ("enu_z_m", "z_up_m"),
        ("relative_humidity_pct", "relative_humidity_pct"),
    ):
        result[target] = (
            normalized.iloc[nearest][source].to_numpy(dtype=float)
            if exact_rows
            else _interpolate(normalized[source], source_times, sample_times)
        )

    telemetry: dict[str, tuple[tuple[str, ...], float]] = {
        "altitude_msl_m": (("altitude_above_seaLevel(feet)", "altitude(feet)", "altitude_msl_m"), FEET_TO_METRES),
        "ground_speed_mps": (("speed(mph)", "ground_speed_mps"), MILES_PER_HOUR_TO_METRES_PER_SECOND),
        "vertical_speed_mps": (("zSpeed(mph)", "vertical_speed_mps"), MILES_PER_HOUR_TO_METRES_PER_SECOND),
        "heading_deg": (("compass_heading(degrees)", "heading_deg"), 1.0),
        "pitch_deg": (("pitch(degrees)", "pitch_deg"), 1.0),
        "roll_deg": (("roll(degrees)", "roll_deg"), 1.0),
        "satellite_count": (("satellites", "satellite_count"), 1.0),
    }
    for target, (candidates, factor) in telemetry.items():
        values, _ = _raw_numeric_for_normalized(raw, normalized, candidates, factor)
        result[target] = (
            values[nearest]
            if exact_rows
            else _interpolate(values, source_times, sample_times)
        )

    uptime, uptime_column = _raw_numeric_for_normalized(
        raw,
        normalized,
        ("time(millisecond)", "time(milliseconds)", "uptime_ms", "elapsed_time_s"),
    )
    if uptime_column is not None and "millisecond" not in uptime_column.casefold() and not _header_key(uptime_column).endswith("ms"):
        uptime *= 1_000.0
    result["uptime_ms"] = (
        uptime[nearest]
        if exact_rows
        else _interpolate(uptime, source_times, sample_times)
    )

    parsed_utc = pd.to_datetime(normalized["timestamp_utc"], utc=True, errors="coerce")
    valid_utc = parsed_utc.notna().to_numpy()
    if np.any(valid_utc):
        anchor = int(np.flatnonzero(valid_utc)[0])
        derived_timestamps = parsed_utc.iloc[anchor] + pd.to_timedelta(
            sample_times - source_times[anchor], unit="s"
        )
        if exact_rows:
            exact_timestamps = parsed_utc.iloc[nearest].reset_index(drop=True)
            timestamps = exact_timestamps.where(exact_timestamps.notna(), derived_timestamps)
        else:
            timestamps = derived_timestamps
        result["utc_time"] = [
            value.isoformat().replace("+00:00", "Z") for value in timestamps
        ]
    else:
        result["utc_time"] = ""

    for target, candidates in (
        ("gps_quality", ("gpslevel", "gps_quality")),
        ("flight_state", ("flycState", "flight_state")),
    ):
        column = _find_column(raw.columns.astype(str).tolist(), candidates)
        result[target] = (
            raw.iloc[rows][column].fillna("").astype(str).to_numpy()
            if column is not None
            else np.full(len(rows), "", dtype=object)
        )
    raw_speed_name = (
        "wind_speed_original_mps"
        if "wind_speed_original_mps" in normalized
        and normalized["wind_speed_original_mps"].notna().any()
        else "wind_speed_mps"
    )
    raw_direction_name = (
        "wind_direction_original_from_deg"
        if "wind_direction_original_from_deg" in normalized
        and normalized["wind_direction_original_from_deg"].notna().any()
        else "wind_direction_from_deg"
    )
    result["wind_speed_raw_mps"] = normalized.iloc[nearest][raw_speed_name].to_numpy(dtype=float)
    result["wind_direction_raw_deg"] = normalized.iloc[nearest][raw_direction_name].to_numpy(dtype=float)
    return result


def _used_wind_frame(wind: pd.DataFrame, config: SimulationConfig, source_height_m: float) -> pd.DataFrame:
    result = wind.copy()
    u = result["wind_u_mps"].to_numpy(dtype=float)
    v = result["wind_v_mps"].to_numpy(dtype=float)
    finite = np.isfinite(u) & np.isfinite(v)
    u = np.where(finite, u, 0.0)
    v = np.where(finite, v, 0.0)
    if config.wind.height_adjustment_enabled:
        factor = (
            max(source_height_m, 1e-6) / config.wind.reference_height_m
        ) ** config.wind.power_law_exponent
        u *= factor
        v *= factor
    speed, direction = uv_to_meteorological(u, v)
    sources: list[str] = []
    for row_index, row in result.iterrows():
        source = str(row["wind_source"])
        if source == "synthetic":
            sources.append("synthetic_fallback")
        elif not finite[row_index]:
            sources.append("missing_measured_zero_advection")
        elif bool(row.get("wind_interpolated", False)):
            sources.append("interpolated")
        else:
            sources.append("measured")
    result["wind_u_used_mps"] = u
    result["wind_v_used_mps"] = v
    result["wind_speed_used_mps"] = np.asarray(speed, dtype=float)
    result["wind_direction_used_deg"] = np.asarray(direction, dtype=float)
    result["wind_source_used"] = sources
    return result


def _apply_input_wind_provenance(
    used_wind: pd.DataFrame,
    normalized: pd.DataFrame,
) -> pd.DataFrame:
    """Carry SFD row-level wind provenance onto the solver timeline."""

    if "wind_input_source" not in normalized:
        return used_wind
    labels = normalized["wind_input_source"].fillna("").astype(str).to_numpy()
    if not np.any(labels != ""):
        return used_wind
    source_times = normalized["elapsed_time_s"].to_numpy(dtype=float)
    target_times = used_wind["elapsed_time_s"].to_numpy(dtype=float)
    right = np.searchsorted(source_times, target_times, side="left")
    right = np.clip(right, 0, len(source_times) - 1)
    left = np.clip(right - 1, 0, len(source_times) - 1)
    choose_left = np.abs(target_times - source_times[left]) <= np.abs(
        source_times[right] - target_times
    )
    nearest = np.where(choose_left, left, right)
    result = used_wind.copy()
    result["wind_source_used"] = labels[nearest]
    return result


def _column_audit(raw: pd.DataFrame, retained: set[str]) -> tuple[list[str], dict[str, str]]:
    excluded: dict[str, str] = {}
    for column in raw.columns.astype(str):
        if column in retained:
            continue
        key = _header_key(column)
        if key.startswith("max") or key in {"distancefeet", "mileagefeet", "ascentfeet"}:
            reason = "maximum or cumulative summary field"
        elif key.startswith("rc"):
            reason = "remote-controller input"
        elif key.startswith("gimbal"):
            reason = "gimbal control"
        elif key in {"isphoto", "isvideo"}:
            reason = "photo/video status"
        elif "battery" in key or "voltagecell" in key or key in {"voltagev", "currenta"}:
            reason = "battery or individual-cell telemetry"
        elif "groundatdrone" in key or "groundelevation" in key or "sonar" in key:
            reason = "terrain/sonar field not used by flat-ground replay"
        elif key.endswith("raw"):
            reason = "raw controller code"
        elif key == "message":
            reason = "routine status message"
        else:
            reason = "unrelated telemetry not required for reconstruction or sensor interpretation"
        excluded[column] = reason
    return sorted(retained), excluded


def _frame_snapshot(
    active: ParticleSet,
    deposited: ParticleSet,
    limit: int,
) -> tuple[NDArray[np.int64], NDArray[np.float64], NDArray[np.float64], NDArray[np.uint8], NDArray[np.uint8], int]:
    ids = np.concatenate((active.parcel_id, deposited.parcel_id))
    positions = np.concatenate((active.positions_m, deposited.positions_m), axis=0)
    masses = np.concatenate((active.mass_g, deposited.mass_g))
    channels = np.concatenate((active.channel, deposited.channel))
    states = np.concatenate(
        (np.zeros(len(active), dtype=np.uint8), np.ones(len(deposited), dtype=np.uint8))
    )
    full_count = len(ids)
    if full_count > limit:
        order = np.argsort(ids, kind="stable")
        chosen = order[np.linspace(0, full_count - 1, limit, dtype=np.int64)]
        ids, positions, masses, channels, states = (
            ids[chosen], positions[chosen], masses[chosen], channels[chosen], states[chosen]
        )
    return ids, positions, masses, channels, states, full_count


def _concat(arrays: list[NDArray[Any]], shape: tuple[int, ...], dtype: Any) -> NDArray[Any]:
    nonempty = [array for array in arrays if len(array)]
    if not nonempty:
        return np.empty(shape, dtype=dtype)
    return np.concatenate(nonempty, axis=0).astype(dtype, copy=False)


def _create_dataset(
    group: h5py.Group,
    name: str,
    values: Any,
    *,
    unit: str | None = None,
) -> h5py.Dataset:
    array = np.asarray(values)
    if array.dtype.kind in {"O", "U"}:
        flat = ["" if value is None else str(value) for value in array.reshape(-1)]
        data = np.asarray(flat, dtype=h5py.string_dtype("utf-8")).reshape(array.shape)
        options = {"compression": "gzip", "compression_opts": 4, "chunks": True} if data.size else {}
        dataset = group.create_dataset(name, data=data, **options)
    else:
        options = (
            {"compression": "gzip", "compression_opts": 4, "shuffle": True, "chunks": True}
            if array.size and array.ndim
            else {}
        )
        dataset = group.create_dataset(name, data=array, **options)
    if unit is not None:
        dataset.attrs["units"] = unit
    return dataset


def _raw_wind_payload(
    raw: pd.DataFrame,
    normalized: pd.DataFrame,
    mapping: Mapping[str, str | None],
) -> dict[str, Any]:
    rows = normalized["source_row_index"].to_numpy(dtype=int)
    speed_column = (
        "wind_speed_original(mph)"
        if "wind_speed_original(mph)" in raw
        else mapping.get("wind_speed")
    )
    direction_column = (
        "wind_direction_original_from(degrees)"
        if "wind_direction_original_from(degrees)" in raw
        else mapping.get("wind_direction")
    )
    speed_text = (
        raw[speed_column].fillna("").astype(str).to_numpy()
        if speed_column is not None
        else np.full(len(raw), "", dtype=object)
    )
    direction_text = (
        raw[direction_column].fillna("").astype(str).to_numpy()
        if direction_column is not None
        else np.full(len(raw), "", dtype=object)
    )
    elapsed = np.full(len(raw), np.nan)
    speed = np.full(len(raw), np.nan)
    direction = np.full(len(raw), np.nan)
    elapsed[rows] = normalized["elapsed_time_s"].to_numpy(dtype=float)
    normalized_speed = (
        normalized["wind_speed_original_mps"]
        if "wind_speed_original_mps" in normalized
        and normalized["wind_speed_original_mps"].notna().any()
        else normalized["wind_speed_mps"]
    )
    normalized_direction = (
        normalized["wind_direction_original_from_deg"]
        if "wind_direction_original_from_deg" in normalized
        and normalized["wind_direction_original_from_deg"].notna().any()
        else normalized["wind_direction_from_deg"]
    )
    speed[rows] = normalized_speed.to_numpy(dtype=float)
    direction[rows] = normalized_direction.to_numpy(dtype=float)
    return {
        "source_row_index": np.arange(len(raw), dtype=np.int64),
        "elapsed_time_s": elapsed,
        "speed_text": speed_text,
        "direction_text": direction_text,
        "wind_speed_mps": speed,
        "wind_direction_from_deg": direction,
        "paired_valid": np.isfinite(speed) & np.isfinite(direction),
    }


def _write_trace_h5(
    path: Path,
    *,
    simulation_id: str,
    scenario_name: str,
    creation_time: str,
    source_path: Path,
    source_checksum: str,
    config: SimulationConfig,
    ingestion_report: Mapping[str, Any],
    column_mapping: Mapping[str, str | None],
    transformer: Any,
    source: Any,
    grid: Any,
    raw_wind: Mapping[str, Any],
    used_wind: pd.DataFrame,
    trajectory: pd.DataFrame,
    dispersion: DispersionModel,
    frame_times: list[float],
    frame_offsets: list[int],
    frame_ids: list[NDArray[np.int64]],
    frame_positions: list[NDArray[np.float64]],
    frame_mass: list[NDArray[np.float64]],
    frame_channel: list[NDArray[np.uint8]],
    frame_state: list[NDArray[np.uint8]],
    frame_full_counts: list[int],
    contributor_offsets: list[int],
    contributor_ids: list[NDArray[np.int64]],
    contributor_mass: list[NDArray[np.float64]],
    contributor_channel: list[NDArray[np.uint8]],
    sensor_log: pd.DataFrame,
    mass_balance: Mapping[str, float],
    warnings: list[str],
    retained_columns: list[str],
    excluded_columns: Mapping[str, str],
) -> None:
    emitted = dispersion.emitted_particles
    final_state = np.full(len(emitted), 3, dtype=np.uint8)  # 3 = removed/zero residual
    final_position = emitted.positions_m.copy()
    final_mass = np.zeros(len(emitted), dtype=float)
    by_id = {int(parcel_id): index for index, parcel_id in enumerate(emitted.parcel_id)}
    for particles, state in (
        (dispersion.particles, 0),
        (dispersion.deposited_particles, 1),
        (dispersion.exited_particles, 2),
    ):
        for position, mass, parcel_id in zip(
            particles.positions_m, particles.mass_g, particles.parcel_id, strict=True
        ):
            index = by_id.get(int(parcel_id))
            if index is not None:
                final_state[index] = state
                final_position[index] = position
                final_mass[index] = mass

    with h5py.File(path, "w") as handle:
        handle.attrs.update(
            {
                "schema_name": "voxmaps_interactive_simulation_trace",
                "schema_version": "1.0.0",
                "simulation_id": simulation_id,
                "scenario_name": scenario_name,
                "software_version": __version__,
                "random_seed": int(config.project.random_seed),
                "creation_time_utc": creation_time,
                "source_csv_filename": source_path.name,
                "source_csv_sha256": source_checksum,
                "source_input_filename": source_path.name,
                "source_input_sha256": source_checksum,
                "scientific_scope": "testing/research forward simulation; not CFD or regulatory",
            }
        )
        configuration = handle.create_group("configuration")
        configuration.create_dataset(
            "json",
            data=json.dumps(config.model_dump(mode="json"), sort_keys=True),
            dtype=h5py.string_dtype("utf-8"),
        )
        ingestion = handle.create_group("ingestion")
        ingestion.create_dataset("report_json", data=json.dumps(ingestion_report), dtype=h5py.string_dtype("utf-8"))
        ingestion.create_dataset("column_mapping_json", data=json.dumps(dict(column_mapping)), dtype=h5py.string_dtype("utf-8"))
        _create_dataset(ingestion, "retained_columns", retained_columns)
        _create_dataset(ingestion, "excluded_columns", list(excluded_columns))
        _create_dataset(ingestion, "excluded_reasons", list(excluded_columns.values()))

        coordinates = handle.create_group("coordinates")
        reference = transformer.reference
        coordinates.attrs.update(
            {
                "coordinate_system": "local East-North-Up",
                "geodetic_crs": "EPSG:4979",
                "ecef_crs": "EPSG:4978",
                "origin_latitude_deg": reference.latitude,
                "origin_longitude_deg": reference.longitude,
                "origin_altitude_m": reference.altitude_m,
                "altitude_datum": reference.altitude_datum,
            }
        )
        _create_dataset(coordinates, "voxel_minimum_m", grid.minimum_m, unit="m")
        _create_dataset(coordinates, "voxel_maximum_m", grid.maximum_m, unit="m")
        _create_dataset(coordinates, "voxel_size_m", grid.size_m, unit="m")

        source_group = handle.create_group("source")
        source_group.attrs.update(
            {
                "source_id": source.name,
                "latitude_deg": float(config.source.latitude),
                "longitude_deg": float(config.source.longitude),
                "x_east_m": source.x_m,
                "y_north_m": source.y_m,
                "ground_z_m": source.ground_z_m,
                "stack_height_m": source.stack_height_m,
                "effective_release_height_m": source.effective_release_height_m,
                "stack_diameter_m": source.stack_diameter_m,
                "exit_velocity_mps": source.exit_velocity_mps,
                "pm25_emission_g_s": source.pm25_emission_g_s,
                "coarse_pm_emission_g_s": source.coarse_emission_g_s,
                "pm10_total_emission_g_s": source.pm10_total_emission_g_s,
            }
        )

        wind_group = handle.create_group("wind")
        raw_group = wind_group.create_group("raw")
        for name, values in raw_wind.items():
            unit = "s" if name == "elapsed_time_s" else ("m/s" if "speed" in name else ("degrees_from_north" if "direction" in name and "text" not in name else None))
            _create_dataset(raw_group, name, values, unit=unit)
        used_group = wind_group.create_group("used")
        used_group.attrs["interpolation_method"] = config.wind.interpolation_method
        for name in (
            "elapsed_time_s",
            "wind_speed_used_mps",
            "wind_direction_used_deg",
            "wind_u_used_mps",
            "wind_v_used_mps",
            "wind_source_used",
            "quality_flags",
        ):
            unit = "s" if name == "elapsed_time_s" else ("m/s" if "mps" in name else ("degrees_from_north" if "direction" in name else None))
            _create_dataset(used_group, name, used_wind[name].to_numpy(), unit=unit)

        trajectory_group = handle.create_group("trajectory")
        for name in (
            "source_row_index",
            "utc_time",
            "uptime_ms",
            "elapsed_time_s",
            "latitude_deg",
            "longitude_deg",
            "altitude_msl_m",
            "height_above_takeoff_m",
            "x_east_m",
            "y_north_m",
            "z_up_m",
        ):
            _create_dataset(trajectory_group, name, trajectory[name].to_numpy(), unit=_UNITS.get(name))

        parcels = handle.create_group("parcels")
        static = parcels.create_group("static")
        for name, values, unit in (
            ("parcel_id", emitted.parcel_id, None),
            ("channel", emitted.channel, "0=fine,1=coarse"),
            ("initial_mass_g", emitted.initial_mass_g, "g"),
            ("emission_time_s", emitted.emission_time_s, "s"),
            ("source_id", emitted.source_id, None),
            ("initial_position_m", emitted.positions_m, "m"),
            ("final_position_m", final_position, "m"),
            ("final_mass_g", final_mass, "g"),
            ("final_state", final_state, "0=active,1=deposited,2=exited,3=removed"),
        ):
            _create_dataset(static, name, values, unit=unit)

        playback = handle.create_group("playback")
        playback.attrs["state_codes"] = "0=active,1=deposited"
        playback.attrs["visual_decimation_only"] = True
        playback.attrs["numerical_concentration_uses_full_particle_set"] = True
        _create_dataset(playback, "frame_times_s", np.asarray(frame_times), unit="s")
        _create_dataset(playback, "offsets", np.asarray(frame_offsets, dtype=np.int64))
        _create_dataset(playback, "parcel_id", _concat(frame_ids, (0,), np.int64))
        _create_dataset(playback, "position_m", _concat(frame_positions, (0, 3), np.float32), unit="m")
        _create_dataset(playback, "mass_g", _concat(frame_mass, (0,), np.float32), unit="g")
        _create_dataset(playback, "channel", _concat(frame_channel, (0,), np.uint8))
        _create_dataset(playback, "state", _concat(frame_state, (0,), np.uint8))
        _create_dataset(playback, "full_numerical_particle_count", np.asarray(frame_full_counts, dtype=np.int64))

        deposition = handle.create_group("deposition")
        for name, values, unit in (
            ("parcel_id", dispersion.deposited_particles.parcel_id, None),
            ("position_m", dispersion.deposited_particles.positions_m, "m"),
            ("mass_g", dispersion.deposited_particles.mass_g, "g"),
            ("channel", dispersion.deposited_particles.channel, None),
        ):
            _create_dataset(deposition, name, values, unit=unit)

        contributors = handle.create_group("contributors")
        contributors.attrs["sampling_volume_m3"] = (
            config.sensor.sampling_region_size_x_m
            * config.sensor.sampling_region_size_y_m
            * config.sensor.sampling_region_size_z_m
        )
        contributors.attrs["background_pm25_ug_m3"] = config.background.pm25_ug_m3
        contributors.attrs["background_coarse_pm_ug_m3"] = config.background.coarse_pm_ug_m3
        _create_dataset(contributors, "offsets", np.asarray(contributor_offsets, dtype=np.int64))
        _create_dataset(contributors, "parcel_id", _concat(contributor_ids, (0,), np.int64))
        _create_dataset(contributors, "mass_g", _concat(contributor_mass, (0,), np.float64), unit="g")
        _create_dataset(contributors, "channel", _concat(contributor_channel, (0,), np.uint8))

        sensor_group = handle.create_group("sensor")
        sensor_group.attrs["output_sampling_mode"] = config.sensor.output_sampling_mode
        sensor_group.attrs["sampling_region_size_x_m"] = config.sensor.sampling_region_size_x_m
        sensor_group.attrs["sampling_region_size_y_m"] = config.sensor.sampling_region_size_y_m
        sensor_group.attrs["sampling_region_size_z_m"] = config.sensor.sampling_region_size_z_m
        for column in SENSOR_LOG_COLUMNS:
            _create_dataset(sensor_group, column, sensor_log[column].to_numpy(), unit=_UNITS.get(column))

        mass_group = handle.create_group("mass_balance")
        mass_group.attrs["units"] = "g"
        for name, value in mass_balance.items():
            mass_group.create_dataset(name, data=float(value))
        _create_dataset(handle, "warnings", warnings)


def _validate_trace(path: Path, sensor_log: pd.DataFrame) -> None:
    if list(sensor_log.columns) != SENSOR_LOG_COLUMNS:
        raise TraceContractError("sensor log columns do not match the required schema")
    if not np.allclose(
        sensor_log["pm10_true_ug_m3"].to_numpy(dtype=float),
        sensor_log["pm25_true_ug_m3"].to_numpy(dtype=float)
        + sensor_log["coarse_pm_true_ug_m3"].to_numpy(dtype=float),
        rtol=1e-12,
        atol=1e-9,
    ):
        raise TraceContractError("PM10 identity failed before publication")
    with h5py.File(path, "r") as handle:
        required = {
            "configuration",
            "coordinates",
            "source",
            "wind",
            "trajectory",
            "parcels",
            "playback",
            "contributors",
            "sensor",
            "mass_balance",
        }
        missing = sorted(required.difference(handle.keys()))
        if missing:
            raise TraceContractError(f"HDF5 trace is missing groups: {missing}")
        if len(handle["contributors/offsets"]) != len(sensor_log) + 1:
            raise TraceContractError("contributor offsets do not align with sensor samples")
        if handle["playback/position_m"].size and handle["playback/position_m"].compression != "gzip":
            raise TraceContractError("playback states are not compressed")


def _interpolate_interval_particles(
    start: ParticleSet,
    end_active: ParticleSet,
    grounded: ParticleSet,
    exited: ParticleSet,
    fraction: float,
) -> ParticleSet:
    """Interpolate a fixed solver step solely for time-aligned sampling.

    The dispersion solver advances once at its configured physics cadence.
    Intermediate flight samples use a non-mutating linear reconstruction of
    parcel state, so telemetry cadence cannot change parcel emission resolution,
    random draws, final mass balance, or the configured maximum solver step.
    """

    value = float(fraction)
    if not 0.0 <= value <= 1.0:
        raise ValueError("interval interpolation fraction must be in [0, 1]")
    if value >= 1.0 - 1e-12:
        return end_active
    if not len(start):
        return ParticleSet.empty()
    endpoint_ids = np.concatenate(
        (end_active.parcel_id, grounded.parcel_id, exited.parcel_id)
    )
    endpoint_positions = np.concatenate(
        (end_active.positions_m, grounded.positions_m, exited.positions_m), axis=0
    )
    endpoint_mass = np.concatenate(
        (end_active.mass_g, grounded.mass_g, exited.mass_g)
    )
    endpoint_age = np.concatenate(
        (end_active.age_s, grounded.age_s, exited.age_s)
    )
    order = np.argsort(endpoint_ids, kind="stable")
    ordered_ids = endpoint_ids[order]
    locations = np.searchsorted(ordered_ids, start.parcel_id)
    if (
        len(ordered_ids) != len(start)
        or np.any(locations >= len(ordered_ids))
        or not np.array_equal(ordered_ids[locations], start.parcel_id)
    ):
        raise TraceContractError("solver interval parcel identities cannot be reconstructed")
    endpoint_positions = endpoint_positions[order][locations]
    endpoint_mass = endpoint_mass[order][locations]
    endpoint_age = endpoint_age[order][locations]
    return ParticleSet.from_arrays(
        start.positions_m + value * (endpoint_positions - start.positions_m),
        start.mass_g + value * (endpoint_mass - start.mass_g),
        start.channel,
        start.age_s + value * (endpoint_age - start.age_s),
        parcel_id=start.parcel_id,
        emission_time_s=start.emission_time_s,
        source_id=start.source_id,
        initial_mass_g=start.initial_mass_g,
    )


def run_trace_simulation(
    flight_path: str | Path,
    config: Any,
    output_dir: str | Path,
    *,
    scenario_name: str | None = None,
    progress_callback: TraceProgressCallback | None = None,
    cancel_check: CancellationCheck | None = None,
) -> TraceRunResult:
    """Run a deterministic flight replay and atomically publish exactly two files."""

    resolved = _resolve_trace_config(config)
    source_path = Path(flight_path).expanduser().resolve()
    name = _safe_scenario_name(scenario_name or resolved.project.scenario_id)
    destination = Path(output_dir).expanduser().resolve()
    h5_name = f"{name}_simulation_trace.h5"
    csv_name = f"{name}_simulated_drone_sensor_log.csv"
    if destination.exists():
        unexpected = sorted(
            item.name for item in destination.iterdir() if item.name not in {h5_name, csv_name}
        )
        if unexpected:
            raise TraceContractError(
                "scenario output directory must be empty or contain only its prior two outputs; "
                f"found: {unexpected}"
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_checksum = _sha256(source_path)
    canonical_config = json.dumps(resolved.model_dump(mode="json"), sort_keys=True)
    simulation_id = hashlib.sha256(
        f"{source_checksum}\n{name}\n{canonical_config}".encode("utf-8")
    ).hexdigest()[:24]
    creation_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    staging = Path(tempfile.mkdtemp(prefix=".voxmaps-trace-", dir=destination.parent))
    try:
        _check_cancel(cancel_check)
        _notify(progress_callback, 0.01, "validating_csv", "Validating immutable flight input")
        ingestion = load_flight_csv(
            source_path,
            mapping=resolved.input.column_mapping,
            min_wind_coverage=resolved.wind.minimum_numeric_coverage,
            invalid_row_policy=resolved.input.invalid_row_policy,
        )
        # A second, text-only read preserves exact relevant cell spellings for
        # HDF5 audit (including values such as "NA" that pandas would otherwise
        # collapse to missing). Numerical validation remains authoritative in
        # ``load_flight_csv`` and never treats these strings as wind values.
        raw = read_flight_table(source_path, preserve_text=True)
        normalized = ingestion.normalized_flight
        _check_cancel(cancel_check)

        _notify(progress_callback, 0.08, "preparing_coordinate_system", "Preparing local East-North-Up coordinates")
        transformer = _make_transformer(resolved, normalized)
        normalized_enu = normalized.copy()
        east, north, up = transformer.to_enu(
            normalized["latitude"].to_numpy(dtype=float),
            normalized["longitude"].to_numpy(dtype=float),
            normalized["altitude_m"].to_numpy(dtype=float),
        )
        normalized_enu["enu_x_m"] = east
        normalized_enu["enu_y_m"] = north
        normalized_enu["enu_z_m"] = up
        source, _ = _resolve_source(resolved, normalized_enu, transformer)
        grid, domain = _make_grid_and_domain(resolved, normalized_enu, source)
        end_time_s = float(normalized_enu["elapsed_time_s"].max())
        if resolved.sensor.output_sampling_mode == "every_flight_sample":
            sample_times = normalized_enu["elapsed_time_s"].to_numpy(dtype=float)
            trajectory = _trajectory_samples(
                raw,
                normalized_enu,
                sample_times,
                np.arange(len(normalized_enu), dtype=np.int64),
            )
        else:
            sample_times = _sample_times(end_time_s, resolved.sensor.sample_interval_s)
            trajectory = _trajectory_samples(raw, normalized_enu, sample_times)
        desired_frame_times = _sample_times(
            end_time_s, resolved.simulation.playback_frame_interval_s
        )
        physics_events = _event_times(
            end_time_s,
            resolved.simulation.timestep_s,
            np.empty(0, dtype=float),
            resolved.simulation.warmup_s,
        )
        timeline_events = np.unique(
            np.round(
                np.concatenate((physics_events, sample_times, desired_frame_times)), 9
            )
        )
        _check_cancel(cancel_check)

        _notify(progress_callback, 0.15, "preparing_wind_timeline", "Preparing raw and simulation-used wind timelines")
        wind = build_wind_series(
            normalized_enu,
            resolved,
            timeline_events,
            random_seed=resolved.project.random_seed,
        ).reset_index(drop=True)
        used_wind = _used_wind_frame(wind, resolved, source.effective_release_height_m)
        used_wind = _apply_input_wind_provenance(used_wind, normalized_enu)
        raw_wind = _raw_wind_payload(raw, normalized_enu, ingestion.mapping)
        availability = assess_measured_wind(
            normalized_enu, resolved.wind.minimum_numeric_coverage
        )
        _check_cancel(cancel_check)

        dispersion = DispersionModel(resolved, domain, seed=resolved.project.random_seed)
        sensor = VirtualSensor(resolved, seed=resolved.project.random_seed + 10_000)
        sample_lookup: dict[float, list[int]] = {}
        for index, value in enumerate(sample_times):
            sample_lookup.setdefault(round(float(value), 9), []).append(index)
        frame_lookup = {round(float(value), 9) for value in desired_frame_times}
        wind_lookup = {
            round(float(value), 9): index
            for index, value in enumerate(used_wind["elapsed_time_s"])
        }
        scheduled_times = np.unique(
            np.round(np.concatenate((sample_times, desired_frame_times)), 9)
        )
        sensor_size = np.array(
            [
                resolved.sensor.sampling_region_size_x_m,
                resolved.sensor.sampling_region_size_y_m,
                resolved.sensor.sampling_region_size_z_m,
            ],
            dtype=float,
        )
        records: list[dict[str, Any]] = []
        contributor_offsets = [0]
        contributor_ids: list[NDArray[np.int64]] = []
        contributor_mass: list[NDArray[np.float64]] = []
        contributor_channel: list[NDArray[np.uint8]] = []
        frame_times: list[float] = []
        frame_offsets = [0]
        frame_ids: list[NDArray[np.int64]] = []
        frame_positions: list[NDArray[np.float64]] = []
        frame_mass: list[NDArray[np.float64]] = []
        frame_channel: list[NDArray[np.uint8]] = []
        frame_state: list[NDArray[np.uint8]] = []
        frame_full_counts: list[int] = []
        first_flight_wind = used_wind.loc[
            (used_wind["elapsed_time_s"] >= -1e-9)
            & (used_wind["wind_source_used"] != "missing_measured_zero_advection")
        ]
        first_wind_u = (
            float(first_flight_wind.iloc[0]["wind_u_used_mps"])
            if len(first_flight_wind)
            else 0.0
        )
        first_wind_v = (
            float(first_flight_wind.iloc[0]["wind_v_used_mps"])
            if len(first_flight_wind)
            else 0.0
        )

        def record_time(
            event: float,
            airborne: ParticleSet,
            deposited: ParticleSet,
        ) -> None:
            key = round(event, 9)
            if key in frame_lookup:
                ids, positions, masses, channels, states, full_count = _frame_snapshot(
                    airborne,
                    deposited,
                    resolved.simulation.visualization_parcel_limit,
                )
                frame_times.append(event)
                frame_ids.append(ids)
                frame_positions.append(positions)
                frame_mass.append(masses)
                frame_channel.append(channels)
                frame_state.append(states)
                frame_full_counts.append(full_count)
                frame_offsets.append(frame_offsets[-1] + len(ids))

            wind_index = wind_lookup[key]
            wind_row = used_wind.iloc[wind_index]
            for sample_index in sample_lookup.get(key, []):
                flight = trajectory.iloc[sample_index]
                centre = np.array(
                    [flight["x_east_m"], flight["y_north_m"], flight["z_up_m"]],
                    dtype=float,
                )
                concentration = sample_sensor_region(
                    airborne,
                    centre,
                    sensor_size,
                    background_pm25_ug_m3=resolved.background.pm25_ug_m3,
                    background_coarse_pm_ug_m3=resolved.background.coarse_pm_ug_m3,
                )
                reading = sensor.sample(
                    event,
                    concentration.pm25_true_ug_m3,
                    concentration.pm10_true_ug_m3,
                    None
                    if pd.isna(flight["relative_humidity_pct"])
                    else float(flight["relative_humidity_pct"]),
                )
                voxel_index = grid.index_of(centre)
                voxel_id = "" if voxel_index is None else grid.voxel_id(voxel_index)
                flags = combine_quality_flags(
                    wind_row.get("quality_flags", ""),
                    QualityFlag.OUT_OF_DOMAIN_POSITION if voxel_index is None else "",
                    reading.flags,
                )
                records.append(
                    {
                        "simulation_id": simulation_id,
                        "sample_id": sample_index,
                        "source_row_index": int(flight["source_row_index"]),
                        "utc_time": str(flight["utc_time"]),
                        "uptime_ms": float(flight["uptime_ms"]),
                        "elapsed_time_s": event,
                        "latitude_deg": float(flight["latitude_deg"]),
                        "longitude_deg": float(flight["longitude_deg"]),
                        "altitude_msl_m": float(flight["altitude_msl_m"]),
                        "height_above_takeoff_m": float(flight["height_above_takeoff_m"]),
                        "x_east_m": float(flight["x_east_m"]),
                        "y_north_m": float(flight["y_north_m"]),
                        "z_up_m": float(flight["z_up_m"]),
                        "ground_speed_mps": float(flight["ground_speed_mps"]),
                        "vertical_speed_mps": float(flight["vertical_speed_mps"]),
                        "heading_deg": float(flight["heading_deg"]),
                        "pitch_deg": float(flight["pitch_deg"]),
                        "roll_deg": float(flight["roll_deg"]),
                        "satellite_count": float(flight["satellite_count"]),
                        "gps_quality": str(flight["gps_quality"]),
                        "flight_state": str(flight["flight_state"]),
                        "wind_speed_raw_mps": float(flight["wind_speed_raw_mps"]),
                        "wind_direction_raw_deg": float(flight["wind_direction_raw_deg"]),
                        "wind_speed_used_mps": float(wind_row["wind_speed_used_mps"]),
                        "wind_direction_used_deg": float(wind_row["wind_direction_used_deg"]),
                        "wind_source": str(wind_row["wind_source_used"]),
                        "sampling_region_id": f"sr-{sample_index:08d}",
                        "voxel_id": voxel_id,
                        "pm25_true_ug_m3": concentration.pm25_true_ug_m3,
                        "coarse_pm_true_ug_m3": concentration.coarse_pm_true_ug_m3,
                        "pm10_true_ug_m3": concentration.pm10_true_ug_m3,
                        "pm25_sensor_ug_m3": reading.pm25_ug_m3,
                        "pm10_sensor_ug_m3": reading.pm10_ug_m3,
                        "fine_contributing_parcels": concentration.fine_count,
                        "coarse_contributing_parcels": concentration.coarse_count,
                        "quality_flags": flags,
                    }
                )
                contributor_ids.append(concentration.parcel_id)
                contributor_mass.append(concentration.mass_g)
                contributor_channel.append(concentration.channel)
                contributor_offsets.append(contributor_offsets[-1] + len(concentration.parcel_id))

        schedule_position = 0
        initial_time = float(physics_events[0])
        while (
            schedule_position < len(scheduled_times)
            and scheduled_times[schedule_position] <= initial_time + 1e-9
        ):
            scheduled = float(scheduled_times[schedule_position])
            if scheduled >= initial_time - 1e-9:
                record_time(scheduled, dispersion.particles, dispersion.deposited_particles)
            schedule_position += 1

        for physics_index in range(1, len(physics_events)):
            _check_cancel(cancel_check)
            interval_start_s = float(physics_events[physics_index - 1])
            interval_end_s = float(physics_events[physics_index])
            dt_s = interval_end_s - interval_start_s
            prior = used_wind.iloc[wind_lookup[round(interval_start_s, 9)]]
            wind_u = float(prior["wind_u_used_mps"])
            wind_v = float(prior["wind_v_used_mps"])
            if interval_start_s < 0.0 and resolved.simulation.preflight_wind_policy == "none":
                wind_u = wind_v = 0.0
            elif (
                interval_start_s < 0.0
                and resolved.simulation.preflight_wind_policy == "hold_first_wind"
            ):
                wind_u, wind_v = first_wind_u, first_wind_v
            dispersion.emit(source, max(0.0, interval_start_s), dt_s)
            interval_start_particles = dispersion.particles.copy()
            deposited_before = dispersion.deposited_particles.copy()
            step_result = dispersion.step(wind_u, wind_v, dt_s)

            while (
                schedule_position < len(scheduled_times)
                and scheduled_times[schedule_position] <= interval_end_s + 1e-9
            ):
                scheduled = float(scheduled_times[schedule_position])
                if scheduled > interval_start_s + 1e-9:
                    interpolation_fraction = (scheduled - interval_start_s) / dt_s
                    airborne = _interpolate_interval_particles(
                        interval_start_particles,
                        dispersion.particles,
                        step_result.grounded_particles,
                        step_result.exited_particles,
                        interpolation_fraction,
                    )
                    deposited = (
                        dispersion.deposited_particles
                        if interpolation_fraction >= 1.0 - 1e-12
                        else deposited_before
                    )
                    record_time(scheduled, airborne, deposited)
                    _check_cancel(cancel_check)
                schedule_position += 1

            fraction = physics_index / max(1, len(physics_events) - 1)
            if (
                physics_index == 1
                or physics_index == len(physics_events) - 1
                or physics_index % max(1, len(physics_events) // 100) == 0
            ):
                _notify(
                    progress_callback,
                    0.20 + 0.50 * fraction,
                    "emitting_transporting_parcels",
                    f"Transported {physics_index}/{len(physics_events) - 1} solver steps; "
                    f"sampled {len(records):,}/{len(sample_times):,} flight rows; "
                    f"{len(dispersion.particles):,} airborne parcels",
                )

        _notify(progress_callback, 0.72, "sampling_drone_sensor", f"Completed {len(records):,} synchronized sensor samples")
        sensor_log = pd.DataFrame.from_records(records, columns=SENSOR_LOG_COLUMNS)
        if _sha256(source_path) != source_checksum:
            raise TraceContractError("source flight input changed during simulation; outputs were not published")
        mass_balance = dispersion.mass_balance_report()
        warnings = list(ingestion.report.get("warnings", []))
        if resolved.wind.mode == "synthetic":
            warnings.append(
                "Simulation used explicitly selected synthetic fallback wind; raw input wind remains stored separately."
            )
        if not availability.usable:
            warnings.append(availability.reason)
        retained = {value for value in ingestion.mapping.values() if value is not None}
        for candidates in (
            ("altitude_above_seaLevel(feet)", "altitude_msl_m"),
            ("speed(mph)", "ground_speed_mps"),
            ("zSpeed(mph)", "vertical_speed_mps"),
            ("compass_heading(degrees)", "heading_deg"),
            ("pitch(degrees)", "pitch_deg"),
            ("roll(degrees)", "roll_deg"),
            ("satellites", "satellite_count"),
            ("gpslevel", "gps_quality"),
            ("flycState", "flight_state"),
        ):
            column = _find_column(raw.columns.astype(str).tolist(), candidates)
            if column is not None:
                retained.add(column)
        retained_columns, excluded_columns = _column_audit(raw, retained)
        _check_cancel(cancel_check)

        _notify(progress_callback, 0.78, "generating_playback_data", f"Generating {len(frame_times):,} synchronized playback frames")
        staged_h5 = staging / h5_name
        staged_csv = staging / csv_name
        _write_trace_h5(
            staged_h5,
            simulation_id=simulation_id,
            scenario_name=name,
            creation_time=creation_time,
            source_path=source_path,
            source_checksum=source_checksum,
            config=resolved,
            ingestion_report=ingestion.report,
            column_mapping=ingestion.mapping,
            transformer=transformer,
            source=source,
            grid=grid,
            raw_wind=raw_wind,
            used_wind=used_wind,
            trajectory=trajectory,
            dispersion=dispersion,
            frame_times=frame_times,
            frame_offsets=frame_offsets,
            frame_ids=frame_ids,
            frame_positions=frame_positions,
            frame_mass=frame_mass,
            frame_channel=frame_channel,
            frame_state=frame_state,
            frame_full_counts=frame_full_counts,
            contributor_offsets=contributor_offsets,
            contributor_ids=contributor_ids,
            contributor_mass=contributor_mass,
            contributor_channel=contributor_channel,
            sensor_log=sensor_log,
            mass_balance=mass_balance,
            warnings=warnings,
            retained_columns=retained_columns,
            excluded_columns=excluded_columns,
        )
        sensor_log.to_csv(staged_csv, index=False, columns=SENSOR_LOG_COLUMNS, lineterminator="\n")
        _check_cancel(cancel_check)
        _notify(progress_callback, 0.91, "validating_outputs", "Validating PM identity, HDF5 schema, compression, and contributor indices")
        _validate_trace(staged_h5, sensor_log)
        if set(item.name for item in staging.iterdir()) != {h5_name, csv_name}:
            raise TraceContractError("staging directory does not contain exactly two primary files")
        _check_cancel(cancel_check)

        _notify(progress_callback, 0.97, "writing_output_files", "Publishing the two scenario files atomically")
        destination.mkdir(parents=True, exist_ok=True)
        final_h5 = destination / h5_name
        final_csv = destination / csv_name
        published: list[Path] = []
        try:
            os.replace(staged_h5, final_h5)
            published.append(final_h5)
            os.replace(staged_csv, final_csv)
            published.append(final_csv)
        except Exception:
            for path in published:
                path.unlink(missing_ok=True)
            raise
        final_names = {item.name for item in destination.iterdir()}
        if final_names != {h5_name, csv_name}:
            for path in published:
                path.unlink(missing_ok=True)
            raise TraceContractError(f"final scenario directory violates two-file contract: {sorted(final_names)}")
        _notify(progress_callback, 1.0, "complete", "Simulation complete; two scenario files published")
        return TraceRunResult(
            simulation_id,
            final_h5,
            final_csv,
            sensor_log,
            mass_balance,
            tuple(dict.fromkeys(warnings)),
            len(frame_times),
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _decode(value: Any) -> Any:
    if isinstance(value, (bytes, np.bytes_)):
        return value.decode("utf-8")
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _read_sensor_frame(handle: h5py.File) -> pd.DataFrame:
    payload: dict[str, Any] = {}
    for column in SENSOR_LOG_COLUMNS:
        values = handle[f"sensor/{column}"][...]
        if values.dtype.kind in {"S", "O", "U"}:
            payload[column] = [_decode(value) for value in values]
        else:
            payload[column] = values
    return pd.DataFrame(payload, columns=SENSOR_LOG_COLUMNS)


def read_trace_summary(path: str | Path) -> dict[str, Any]:
    """Return JSON-compatible metadata, trajectories, plots, and frame indices."""

    with h5py.File(Path(path), "r") as handle:
        sensor = _read_sensor_frame(handle)
        source_attrs = {key: _decode(value) for key, value in handle["source"].attrs.items()}
        source = {
            **source_attrs,
            "x": source_attrs.get("x_east_m", 0.0),
            "y": source_attrs.get("y_north_m", 0.0),
            "z": source_attrs.get("ground_z_m", 0.0),
            "latitude": source_attrs.get("latitude_deg"),
            "longitude": source_attrs.get("longitude_deg"),
            "diameter_m": source_attrs.get("stack_diameter_m"),
        }
        coordinates = {
            key: _decode(value) for key, value in handle["coordinates"].attrs.items()
        }
        frame_times = handle["playback/frame_times_s"][...].astype(float).tolist()
        warnings = [_decode(value) for value in handle["warnings"][...]]
        mass_balance = {
            name: float(dataset[()])
            for name, dataset in handle["mass_balance"].items()
        }
        configuration = json.loads(_decode(handle["configuration/json"][()]))
        frame_interval = (
            float(np.median(np.diff(frame_times))) if len(frame_times) > 1 else 0.0
        )
        flight_path = [
            {"x": float(row.x_east_m), "y": float(row.y_north_m), "z": float(row.z_up_m)}
            for row in sensor[["x_east_m", "y_north_m", "z_up_m"]].itertuples(index=False)
        ]
        return {
            "simulation_id": _decode(handle.attrs["simulation_id"]),
            "scenario_name": _decode(handle.attrs["scenario_name"]),
            "schema_version": _decode(handle.attrs["schema_version"]),
            "software_version": _decode(handle.attrs["software_version"]),
            "source_csv_filename": _decode(handle.attrs["source_csv_filename"]),
            "source_csv_sha256": _decode(handle.attrs["source_csv_sha256"]),
            "random_seed": int(handle.attrs["random_seed"]),
            "source": source,
            "coordinates": coordinates,
            "coordinate_origin": {
                "latitude_deg": coordinates.get("origin_latitude_deg"),
                "longitude_deg": coordinates.get("origin_longitude_deg"),
                "altitude_m": coordinates.get("origin_altitude_m"),
            },
            "frame_count": len(frame_times),
            "frame_times_s": frame_times,
            "frame_interval_s": frame_interval,
            "sample_count": len(sensor),
            "duration_s": float(sensor["elapsed_time_s"].iloc[-1]) if len(sensor) else 0.0,
            "flight_path": flight_path,
            "warnings": warnings,
            "mass_balance": mass_balance,
            "configuration": configuration,
            "numerical_particle_count": int(
                np.max(handle["playback/full_numerical_particle_count"][...], initial=0)
            ),
            "visual_decimation_only": bool(handle["playback"].attrs["visual_decimation_only"]),
        }


def load_playback_frame(
    path: str | Path,
    frame_index: int,
    *,
    max_particles: int | None = None,
) -> dict[str, Any]:
    """Load one HDF5 playback frame without rerunning the solver."""

    with h5py.File(Path(path), "r") as handle:
        times = handle["playback/frame_times_s"]
        index = int(frame_index)
        if index < 0 or index >= len(times):
            raise IndexError(f"frame_index must be in [0, {len(times) - 1}]")
        offsets = handle["playback/offsets"]
        start, stop = int(offsets[index]), int(offsets[index + 1])
        selection = np.arange(start, stop, dtype=np.int64)
        if max_particles is not None and max_particles > 0 and len(selection) > max_particles:
            selection = selection[np.linspace(0, len(selection) - 1, max_particles, dtype=np.int64)]
        group = handle["playback"]
        elapsed = float(times[index])
        sensor_times = handle["sensor/elapsed_time_s"][...]
        sensor_index = int(np.argmin(np.abs(sensor_times - elapsed)))
        sensor = _read_sensor_frame(handle).iloc[sensor_index].to_dict()
        wind_times = handle["wind/used/elapsed_time_s"][...]
        wind_index = int(np.argmin(np.abs(wind_times - elapsed)))
        sensor_json = {key: _decode(value) for key, value in sensor.items()}
        wind_speed = float(handle["wind/used/wind_speed_used_mps"][wind_index])
        wind_direction = float(handle["wind/used/wind_direction_used_deg"][wind_index])
        wind_source = _decode(handle["wind/used/wind_source_used"][wind_index])
        returned_count = len(selection)
        full_count = int(group["full_numerical_particle_count"][index])
        return {
            "frame_index": index,
            "elapsed_time_s": elapsed,
            "full_numerical_particle_count": full_count,
            "numerical_particle_count": full_count,
            "stored_particle_count": stop - start,
            "returned_particle_count": returned_count,
            "rendered_particle_count": returned_count,
            "parcel_id": group["parcel_id"][selection].astype(np.int64).tolist(),
            "position_m": group["position_m"][selection].astype(float).tolist(),
            "mass_g": group["mass_g"][selection].astype(float).tolist(),
            "channel": group["channel"][selection].astype(np.uint8).tolist(),
            "state": group["state"][selection].astype(np.uint8).tolist(),
            "sensor_sample": sensor_json,
            "drone": {
                "x": sensor_json.get("x_east_m"),
                "y": sensor_json.get("y_north_m"),
                "z": sensor_json.get("z_up_m"),
            },
            "utc_time": sensor_json.get("utc_time"),
            "pm25_true_ug_m3": sensor_json.get("pm25_true_ug_m3"),
            "pm10_true_ug_m3": sensor_json.get("pm10_true_ug_m3"),
            "pm25_sensor_ug_m3": sensor_json.get("pm25_sensor_ug_m3"),
            "pm10_sensor_ug_m3": sensor_json.get("pm10_sensor_ug_m3"),
            "sensor_region": {
                "size_x_m": float(handle["sensor"].attrs["sampling_region_size_x_m"]),
                "size_y_m": float(handle["sensor"].attrs["sampling_region_size_y_m"]),
                "size_z_m": float(handle["sensor"].attrs["sampling_region_size_z_m"]),
            },
            "wind_speed_mps": wind_speed,
            "wind_direction_deg": wind_direction,
            "wind_source": wind_source,
            "wind": {
                "speed_mps": wind_speed,
                "direction_from_deg": wind_direction,
                "source": wind_source,
            },
        }


def reconstruct_sensor_row(path: str | Path, sample_index: int) -> dict[str, Any]:
    """Reconstruct true PM concentrations from exact HDF5 contributor records."""

    with h5py.File(Path(path), "r") as handle:
        index = int(sample_index)
        sensor = _read_sensor_frame(handle)
        if index < 0 or index >= len(sensor):
            raise IndexError(f"sample_index must be in [0, {len(sensor) - 1}]")
        offsets = handle["contributors/offsets"]
        start, stop = int(offsets[index]), int(offsets[index + 1])
        mass = handle["contributors/mass_g"][start:stop].astype(float)
        channel = handle["contributors/channel"][start:stop].astype(np.uint8)
        group = handle["contributors"]
        volume = float(group.attrs["sampling_volume_m3"])
        fine = float(mass[channel == FINE].sum()) * 1_000_000.0 / volume + float(
            group.attrs["background_pm25_ug_m3"]
        )
        coarse = float(mass[channel == COARSE].sum()) * 1_000_000.0 / volume + float(
            group.attrs["background_coarse_pm_ug_m3"]
        )
        stored = sensor.iloc[index].to_dict()
        reconstructed = {
            "pm25_true_ug_m3": fine,
            "coarse_pm_true_ug_m3": coarse,
            "pm10_true_ug_m3": fine + coarse,
        }
        return {
            "sample_index": index,
            "contributor_count": stop - start,
            "contributor_parcel_ids": handle["contributors/parcel_id"][start:stop].astype(np.int64).tolist(),
            "stored": {key: _decode(value) for key, value in stored.items()},
            "reconstructed": reconstructed,
            "matches": all(
                np.isclose(float(stored[key]), value, rtol=1e-12, atol=1e-9)
                for key, value in reconstructed.items()
            ),
        }


# Backend-friendly alias used by early desktop prototypes.
run_interactive_simulation = run_trace_simulation


__all__ = [
    "SENSOR_LOG_COLUMNS",
    "SensorRegionSample",
    "SimulationCancelled",
    "TraceContractError",
    "TraceRunResult",
    "load_playback_frame",
    "read_trace_summary",
    "reconstruct_sensor_row",
    "run_interactive_simulation",
    "run_trace_simulation",
    "sample_sensor_region",
]
