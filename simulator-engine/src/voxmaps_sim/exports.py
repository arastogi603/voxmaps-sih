"""VoxMaps-facing aggregation and export adapter.

The simulator deliberately keeps this module at the edge of the system.  Core
physics code returns ordinary data frames and dictionaries; every assumption
about filenames and the external VoxMaps schema lives here so that it can be
changed without touching the dispersion engine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
import json
import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)


def _first_column(frame: pd.DataFrame, names: Sequence[str]) -> str | None:
    """Return the first matching column name, ignoring case."""

    lookup = {str(column).casefold(): str(column) for column in frame.columns}
    for name in names:
        if name in frame.columns:
            return name
        match = lookup.get(name.casefold())
        if match is not None:
            return match
    return None


def _jsonable(value: Any) -> Any:
    """Convert pandas, NumPy, Pydantic, and path values to strict JSON values."""

    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, np.ndarray, pd.Series)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(payload), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path


def _quality_is_clean(value: Any) -> bool:
    if value is None or value is pd.NA:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, (list, tuple, set)):
        return len(value) == 0
    text = str(value).strip().casefold()
    return text in {"", "[]", "none", "nan", "ok", "valid"}


def _numeric_stats(values: pd.Series, prefix: str) -> dict[str, float | int | None]:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return {
            f"{prefix}_observation_count": 0,
            f"{prefix}_mean": None,
            f"{prefix}_median": None,
            f"{prefix}_minimum": None,
            f"{prefix}_maximum": None,
            f"{prefix}_standard_deviation": None,
            f"{prefix}_percentile_95": None,
        }
    return {
        f"{prefix}_observation_count": int(numeric.size),
        f"{prefix}_mean": float(numeric.mean()),
        f"{prefix}_median": float(numeric.median()),
        f"{prefix}_minimum": float(numeric.min()),
        f"{prefix}_maximum": float(numeric.max()),
        f"{prefix}_standard_deviation": float(numeric.std(ddof=0)),
        f"{prefix}_percentile_95": float(numeric.quantile(0.95)),
    }


_VALUE_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "pm25_true_ug_m3",
        ("pm25_true_ug_m3", "true_pm25_ug_m3", "pm25_true", "pm25_ground_truth_ug_m3"),
    ),
    (
        "coarse_pm_true_ug_m3",
        ("coarse_pm_true_ug_m3", "true_coarse_pm_ug_m3", "coarse_true_ug_m3"),
    ),
    (
        "pm10_true_ug_m3",
        ("pm10_true_ug_m3", "true_pm10_ug_m3", "pm10_true", "pm10_ground_truth_ug_m3"),
    ),
    (
        "pm25_sensor_ug_m3",
        (
            "pm25_sensor_ug_m3",
            "pm25_simulated_sensor_ug_m3",
            "simulated_pm25_ug_m3",
            "pm25_measured_ug_m3",
        ),
    ),
    (
        "pm10_sensor_ug_m3",
        (
            "pm10_sensor_ug_m3",
            "pm10_simulated_sensor_ug_m3",
            "simulated_pm10_ug_m3",
            "pm10_measured_ug_m3",
        ),
    ),
)


def aggregate_voxel_observations(samples: pd.DataFrame) -> pd.DataFrame:
    """Aggregate repeated drone observations by stable 3-D voxel identifier.

    The output is intentionally wide: each concentration channel gets its own
    descriptive statistics.  PM2.5 and total PM10 are always represented, and
    coarse PM plus virtual-sensor values are included when present.
    """

    if samples.empty:
        return pd.DataFrame(columns=["voxel_id", "observation_count"])
    frame = samples.copy()
    voxel_col = _first_column(frame, ("voxel_id", "current_voxel_id", "voxel"))
    if voxel_col is None:
        index_columns = [
            _first_column(frame, ("voxel_x_index", "voxel_ix", "ix")),
            _first_column(frame, ("voxel_y_index", "voxel_iy", "iy")),
            _first_column(frame, ("voxel_z_index", "voxel_iz", "iz")),
        ]
        if any(column is None for column in index_columns):
            raise ValueError(
                "Drone samples need voxel_id/current_voxel_id or all three voxel index columns."
            )
        frame["voxel_id"] = (
            frame[index_columns[0]].astype("Int64").astype(str)
            + ":"
            + frame[index_columns[1]].astype("Int64").astype(str)
            + ":"
            + frame[index_columns[2]].astype("Int64").astype(str)
        )
        voxel_col = "voxel_id"

    # Out-of-domain samples remain present in drone_sensor_samples.csv with an
    # explicit quality flag, but they do not represent an observable voxel.
    frame = frame.loc[frame[voxel_col].notna()].copy()
    if frame.empty:
        return pd.DataFrame(columns=["voxel_id", "observation_count"])

    time_col = _first_column(
        frame, ("timestamp", "datetime_utc", "datetime(utc)", "original_timestamp", "elapsed_time_s")
    )
    altitude_col = _first_column(frame, ("altitude_m", "enu_z_m", "z_m", "height_m"))
    quality_col = _first_column(frame, ("quality_flags", "data_quality_flags"))
    quality_score_col = _first_column(frame, ("data_quality_score", "quality_score"))

    carry_mean: dict[str, str] = {}
    for output_name, candidates in (
        ("voxel_center_x_m", ("voxel_center_x_m", "center_x_m", "enu_x_m")),
        ("voxel_center_y_m", ("voxel_center_y_m", "center_y_m", "enu_y_m")),
        ("voxel_center_z_m", ("voxel_center_z_m", "center_z_m", "enu_z_m")),
        ("mean_latitude", ("latitude", "lat")),
        ("mean_longitude", ("longitude", "lon", "lng")),
    ):
        column = _first_column(frame, candidates)
        if column is not None:
            carry_mean[output_name] = column

    carry_first: dict[str, str] = {}
    for output_name, candidates in (
        ("voxel_x_index", ("voxel_x_index", "voxel_ix", "ix")),
        ("voxel_y_index", ("voxel_y_index", "voxel_iy", "iy")),
        ("voxel_z_index", ("voxel_z_index", "voxel_iz", "iz")),
        ("scenario_id", ("scenario_id",)),
    ):
        column = _first_column(frame, candidates)
        if column is not None:
            carry_first[output_name] = column

    value_columns = [
        (prefix, column)
        for prefix, aliases in _VALUE_COLUMNS
        if (column := _first_column(frame, aliases)) is not None
    ]

    records: list[dict[str, Any]] = []
    for voxel_id, group in frame.groupby(voxel_col, sort=True, dropna=False):
        record: dict[str, Any] = {
            "voxel_id": voxel_id,
            "observation_count": int(len(group)),
        }
        if time_col is not None:
            valid_time = group[time_col].dropna()
            record["first_observation_time"] = valid_time.iloc[0] if not valid_time.empty else None
            record["last_observation_time"] = valid_time.iloc[-1] if not valid_time.empty else None
        if altitude_col is not None:
            altitude = pd.to_numeric(group[altitude_col], errors="coerce")
            record["mean_altitude_m"] = float(altitude.mean()) if altitude.notna().any() else None
        for output_name, column in carry_mean.items():
            values = pd.to_numeric(group[column], errors="coerce")
            record[output_name] = float(values.mean()) if values.notna().any() else None
        for output_name, column in carry_first.items():
            valid = group[column].dropna()
            record[output_name] = valid.iloc[0] if not valid.empty else None
        for prefix, column in value_columns:
            record.update(_numeric_stats(group[column], prefix))

        if quality_score_col is not None:
            scores = pd.to_numeric(group[quality_score_col], errors="coerce")
            data_quality_score = float(scores.mean()) if scores.notna().any() else 0.0
        elif quality_col is None:
            data_quality_score = 1.0
        else:
            try:
                from .quality import quality_score

                data_quality_score = float(group[quality_col].map(quality_score).mean())
            except ImportError:  # pragma: no cover - only for standalone adapter reuse
                data_quality_score = float(group[quality_col].map(_quality_is_clean).mean())
        # More revisits increase confidence, with diminishing returns.  Quality
        # is never hidden by a large observation count.
        count_factor = math.sqrt(len(group) / (len(group) + 4.0))
        record["data_quality_score"] = data_quality_score
        record["confidence_score"] = data_quality_score * count_factor
        records.append(record)

    return pd.DataFrame.from_records(records)


def _extract_reference(metadata: Mapping[str, Any] | None) -> tuple[float, float] | None:
    if not metadata:
        return None
    candidates: list[Mapping[str, Any]] = [metadata]
    for key in ("coordinates", "coordinate_reference", "enu_reference", "reference"):
        nested = metadata.get(key)
        if isinstance(nested, Mapping):
            candidates.append(nested)
    for candidate in candidates:
        latitude = next(
            (candidate[key] for key in ("reference_latitude", "latitude", "lat", "latitude_deg") if key in candidate),
            None,
        )
        longitude = next(
            (
                candidate[key]
                for key in ("reference_longitude", "longitude", "lon", "longitude_deg")
                if key in candidate
            ),
            None,
        )
        try:
            if latitude is not None and longitude is not None:
                return float(latitude), float(longitude)
        except (TypeError, ValueError):
            continue
    return None


def _make_inverse_enu_transform(
    metadata: Mapping[str, Any] | None,
) -> Callable[[float, float], tuple[float, float]] | None:
    reference = _extract_reference(metadata)
    if reference is None:
        return None
    try:
        from pyproj import CRS, Transformer

        latitude, longitude = reference
        local_crs = CRS.from_proj4(
            f"+proj=aeqd +lat_0={latitude:.12f} +lon_0={longitude:.12f} +datum=WGS84 +units=m +no_defs"
        )
        transformer = Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True)
        return lambda east, north: transformer.transform(east, north)
    except Exception:  # pragma: no cover - only reached without working PyProj
        LOGGER.exception("Could not create ENU-to-geographic transform")
        return None


def _frame_coordinates(
    frame: pd.DataFrame,
    metadata: Mapping[str, Any] | None,
    *,
    centre: bool,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    lon_col = _first_column(
        frame,
        (
            "voxel_center_longitude" if centre else "longitude",
            "center_longitude",
            "mean_longitude",
            "longitude",
            "lon",
        ),
    )
    lat_col = _first_column(
        frame,
        (
            "voxel_center_latitude" if centre else "latitude",
            "center_latitude",
            "mean_latitude",
            "latitude",
            "lat",
        ),
    )
    z_col = _first_column(
        frame,
        (
            "voxel_center_z_m" if centre else "altitude_m",
            "center_z_m",
            "enu_z_m",
            "z_m",
            "altitude_m",
        ),
    )
    if lon_col is not None and lat_col is not None:
        longitude = pd.to_numeric(frame[lon_col], errors="coerce")
        latitude = pd.to_numeric(frame[lat_col], errors="coerce")
    else:
        x_col = _first_column(
            frame,
            (
                "voxel_center_x_m" if centre else "enu_x_m",
                "center_x_m",
                "enu_x_m",
                "x_m",
            ),
        )
        y_col = _first_column(
            frame,
            (
                "voxel_center_y_m" if centre else "enu_y_m",
                "center_y_m",
                "enu_y_m",
                "y_m",
            ),
        )
        inverse = _make_inverse_enu_transform(metadata)
        longitude = pd.Series(np.nan, index=frame.index, dtype=float)
        latitude = pd.Series(np.nan, index=frame.index, dtype=float)
        if x_col is not None and y_col is not None and inverse is not None:
            x_values = pd.to_numeric(frame[x_col], errors="coerce")
            y_values = pd.to_numeric(frame[y_col], errors="coerce")
            valid = x_values.notna() & y_values.notna()
            converted = [inverse(float(x), float(y)) for x, y in zip(x_values[valid], y_values[valid])]
            if converted:
                longitude.loc[valid] = [item[0] for item in converted]
                latitude.loc[valid] = [item[1] for item in converted]
    altitude = (
        pd.to_numeric(frame[z_col], errors="coerce")
        if z_col is not None
        else pd.Series(0.0, index=frame.index, dtype=float)
    )
    return longitude, latitude, altitude


def voxel_map_feature_collection(
    voxel_observations: pd.DataFrame,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a WGS84 GeoJSON FeatureCollection of observed voxel centres."""

    longitude, latitude, altitude = _frame_coordinates(voxel_observations, metadata, centre=True)
    features: list[dict[str, Any]] = []
    for position, (_, row) in enumerate(voxel_observations.iterrows()):
        lon = longitude.iloc[position]
        lat = latitude.iloc[position]
        if not (np.isfinite(lon) and np.isfinite(lat)):
            continue
        properties = {str(key): _jsonable(value) for key, value in row.items()}
        properties["coordinate_semantics"] = "WGS84 voxel centre; altitude is local ENU metres"
        features.append(
            {
                "type": "Feature",
                "id": str(properties.get("voxel_id", position)),
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        float(lon),
                        float(lat),
                        float(altitude.iloc[position])
                        if np.isfinite(altitude.iloc[position])
                        else 0.0,
                    ],
                },
                "properties": properties,
            }
        )
    return {
        "type": "FeatureCollection",
        "name": "voxmaps_voxel_observations",
        "features": features,
    }


