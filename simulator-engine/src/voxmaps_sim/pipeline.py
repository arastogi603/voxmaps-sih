"""End-to-end flight replay, dispersion, sensing, and export pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Callable, Any

import numpy as np
import pandas as pd

from .config import SimulationConfig, load_config
from .coordinates import ENUReference, ENUTransformer, add_enu_coordinates
from .dispersion import DispersionModel, DomainBounds
from .exports import aggregate_voxel_observations, export_run
from .ingestion import FlightDataResult, discover_flight_csv, load_flight_csv
from .quality import QualityFlag, combine_quality_flags, quality_score
from .sensor import VirtualSensor
from .source import SourceModel
from .voxels import VoxelGrid
from .wind import assess_measured_wind, build_wind_series, meteorological_to_uv

LOGGER = logging.getLogger(__name__)
ProgressCallback = Callable[[float, str], None]


@dataclass(slots=True)
class RunResult:
    """In-memory results and paths produced by one scenario."""

    samples: pd.DataFrame
    voxel_observations: pd.DataFrame
    ground_truth_voxels: pd.DataFrame
    normalized_flight: pd.DataFrame
    source_label: dict[str, Any]
    metadata: dict[str, Any]
    mass_balance: dict[str, float]
    run_report: dict[str, Any]
    artifacts: dict[str, Path]
    config: SimulationConfig

    @property
    def output_paths(self) -> dict[str, Path]:
        return self.artifacts


def _notify(callback: ProgressCallback | None, fraction: float, message: str) -> None:
    if callback is not None:
        callback(float(np.clip(fraction, 0.0, 1.0)), message)


def _resolve_config(config: SimulationConfig | str | Path) -> SimulationConfig:
    if isinstance(config, SimulationConfig):
        return config.model_copy(deep=True)
    return load_config(config)


def _resolve_flight_path(
    flight_path: str | Path | None, config: SimulationConfig
) -> Path:
    candidate = flight_path if flight_path is not None else config.input.flight_csv
    if candidate is None:
        return discover_flight_csv(Path.cwd())
    path = Path(candidate).expanduser()
    if path.is_dir():
        return discover_flight_csv(path)
    return path


def _make_transformer(config: SimulationConfig, flight: pd.DataFrame) -> ENUTransformer:
    coordinates = config.coordinates
    if coordinates.reference_mode == "configured":
        assert coordinates.reference_latitude is not None
        assert coordinates.reference_longitude is not None
        return ENUTransformer(
            ENUReference(
                coordinates.reference_latitude,
                coordinates.reference_longitude,
                coordinates.reference_altitude_m,
                "configured WGS84/local reference",
            )
        )
    if coordinates.reference_mode == "source_location":
        if config.source.latitude is None or config.source.longitude is None:
            raise ValueError(
                "coordinates.reference_mode='source_location' requires configured "
                "source latitude and longitude"
            )
        return ENUTransformer(
            ENUReference(
                config.source.latitude,
                config.source.longitude,
                coordinates.reference_altitude_m,
                "configured source location",
            )
        )
    return ENUTransformer.from_flight(
        flight,
        flat_ground=coordinates.flat_ground,
    )


def _resolve_source(
    config: SimulationConfig,
    flight: pd.DataFrame,
    transformer: ENUTransformer,
) -> tuple[SourceModel, bool]:
    generated = config.source.latitude is None
    if not generated:
        assert config.source.latitude is not None and config.source.longitude is not None
        x_m, y_m, _ = transformer.to_enu(
            config.source.latitude,
            config.source.longitude,
            config.source.ground_elevation_m,
        )
    else:
        centre_x = 0.5 * (float(flight["enu_x_m"].min()) + float(flight["enu_x_m"].max()))
        centre_y = 0.5 * (float(flight["enu_y_m"].min()) + float(flight["enu_y_m"].max()))
        x_m = centre_x + config.source.offset_east_from_flight_centre_m
        y_m = centre_y + config.source.offset_north_from_flight_centre_m
        latitude, longitude, _ = transformer.to_geodetic(x_m, y_m, 0.0)
        # Replace the validated section atomically because assignment validation
        # correctly rejects a transient state containing only one coordinate.
        config.source = config.source.model_copy(
            update={"latitude": float(latitude), "longitude": float(longitude)}
        )
    source = SourceModel.from_config(
        config,
        source_x_m=float(x_m),
        source_y_m=float(y_m),
        source_ground_z_m=0.0,
    )
    return source, generated


def _event_times(
    end_time_s: float,
    timestep_s: float,
    sample_times: np.ndarray,
    warmup_s: float = 0.0,
) -> np.ndarray:
    start_time_s = -float(warmup_s)
    physics = np.arange(
        start_time_s, end_time_s + timestep_s * 0.25, timestep_s, dtype=float
    )
    if not len(physics) or physics[-1] < end_time_s - 1e-9:
        physics = np.append(physics, end_time_s)
    else:
        physics[-1] = min(physics[-1], end_time_s)
    events = np.unique(
        np.round(
            np.concatenate((physics, sample_times, [start_time_s, 0.0, end_time_s])),
            9,
        )
    )
    return events[
        (events >= start_time_s - 1e-9) & (events <= end_time_s + 1e-9)
    ]


def _sample_times(end_time_s: float, interval_s: float) -> np.ndarray:
    times = np.arange(0.0, end_time_s + interval_s * 0.25, interval_s, dtype=float)
    if not len(times) or times[-1] < end_time_s - 1e-9:
        times = np.append(times, end_time_s)
    return np.unique(np.round(times[times <= end_time_s + 1e-9], 9))


def _interp_optional(frame: pd.DataFrame, column: str, times: np.ndarray) -> np.ndarray:
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
    source_times = frame["elapsed_time_s"].to_numpy(dtype=float)
    valid = np.isfinite(values) & np.isfinite(source_times)
    if not np.any(valid):
        return np.full(len(times), np.nan)
    collapsed = (
        pd.DataFrame({"time": source_times[valid], "value": values[valid]})
        .groupby("time", sort=True, as_index=False)["value"]
        .mean()
    )
    return np.interp(
        times,
        collapsed["time"].to_numpy(dtype=float),
        collapsed["value"].to_numpy(dtype=float),
    )


def resample_trajectory(flight: pd.DataFrame, sample_times: np.ndarray) -> pd.DataFrame:
    """Linearly resample the recorded path while preserving the original table separately."""

    result = pd.DataFrame({"elapsed_time_s": sample_times})
    for column in (
        "latitude",
        "longitude",
        "altitude_m",
        "enu_x_m",
        "enu_y_m",
        "enu_z_m",
        "temperature_c",
        "relative_humidity_pct",
        "pressure_hpa",
    ):
        result[column] = _interp_optional(flight, column, sample_times)
    source_times = flight["elapsed_time_s"].to_numpy(dtype=float)
    exact = np.isclose(
        sample_times[:, None], source_times[None, :], atol=1e-7, rtol=0.0
    ).any(axis=1)
    result["trajectory_interpolated"] = ~exact
    parsed = pd.to_datetime(flight.get("timestamp_utc"), utc=True, errors="coerce")
    valid_timestamp = parsed.notna()
    if valid_timestamp.any():
        anchor_index = int(np.flatnonzero(valid_timestamp.to_numpy())[0])
        anchor_time = parsed.iloc[anchor_index]
        anchor_elapsed = float(flight.iloc[anchor_index]["elapsed_time_s"])
        result["original_timestamp"] = (
            anchor_time + pd.to_timedelta(sample_times - anchor_elapsed, unit="s")
        )
    else:
        result["original_timestamp"] = pd.NaT
    return result


def _make_grid_and_domain(
    config: SimulationConfig, flight: pd.DataFrame, source: SourceModel
) -> tuple[VoxelGrid, DomainBounds]:
    padding = config.simulation.horizontal_padding_m
    min_x = min(float(flight["enu_x_m"].min()), source.x_m) - padding
    max_x = max(float(flight["enu_x_m"].max()), source.x_m) + padding
    min_y = min(float(flight["enu_y_m"].min()), source.y_m) - padding
    max_y = max(float(flight["enu_y_m"].max()), source.y_m) + padding
    grid_min_z = min(
        config.simulation.vertical_min_m,
        float(flight["enu_z_m"].min()),
        0.0,
    )
    grid_max_z = max(
        config.simulation.vertical_max_m,
        float(flight["enu_z_m"].max()),
        float(source.release_position_m[2]),
    )
    grid = VoxelGrid(
        [min_x, min_y, grid_min_z],
        [max_x, max_y, grid_max_z],
        config.voxel.size_x_m,
        config.voxel.size_y_m,
        config.voxel.size_z_m,
    )
    # Parcel ground is always z=0 even when small negative drone heights are
    # retained and indexable by the observation grid.
    particle_domain = DomainBounds(
        float(grid.minimum_m[0]),
        float(grid.maximum_m[0]),
        float(grid.minimum_m[1]),
        float(grid.maximum_m[1]),
        0.0,
        float(grid.maximum_m[2]),
    )
    return grid, particle_domain


def _ground_truth_frame(grid: VoxelGrid, dispersion: DispersionModel, config: SimulationConfig, time_s: float) -> pd.DataFrame:
    values = grid.aggregate(
        dispersion.particles,
        include_empty=False,
        background_pm25_ug_m3=config.background.pm25_ug_m3,
        background_coarse_pm_ug_m3=config.background.coarse_pm_ug_m3,
    )
    indices = values["indices"]
    centres = values["centres_m"]
    return pd.DataFrame(
        {
            "snapshot_elapsed_time_s": np.full(len(indices), time_s),
            "voxel_id": values["voxel_id"],
            "voxel_x_index": indices[:, 0] if len(indices) else np.array([], dtype=int),
            "voxel_y_index": indices[:, 1] if len(indices) else np.array([], dtype=int),
            "voxel_z_index": indices[:, 2] if len(indices) else np.array([], dtype=int),
            "voxel_center_x_m": centres[:, 0] if len(centres) else np.array([]),
            "voxel_center_y_m": centres[:, 1] if len(centres) else np.array([]),
            "voxel_center_z_m": centres[:, 2] if len(centres) else np.array([]),
            "fine_mass_g": values["fine_mass_g"],
            "coarse_mass_g": values["coarse_mass_g"],
            "fine_parcel_count": values["fine_parcel_count"],
            "coarse_parcel_count": values["coarse_parcel_count"],
            "pm25_true_ug_m3": values["pm25_true_ug_m3"],
            "coarse_pm_true_ug_m3": values["coarse_pm_true_ug_m3"],
            "pm10_true_ug_m3": values["pm10_true_ug_m3"],
        }
    )


def run_simulation(
    flight_path: str | Path | None,
    config: SimulationConfig | str | Path,
    output_dir: str | Path,
    progress_callback: ProgressCallback | None = None,
    *,
    write_visualization: bool = True,
) -> RunResult:
    """Run one complete, deterministic scenario and write all required artifacts."""

    resolved = _resolve_config(config)
    source_path = _resolve_flight_path(flight_path, resolved)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _notify(progress_callback, 0.01, "Validating and normalizing flight CSV")
    ingestion: FlightDataResult = load_flight_csv(
        source_path,
        mapping=resolved.input.column_mapping,
        min_wind_coverage=resolved.wind.minimum_numeric_coverage,
        invalid_row_policy=resolved.input.invalid_row_policy,
        output_dir=destination,
    )
    normalized = ingestion.normalized_flight
    transformer = _make_transformer(resolved, normalized)
    normalized_enu = add_enu_coordinates(normalized, transformer)
    source, generated_source_location = _resolve_source(resolved, normalized_enu, transformer)
    grid, particle_domain = _make_grid_and_domain(resolved, normalized_enu, source)
    end_time_s = float(normalized_enu["elapsed_time_s"].max())
    sample_times = _sample_times(
        end_time_s, resolved.simulation.trajectory_sample_interval_s
    )
    trajectory = resample_trajectory(normalized_enu, sample_times)
    events = _event_times(
        end_time_s,
        resolved.simulation.timestep_s,
        sample_times,
        resolved.simulation.warmup_s,
    )
    _notify(progress_callback, 0.08, "Resolving atmospheric wind provenance")
    wind = build_wind_series(
        normalized_enu,
        resolved,
        events,
        random_seed=resolved.project.random_seed,
    ).reset_index(drop=True)
    availability = assess_measured_wind(
        normalized_enu, resolved.wind.minimum_numeric_coverage
    )

    dispersion = DispersionModel(
        resolved,
        particle_domain,
        seed=resolved.project.random_seed,
    )
    sensor = VirtualSensor(resolved, seed=resolved.project.random_seed + 10_000)
    sample_lookup = {round(float(value), 9): index for index, value in enumerate(sample_times)}
    records: list[dict[str, Any]] = []
    previous_event = float(events[0])
    last_progress_bucket = -1
    missing_wind_steps = 0
    finite_wind = np.isfinite(wind["wind_u_mps"].to_numpy(dtype=float)) & np.isfinite(
        wind["wind_v_mps"].to_numpy(dtype=float)
    )
    nonnegative_events = events >= -1e-9
    first_flight_valid = np.flatnonzero(finite_wind & nonnegative_events)
    first_wind_u = (
        float(wind.iloc[first_flight_valid[0]]["wind_u_mps"])
        if len(first_flight_valid)
        else 0.0
    )
    first_wind_v = (
        float(wind.iloc[first_flight_valid[0]]["wind_v_mps"])
        if len(first_flight_valid)
        else 0.0
    )

    for event_index, event_value in enumerate(events):
        event = float(event_value)
        if event_index:
            dt_s = event - previous_event
            prior_wind = wind.iloc[event_index - 1]
            wind_u = float(prior_wind["wind_u_mps"])
            wind_v = float(prior_wind["wind_v_mps"])
            if previous_event < 0.0 and resolved.simulation.preflight_wind_policy == "none":
                wind_u = wind_v = 0.0
            elif (
                previous_event < 0.0
                and resolved.simulation.preflight_wind_policy == "hold_first_wind"
            ):
                wind_u, wind_v = first_wind_u, first_wind_v
            elif not np.isfinite(wind_u) or not np.isfinite(wind_v):
                # Long measured gaps remain explicitly missing. A zero-advection
                # step keeps mass accounting defined without inventing wind.
                wind_u = wind_v = 0.0
                missing_wind_steps += 1
            if resolved.wind.height_adjustment_enabled:
                height = max(source.effective_release_height_m, 1e-6)
                factor = (height / resolved.wind.reference_height_m) ** resolved.wind.power_law_exponent
                wind_u *= factor
                wind_v *= factor
            # During pre-flight spin-up, t=0 emission conditions are held so a
            # configured source active at flight start can establish a plume.
            emission_clock_s = max(0.0, previous_event)
            dispersion.emit(source, emission_clock_s, dt_s)
            dispersion.step(wind_u, wind_v, dt_s)
        previous_event = event

        sample_index = sample_lookup.get(round(event, 9))
        if sample_index is not None:
            trajectory_row = trajectory.iloc[sample_index]
            wind_row = wind.iloc[event_index]
            position = np.array(
                [trajectory_row["enu_x_m"], trajectory_row["enu_y_m"], trajectory_row["enu_z_m"]],
                dtype=float,
            )
            concentration = grid.sample_position(
                dispersion.particles,
                position,
                background_pm25_ug_m3=resolved.background.pm25_ug_m3,
                background_coarse_pm_ug_m3=resolved.background.coarse_pm_ug_m3,
            )
            if concentration.voxel_id is None:
                true_pm25 = resolved.background.pm25_ug_m3
                true_coarse = resolved.background.coarse_pm_ug_m3
                true_pm10 = true_pm25 + true_coarse
                centre = np.array([np.nan, np.nan, np.nan])
                lower = upper = np.array([np.nan, np.nan, np.nan])
            else:
                true_pm25 = concentration.pm25_true_ug_m3
                true_coarse = concentration.coarse_pm_true_ug_m3
                true_pm10 = concentration.pm10_true_ug_m3
                centre = grid.centre(concentration.voxel_index)
                lower, upper = grid.boundaries(concentration.voxel_index)
            humidity = trajectory_row.get("relative_humidity_pct", np.nan)
            reading = sensor.sample(
                event,
                true_pm25,
                true_pm10,
                None if pd.isna(humidity) else float(humidity),
            )
            wind_flags = wind_row.get("quality_flags", "")
            if not np.isfinite(float(wind_row["wind_speed_mps"])):
                wind_flags = combine_quality_flags(wind_flags, QualityFlag.MISSING_WIND)
            flags = combine_quality_flags(
                wind_flags,
                QualityFlag.INTERPOLATION if trajectory_row["trajectory_interpolated"] else "",
                concentration.quality_flags,
                reading.flags,
            )
            ix, iy, iz = concentration.voxel_index
            record = {
                "original_timestamp": trajectory_row["original_timestamp"],
                "elapsed_time_s": event,
                "latitude": float(trajectory_row["latitude"]),
                "longitude": float(trajectory_row["longitude"]),
                "altitude_m": float(trajectory_row["altitude_m"]),
                "enu_x_m": float(position[0]),
                "enu_y_m": float(position[1]),
                "enu_z_m": float(position[2]),
                "current_voxel_id": concentration.voxel_id,
                "voxel_id": concentration.voxel_id,
                "voxel_x_index": ix,
                "voxel_y_index": iy,
                "voxel_z_index": iz,
                "voxel_center_x_m": float(centre[0]),
                "voxel_center_y_m": float(centre[1]),
                "voxel_center_z_m": float(centre[2]),
                "voxel_min_x_m": float(lower[0]),
                "voxel_min_y_m": float(lower[1]),
                "voxel_min_z_m": float(lower[2]),
                "voxel_max_x_m": float(upper[0]),
                "voxel_max_y_m": float(upper[1]),
                "voxel_max_z_m": float(upper[2]),
                "voxel_volume_m3": grid.voxel_volume_m3,
                "atmospheric_wind_speed_mps": float(wind_row["wind_speed_mps"]),
                "wind_speed_mps": float(wind_row["wind_speed_mps"]),
                "atmospheric_wind_direction_from_deg": float(wind_row["wind_direction_from_deg"]),
                "wind_direction_from_deg": float(wind_row["wind_direction_from_deg"]),
                "wind_east_mps": float(wind_row["wind_u_mps"]),
                "wind_north_mps": float(wind_row["wind_v_mps"]),
                "wind_source": str(wind_row["wind_source"]),
                "pm25_true_ug_m3": true_pm25,
                "coarse_pm_true_ug_m3": true_coarse,
                "pm10_true_ug_m3": true_pm10,
                "pm25_simulated_sensor_ug_m3": reading.pm25_ug_m3,
                "pm10_simulated_sensor_ug_m3": reading.pm10_ug_m3,
                "pm25_sensor_ug_m3": reading.pm25_ug_m3,
                "pm10_sensor_ug_m3": reading.pm10_ug_m3,
                "fine_parcel_count": concentration.fine_parcel_count,
                "coarse_parcel_count": concentration.coarse_parcel_count,
                "fine_mass_g": concentration.fine_mass_g,
                "coarse_mass_g": concentration.coarse_mass_g,
                "scenario_id": resolved.project.scenario_id,
                "quality_flags": flags,
                "data_quality_score": quality_score(flags),
            }
            records.append(record)

        progress_bucket = int(100 * (event_index + 1) / len(events))
        if progress_bucket // 2 != last_progress_bucket:
            last_progress_bucket = progress_bucket // 2
            _notify(
                progress_callback,
                0.10 + 0.72 * (event_index + 1) / len(events),
                f"Replaying trajectory and dispersing parcels ({progress_bucket}%)",
            )

    samples = pd.DataFrame.from_records(records)
    voxel_observations = aggregate_voxel_observations(samples)
    if not voxel_observations.empty:
        lat, lon, _ = transformer.to_geodetic(
            voxel_observations["voxel_center_x_m"].to_numpy(dtype=float),
            voxel_observations["voxel_center_y_m"].to_numpy(dtype=float),
            voxel_observations["voxel_center_z_m"].to_numpy(dtype=float),
        )
        voxel_observations["voxel_center_latitude"] = lat
        voxel_observations["voxel_center_longitude"] = lon
    ground_truth = _ground_truth_frame(grid, dispersion, resolved, end_time_s)
    if not ground_truth.empty:
        lat, lon, _ = transformer.to_geodetic(
            ground_truth["voxel_center_x_m"].to_numpy(dtype=float),
            ground_truth["voxel_center_y_m"].to_numpy(dtype=float),
            ground_truth["voxel_center_z_m"].to_numpy(dtype=float),
        )
        ground_truth["voxel_center_latitude"] = lat
        ground_truth["voxel_center_longitude"] = lon

    source_end = source.emission_end_s if source.emission_end_s is not None else end_time_s
    source_label: dict[str, Any] = {
        "source_name": source.name,
        "source_latitude": resolved.source.latitude,
        "source_longitude": resolved.source.longitude,
        "source_enu_x_m": source.x_m,
        "source_enu_y_m": source.y_m,
        "source_enu_z_m": source.ground_z_m,
        "effective_release_enu_z_m": float(source.release_position_m[2]),
        "ground_elevation_m": source.ground_elevation_m,
        "stack_height_m": source.stack_height_m,
        "effective_release_height_m": source.effective_release_height_m,
        "pm25_emission_rate_g_s": source.pm25_emission_g_s,
        "total_pm10_emission_rate_g_s": source.pm10_total_emission_g_s,
        "coarse_pm_emission_rate_g_s": source.coarse_emission_g_s,
        "emission_start_s": source.emission_start_s,
        "emission_end_s": source_end,
        "preflight_emission_warmup_s": resolved.simulation.warmup_s,
    }
    reference = transformer.reference
    actual_wind_source = (
        str(wind["wind_source"].iloc[0]) if len(wind) else resolved.wind.mode
    )
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "project": resolved.project.name,
        "scenario_id": resolved.project.scenario_id,
        "random_seed": resolved.project.random_seed,
        "flight_source": str(source_path.resolve()),
        "trajectory_provenance": "recorded flight CSV",
        "wind_mode_requested": resolved.wind.mode,
        "wind_provenance": actual_wind_source,
        "wind_source": actual_wind_source,
        "coordinates": {
            "system": "local East-North-Up metres",
            "reference_latitude": reference.latitude,
            "reference_longitude": reference.longitude,
            "reference_altitude_m": reference.altitude_m,
            "flat_ground_assumption": resolved.coordinates.flat_ground,
        },
        "source_location_was_generated": generated_source_location,
        "generated_source_location_assumption": (
            "configured reproducible ENU offset from flight-path bounding-box centre"
            if generated_source_location
            else None
        ),
        "simulation": {
            "start_elapsed_time_s": 0.0,
            "end_elapsed_time_s": end_time_s,
            "timestep_s": resolved.simulation.timestep_s,
            "trajectory_sample_interval_s": resolved.simulation.trajectory_sample_interval_s,
            "warmup_s": resolved.simulation.warmup_s,
            "preflight_wind_policy": resolved.simulation.preflight_wind_policy,
            "spatial_wind_field": "uniform across domain",
        },
        "voxel_grid": {
            "minimum_m": grid.minimum_m.tolist(),
            "maximum_m": grid.maximum_m.tolist(),
            "shape": list(grid.shape),
            "size_m": grid.size_m.tolist(),
            "voxel_volume_m3": grid.voxel_volume_m3,
            "primary_sampling": "exact current voxel",
            "smoothing_enabled": resolved.voxel.smoothing_enabled,
        },
        "scientific_status": "research simulator; not regulatory-grade, calibrated, CFD, or full atmospheric modelling",
        "assumptions_and_limitations": [
            "spatially uniform time-varying wind",
            "flat terrain and takeoff-relative height",
            "no building wake effects",
            "seeded Gaussian random-walk turbulence",
            "prescribed simplified plume rise",
            "simplified settling, deposition, and optional decay",
            "computational mass parcels rather than individual physical particles",
            "configurable demonstration sensor behaviour, not calibrated VoxMaps constants",
        ],
    }
    mass_balance = dispersion.mass_balance_report()
    mass_balance_tolerance_g = 1e-8
    mass_balance_within_tolerance = all(
        abs(float(mass_balance[key])) < mass_balance_tolerance_g
        for key in (
            "numerical_mass_balance_error_fine_g",
            "numerical_mass_balance_error_coarse_g",
            "numerical_mass_balance_error_g",
        )
    )
    run_report: dict[str, Any] = {
        "status": "success",
        "scenario_id": resolved.project.scenario_id,
        "processed_sample_count": int(len(samples)),
        "normalized_trajectory_row_count": int(len(normalized_enu)),
        "visited_voxel_count": int(samples["voxel_id"].nunique(dropna=True)),
        "final_occupied_voxel_count": int(len(ground_truth)),
        "final_particle_count": int(len(dispersion.particles)),
        "wind_mode_requested": resolved.wind.mode,
        "wind_source_used": actual_wind_source,
        "measured_wind_availability": availability.to_dict(),
        "missing_wind_physics_step_count": missing_wind_steps,
        "input_invalid_row_count": ingestion.report["trajectory"]["invalid_row_count"],
        "synthetic_wind": actual_wind_source == "synthetic",
        "mass_balance_tolerance_g": mass_balance_tolerance_g,
        "mass_balance_within_tolerance": mass_balance_within_tolerance,
        "warnings": list(ingestion.report.get("warnings", [])),
    }
    if missing_wind_steps:
        run_report["warnings"].append(
            "Measured-wind gaps exceeding the interpolation limit were explicitly flagged "
            "and advanced with zero horizontal advection; no synthetic wind was substituted."
        )

    _notify(progress_callback, 0.86, "Writing VoxMaps maps, labels, reports, and visualisation")
    artifacts = export_run(
        destination,
        samples,
        voxel_observations,
        normalized_flight=normalized_enu,
        ground_truth_voxels=ground_truth,
        source_label=source_label,
        scenario_metadata=metadata,
        mass_balance=mass_balance,
        run_report=run_report,
        data_quality_report=ingestion.report,
        column_mapping=ingestion.mapping,
        resolved_config=resolved,
        parquet_compression=resolved.output.parquet_compression,
        write_visualization=write_visualization,
    )
    _notify(progress_callback, 1.0, "Simulation complete")
    LOGGER.info(
        "scenario_complete scenario_id=%s samples=%d particles=%d wind=%s",
        resolved.project.scenario_id,
        len(samples),
        len(dispersion.particles),
        resolved.wind.mode,
    )
    return RunResult(
        samples,
        voxel_observations,
        ground_truth,
        normalized_enu,
        source_label,
        metadata,
        mass_balance,
        run_report,
        artifacts,
        resolved,
    )


# Compatibility name used by integrations and the Streamlit UI.
run_pipeline = run_simulation


__all__ = ["ProgressCallback", "RunResult", "resample_trajectory", "run_pipeline", "run_simulation"]