def flight_path_feature_collection(
    samples: pd.DataFrame,
    metadata: Mapping[str, Any] | None = None,
    source_label: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a WGS84 flight-path LineString and optional known-source marker."""

    longitude, latitude, altitude = _frame_coordinates(samples, metadata, centre=False)
    valid = longitude.notna() & latitude.notna()
    coordinates = [
        [float(lon), float(lat), float(z) if np.isfinite(z) else 0.0]
        for lon, lat, z in zip(longitude[valid], latitude[valid], altitude[valid])
    ]
    features: list[dict[str, Any]] = []
    if coordinates:
        features.append(
            {
                "type": "Feature",
                "id": "recorded-drone-flight-path",
                "geometry": {"type": "LineString", "coordinates": coordinates},
                "properties": {
                    "name": "recorded drone flight path",
                    "point_count": len(coordinates),
                    "altitude_units": "metres in local ENU frame",
                },
            }
        )

    if source_label:
        latitude_value = next(
            (source_label[key] for key in ("source_latitude", "latitude", "latitude_deg") if key in source_label),
            None,
        )
        longitude_value = next(
            (
                source_label[key]
                for key in ("source_longitude", "longitude", "longitude_deg")
                if key in source_label
            ),
            None,
        )
        if latitude_value is not None and longitude_value is not None:
            ground = float(source_label.get("ground_elevation_m", 0.0) or 0.0)
            features.append(
                {
                    "type": "Feature",
                    "id": "known-source",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [float(longitude_value), float(latitude_value), ground],
                    },
                    "properties": {"role": "supervised ground-truth source", **_jsonable(source_label)},
                }
            )
    return {"type": "FeatureCollection", "name": "voxmaps_flight_path", "features": features}


def _write_parquet_with_fallback(
    frame: pd.DataFrame,
    path: Path,
    *,
    compression: str = "snappy",
) -> tuple[Path | None, Path | None, str | None]:
    """Write genuine Parquet or an explicitly named CSV fallback.

    A CSV is never written with a ``.parquet`` suffix.  When PyArrow (or another
    pandas Parquet engine) is unavailable the fallback path and reason are
    surfaced in ``run_report.json`` and the returned manifest.
    """

    try:
        frame.to_parquet(path, index=False, compression=compression)
        return path, None, None
    except (ImportError, ModuleNotFoundError) as exc:
        fallback = path.with_suffix(".csv")
        frame.to_csv(fallback, index=False)
        reason = f"Parquet engine unavailable: {exc}"
        LOGGER.warning("%s; wrote %s", reason, fallback)
        return None, fallback, reason


class VoxMapsExportAdapter:
    """Own the complete on-disk schema for a single simulator run."""

    def __init__(self, output_dir: str | Path, *, parquet_compression: str = "snappy") -> None:
        self.output_dir = Path(output_dir)
        self.parquet_compression = parquet_compression

    def export(
        self,
        samples: pd.DataFrame,
        voxel_observations: pd.DataFrame | None = None,
        *,
        normalized_flight: pd.DataFrame | None = None,
        ground_truth_voxels: pd.DataFrame | None = None,
        source_label: Mapping[str, Any] | None = None,
        scenario_metadata: Mapping[str, Any] | None = None,
        mass_balance: Mapping[str, Any] | None = None,
        run_report: Mapping[str, Any] | None = None,
        data_quality_report: Mapping[str, Any] | None = None,
        column_mapping: Mapping[str, Any] | None = None,
        resolved_config: Mapping[str, Any] | Any | None = None,
        write_visualization: bool = True,
    ) -> dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        samples = pd.DataFrame(samples).copy()
        if voxel_observations is None:
            voxel_observations = aggregate_voxel_observations(samples)
        else:
            voxel_observations = pd.DataFrame(voxel_observations).copy()
        if ground_truth_voxels is None:
            ground_truth_voxels = voxel_observations
        else:
            ground_truth_voxels = pd.DataFrame(ground_truth_voxels).copy()

        metadata = dict(_jsonable(scenario_metadata or {}))
        label = dict(_jsonable(source_label or {}))
        report = dict(_jsonable(run_report or {}))
        mass = dict(_jsonable(mass_balance or {}))
        quality = dict(_jsonable(data_quality_report or {}))
        mapping = dict(_jsonable(column_mapping or {}))

        paths: dict[str, Path] = {}
        paths["drone_sensor_samples"] = self.output_dir / "drone_sensor_samples.csv"
        samples.to_csv(paths["drone_sensor_samples"], index=False)
        paths["voxel_observations"] = self.output_dir / "voxel_observations.csv"
        voxel_observations.to_csv(paths["voxel_observations"], index=False)
        if normalized_flight is not None:
            paths["normalized_flight"] = self.output_dir / "normalized_flight.csv"
            pd.DataFrame(normalized_flight).to_csv(paths["normalized_flight"], index=False)

        paths["voxel_map"] = _write_json(
            self.output_dir / "voxel_map.geojson",
            voxel_map_feature_collection(voxel_observations, metadata),
        )
        flight_frame = normalized_flight if normalized_flight is not None else samples
        paths["flight_path"] = _write_json(
            self.output_dir / "flight_path.geojson",
            flight_path_feature_collection(pd.DataFrame(flight_frame), metadata, label),
        )

        parquet_path, fallback_path, fallback_reason = _write_parquet_with_fallback(
            ground_truth_voxels,
            self.output_dir / "ground_truth_voxels.parquet",
            compression=self.parquet_compression,
        )
        if parquet_path is not None:
            paths["ground_truth_voxels"] = parquet_path
            report["ground_truth_voxel_storage"] = "parquet"
        else:
            assert fallback_path is not None
            paths["ground_truth_voxels_csv_fallback"] = fallback_path
            report["ground_truth_voxel_storage"] = "csv_fallback"
            report.setdefault("warnings", []).append(fallback_reason)

        json_artifacts = {
            "source_label": ("source_label.json", label),
            "scenario_metadata": ("scenario_metadata.json", metadata),
            "mass_balance": ("mass_balance.json", mass),
            "run_report": ("run_report.json", report),
            "data_quality_report": ("data_quality_report.json", quality),
            "column_mapping": ("column_mapping.json", mapping),
        }
        for key, (filename, payload) in json_artifacts.items():
            paths[key] = _write_json(self.output_dir / filename, payload)

        if resolved_config is not None:
            paths["resolved_scenario_config"] = _write_json(
                self.output_dir / "resolved_scenario_config.json", resolved_config
            )

        if write_visualization:
            try:
                from .visualization import create_visualization

                paths["visualization"] = create_visualization(
                    self.output_dir,
                    samples=samples,
                    voxel_observations=voxel_observations,
                    source_label=label,
                    output_path=self.output_dir / "visualization.html",
                )
            except Exception as exc:
                # Exported scientific data remain useful if a rendering backend
                # fails, but the failure is explicit and actionable.
                LOGGER.exception("Could not create visualization.html")
                report.setdefault("errors", []).append(f"Visualization failed: {exc}")
                paths["run_report"] = _write_json(self.output_dir / "run_report.json", report)
                raise

        manifest_payload = {
            key: str(path.relative_to(self.output_dir)) for key, path in sorted(paths.items())
        }
        paths["artifact_manifest"] = _write_json(
            self.output_dir / "artifact_manifest.json", manifest_payload
        )
        return paths


# Shorter alias retained for callers that prefer ``VoxMapsExporter``.
VoxMapsExporter = VoxMapsExportAdapter


def export_run(
    output_dir: str | Path,
    samples: pd.DataFrame | None = None,
    voxel_observations: pd.DataFrame | None = None,
    *,
    normalized_flight: pd.DataFrame | None = None,
    ground_truth_voxels: pd.DataFrame | None = None,
    source_label: Mapping[str, Any] | None = None,
    scenario_metadata: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    mass_balance: Mapping[str, Any] | None = None,
    run_report: Mapping[str, Any] | None = None,
    data_quality_report: Mapping[str, Any] | None = None,
    column_mapping: Mapping[str, Any] | None = None,
    resolved_config: Mapping[str, Any] | Any | None = None,
    parquet_compression: str = "snappy",
    write_visualization: bool = True,
    **aliases: Any,
) -> dict[str, Path]:
    """Compatibility wrapper used by the pipeline and external integrations.

    Common aliases (``drone_samples``, ``voxel_map``, ``trajectory``, and
    ``particle_ground_truth``) are accepted to keep the core pipeline decoupled
    from this module's naming choices.
    """

    if samples is None:
        samples = aliases.pop("drone_samples", aliases.pop("sensor_samples", None))
    if samples is None:
        raise TypeError("export_run requires a drone sensor samples DataFrame")
    if voxel_observations is None:
        voxel_observations = aliases.pop("voxel_map", aliases.pop("aggregated_voxels", None))
    if normalized_flight is None:
        normalized_flight = aliases.pop("trajectory", aliases.pop("flight_path", None))
    if ground_truth_voxels is None:
        ground_truth_voxels = aliases.pop(
            "particle_ground_truth", aliases.pop("ground_truth", None)
        )
    if aliases:
        LOGGER.debug("Ignoring unrecognised export metadata keys: %s", sorted(aliases))
    combined_metadata = scenario_metadata if scenario_metadata is not None else metadata
    adapter = VoxMapsExportAdapter(output_dir, parquet_compression=parquet_compression)
    return adapter.export(
        pd.DataFrame(samples),
        None if voxel_observations is None else pd.DataFrame(voxel_observations),
        normalized_flight=normalized_flight,
        ground_truth_voxels=ground_truth_voxels,
        source_label=source_label,
        scenario_metadata=combined_metadata,
        mass_balance=mass_balance,
        run_report=run_report,
        data_quality_report=data_quality_report,
        column_mapping=column_mapping,
        resolved_config=resolved_config,
        write_visualization=write_visualization,
    )


__all__ = [
    "VoxMapsExportAdapter",
    "VoxMapsExporter",
    "aggregate_voxel_observations",
    "export_run",
    "flight_path_feature_collection",
    "voxel_map_feature_collection",
]
