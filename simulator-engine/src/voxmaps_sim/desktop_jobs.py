"""Immutable upload storage and cancellable background jobs for the local GUI."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable

import numpy as np
import pandas as pd

from .config import SimulationConfig
from .desktop_models import (
    DesktopScenarioConfig,
    JobCreateRequest,
    JobProgress,
    JobState,
    TERMINAL_JOB_STATES,
)
from .ingestion import (
    CANONICAL_CANDIDATES,
    FlightDataError,
    FlightDataResult,
    SUPPORTED_FLIGHT_SUFFIXES,
    load_flight_csv,
    read_flight_table,
    resolve_column_mapping,
)


MAX_UPLOAD_BYTES = 500 * 1024 * 1024
LOGGER = logging.getLogger(__name__)

STAGES: tuple[tuple[str, str], ...] = (
    ("validating_csv", "Validating flight file"),
    ("preparing_coordinates", "Preparing coordinate system"),
    ("preparing_wind", "Preparing wind timeline"),
    ("transporting_parcels", "Emitting and transporting parcels"),
    ("sampling_sensor", "Sampling the drone sensor"),
    ("generating_playback", "Generating playback data"),
    ("validating_outputs", "Validating outputs"),
    ("writing_outputs", "Writing output files"),
)


class DesktopServiceError(RuntimeError):
    """Base class for errors safe to present through the local API."""


class UploadNotFoundError(DesktopServiceError):
    pass


class JobNotFoundError(DesktopServiceError):
    pass


class JobConflictError(DesktopServiceError):
    pass


class OutputContractError(DesktopServiceError):
    pass


class JobCancelled(DesktopServiceError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def _filesystem_path(path: Path) -> Path:
    """Use Windows' extended path namespace for deeply nested scenario names."""

    resolved = str(path.resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return Path(resolved)
    if resolved.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + resolved.lstrip("\\"))
    return Path("\\\\?\\" + resolved)


def _iso(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return parsed.isoformat().replace("+00:00", "Z")


def _header_key(value: str) -> str:
    return "".join(character for character in value.strip().casefold() if character.isalnum())


def _column_dispositions(
    columns: list[str], mapping: dict[str, str | None]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Explain which source fields are useful without silently dropping the rest."""

    canonical_by_source = {
        source: canonical for canonical, source in mapping.items() if source is not None
    }
    retained: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    for column in columns:
        key = _header_key(column)
        canonical = canonical_by_source.get(column)
        if canonical is not None:
            reason = f"mapped to the normalized {canonical.replace('_', ' ')} field"
            retained.append(
                {
                    "column": column,
                    "normalized_name": canonical,
                    "category": "simulation_input",
                    "reason": reason,
                }
            )
            continue

        if key.startswith("max") or any(
            token in key for token in ("maximum", "cumulative", "totaldistance")
        ):
            category, reason = "summary", "maximum or cumulative summary field"
        elif key.startswith(("rc", "joystick", "stick")) or any(
            token in key for token in ("throttleinput", "rudderinput", "aileroninput")
        ):
            category, reason = "control_input", "remote-controller input is unrelated to sensor interpretation"
        elif "gimbal" in key:
            category, reason = "gimbal", "gimbal control or orientation is unrelated to the plume calculation"
        elif any(token in key for token in ("photo", "video", "camera")):
            category, reason = "media", "photo/video status is unrelated to the clean sensor log"
        elif "battery" in key and any(
            token in key for token in ("cell", "current", "temperature", "voltage")
        ):
            category, reason = "battery", "individual battery telemetry is excluded from the clean sensor log"
        elif "motor" in key and any(token in key for token in ("current", "temperature", "rpm")):
            category, reason = "motor", "motor current, temperature, or RPM is unrelated telemetry"
        elif "terrain" in key:
            category, reason = "terrain", "terrain field is not usable as a reliable elevation datum"
        elif "flycstateraw" in key or any(
            token in key for token in ("message", "warning", "errorcode", "rawcode")
        ):
            category, reason = "status", "routine message or raw controller code"
        elif any(
            token in key
            for token in (
                "altitudeabovesealevel",
                "altitudemsl",
                "heightabovetakeoff",
                "speedmph",
                "groundspeed",
                "verticalspeed",
                "zspeed",
                "compassheading",
                "headingdegrees",
                "pitchdegrees",
                "rolldegrees",
                "satellite",
                "gpssignal",
                "gpsquality",
                "flightmode",
                "flightstate",
                "flycstate",
                "isflying",
            )
        ):
            retained.append(
                {
                    "column": column,
                    "normalized_name": "",
                    "category": "flight_context",
                    "reason": "retained when available for geospatial, flight-quality, or sensor context",
                }
            )
            continue
        else:
            category, reason = "unrelated_telemetry", "not required for simulation, time alignment, or clean sensor interpretation"
        excluded.append(
            {
                "column": column,
                "normalized_name": "",
                "category": category,
                "reason": reason,
            }
        )
    return retained, excluded


def _preview_points(flight: pd.DataFrame, maximum_points: int = 600) -> list[dict[str, Any]]:
    if flight.empty:
        return []
    count = min(maximum_points, len(flight))
    indices = np.unique(np.linspace(0, len(flight) - 1, count, dtype=int))
    preview = flight.iloc[indices]
    return [
        {
            "latitude_deg": float(row.latitude),
            "longitude_deg": float(row.longitude),
            "altitude_m": float(row.altitude_m),
            "elapsed_time_s": float(row.elapsed_time_s),
        }
        for row in preview.itertuples(index=False)
    ]


def _partial_mapping(columns: list[str]) -> dict[str, str | None]:
    try:
        return resolve_column_mapping(columns)
    except FlightDataError:
        resolved: dict[str, str | None] = {
            canonical: None for canonical in CANONICAL_CANDIDATES
        }
        normalized = {_header_key(column): column for column in columns}
        for canonical, candidates in CANONICAL_CANDIDATES.items():
            matches = {
                normalized[_header_key(candidate)]
                for candidate in candidates
                if _header_key(candidate) in normalized
            }
            if len(matches) == 1:
                resolved[canonical] = matches.pop()
        return resolved


def inspect_flight_copy(
    path: Path,
    *,
    source_filename: str,
    checksum_sha256: str,
    mapping: dict[str, str] | None = None,
    min_wind_coverage: float = 0.0,
) -> tuple[dict[str, Any], FlightDataResult | None]:
    """Return UI-friendly facts and, when valid, the normalized flight result."""

    raw = read_flight_table(path)
    columns = [str(column) for column in raw.columns]
    validation_errors: list[str] = []
    result: FlightDataResult | None = None
    try:
        result = load_flight_csv(
            path,
            mapping=mapping,
            min_wind_coverage=min_wind_coverage,
        )
        resolved = result.mapping
    except FlightDataError as exc:
        validation_errors.append(str(exc))
        resolved = _partial_mapping(columns)

    retained, excluded = _column_dispositions(columns, resolved)
    if result is None:
        inspection = {
            "filename": source_filename,
            "source_filename": source_filename,
            "checksum_sha256": checksum_sha256,
            "size_bytes": int(path.stat().st_size),
            "row_count": int(len(raw)),
            "usable_row_count": 0,
            "duration_s": 0.0,
            "first_utc": None,
            "last_utc": None,
            "gps_extent": {
                "min_latitude": None,
                "max_latitude": None,
                "min_longitude": None,
                "max_longitude": None,
            },
            "altitude_range_m": {"min": None, "max": None},
            "detected_columns": resolved,
            "wind_columns": {
                "speed": resolved.get("wind_speed"),
                "direction": resolved.get("wind_direction"),
            },
            "valid_paired_wind_rows": 0,
            "valid_paired_wind_coverage": 0.0,
            "sampling_frequency_hz": None,
            "warnings": validation_errors,
            "validation_errors": validation_errors,
            "preview_points": [],
            "retained_columns": retained,
            "excluded_columns": excluded,
            "requires_mapping": True,
            "available_columns": columns,
            "mapping": resolved,
            "valid": False,
        }
        return inspection, None

    flight = result.normalized_flight
    timestamps = flight["timestamp_utc"].dropna()
    elapsed = pd.to_numeric(flight["elapsed_time_s"], errors="coerce").dropna()
    positive_steps = elapsed.diff().dropna()
    positive_steps = positive_steps[positive_steps > 0.0]
    sampling_frequency = (
        float(1.0 / positive_steps.median()) if len(positive_steps) else None
    )
    wind = result.report["wind"]
    warnings = [str(item) for item in result.report.get("warnings", [])]
    if not len(timestamps):
        warnings.append("No parseable UTC timestamp was detected; relative uptime will be used.")
    inspection = {
        "filename": source_filename,
        "source_filename": source_filename,
        "checksum_sha256": checksum_sha256,
        "size_bytes": int(path.stat().st_size),
        "row_count": int(result.report["input"]["row_count"]),
        "usable_row_count": int(len(flight)),
        "duration_s": float(elapsed.max() - elapsed.min()) if len(elapsed) else 0.0,
        "first_utc": _iso(timestamps.min()) if len(timestamps) else None,
        "last_utc": _iso(timestamps.max()) if len(timestamps) else None,
        "gps_extent": {
            "min_latitude": float(flight["latitude"].min()),
            "max_latitude": float(flight["latitude"].max()),
            "min_longitude": float(flight["longitude"].min()),
            "max_longitude": float(flight["longitude"].max()),
        },
        "altitude_range_m": {
            "min": float(flight["altitude_m"].min()),
            "max": float(flight["altitude_m"].max()),
        },
        "detected_columns": resolved,
        "wind_columns": {
            "speed": resolved.get("wind_speed"),
            "direction": resolved.get("wind_direction"),
        },
        "valid_paired_wind_rows": int(wind["paired_numeric_count"]),
        "valid_paired_wind_coverage": float(wind["paired_numeric_coverage"]),
        "sfd_original_wind_rows": int(wind.get("sfd_original_row_count", 0)),
        "sfd_generated_wind_rows": int(
            wind.get("sfd_generated_or_interpolated_row_count", 0)
        ),
        "wind_source_counts": dict(wind.get("sfd_source_counts", {})),
        "input_format": result.report["source_file"].get("input_format", "airdata_csv"),
        "worksheet": result.report["source_file"].get("worksheet"),
        "measured_wind_usable": bool(wind["measured_wind_usable"]),
        "maximum_bracketed_wind_gap_s": wind.get(
            "maximum_bracketed_observation_gap_s"
        ),
        "sampling_frequency_hz": sampling_frequency,
        "warnings": warnings,
        "validation_errors": [],
        "preview_points": _preview_points(flight),
        "retained_columns": retained,
        "excluded_columns": excluded,
        "requires_mapping": False,
        "available_columns": columns,
        "mapping": resolved,
        "valid": True,
    }
    return inspection, result


@dataclass(slots=True)
class UploadRecord:
    upload_id: str
    path: Path
    source_filename: str
    checksum_sha256: str
    size_bytes: int
    inspection: dict[str, Any]
    normalized_result: FlightDataResult | None
    mapping: dict[str, str] = field(default_factory=dict)
    min_wind_coverage: float = 0.0
    created_at: datetime = field(default_factory=utc_now)


class UploadStore:
    """Private immutable copies of browser uploads.

    The copies live outside scenario output folders and are checksum-verified
    again before a simulation starts.  Neither the user's original file nor
    the private copy is ever opened for writing after upload publication.
    """

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, UploadRecord] = {}
        self._lock = threading.RLock()

    def create(self, filename: str, stream: BinaryIO) -> UploadRecord:
        safe_name = Path(filename or "flight.csv").name
        if Path(safe_name).suffix.casefold() not in SUPPORTED_FLIGHT_SUFFIXES:
            raise FlightDataError("flight upload must have a .csv or SFD .xlsx filename")
        upload_id = uuid.uuid4().hex
        upload_dir = self.root / upload_id
        filesystem_upload_dir = _filesystem_path(upload_dir)
        filesystem_upload_dir.mkdir(parents=False, exist_ok=False)
        partial = upload_dir / f".{safe_name}.partial"
        published = upload_dir / safe_name
        filesystem_partial = _filesystem_path(partial)
        filesystem_published = _filesystem_path(published)
        digest = hashlib.sha256()
        size = 0
        try:
            with filesystem_partial.open("xb") as target:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise FlightDataError("flight upload exceeds the 500 MB local limit")
                    digest.update(chunk)
                    target.write(chunk)
            if size == 0:
                raise FlightDataError("uploaded flight file is empty")
            os.replace(filesystem_partial, filesystem_published)
            checksum = digest.hexdigest()
            inspection, normalized = inspect_flight_copy(
                filesystem_published,
                source_filename=safe_name,
                checksum_sha256=checksum,
            )
            record = UploadRecord(
                upload_id=upload_id,
                path=published,
                source_filename=safe_name,
                checksum_sha256=checksum,
                size_bytes=size,
                inspection=inspection,
                normalized_result=normalized,
            )
            with self._lock:
                self._records[upload_id] = record
            return record
        except Exception:
            shutil.rmtree(filesystem_upload_dir, ignore_errors=True)
            raise

    def get(self, upload_id: str) -> UploadRecord:
        with self._lock:
            record = self._records.get(upload_id)
        if record is None:
            raise UploadNotFoundError(f"unknown upload: {upload_id}")
        return record

    def remap(
        self,
        upload_id: str,
        mapping: dict[str, str],
        min_wind_coverage: float,
    ) -> UploadRecord:
        record = self.get(upload_id)
        inspection, normalized = inspect_flight_copy(
            _filesystem_path(record.path),
            source_filename=record.source_filename,
            checksum_sha256=record.checksum_sha256,
            mapping=mapping,
            min_wind_coverage=min_wind_coverage,
        )
        with self._lock:
            record.inspection = inspection
            record.normalized_result = normalized
            record.mapping = dict(mapping)
            record.min_wind_coverage = min_wind_coverage
        return record

    def delete(self, upload_id: str) -> None:
        with self._lock:
            record = self._records.pop(upload_id, None)
        if record is None:
            raise UploadNotFoundError(f"unknown upload: {upload_id}")
        upload_dir = record.path.parent.resolve()
        if upload_dir.parent != self.root or record.path.name != record.source_filename:
            raise DesktopServiceError("refusing to remove an upload outside the private store")
        shutil.rmtree(_filesystem_path(upload_dir))

    @staticmethod
    def verify_checksum(record: UploadRecord) -> bool:
        digest = hashlib.sha256()
        with _filesystem_path(record.path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == record.checksum_sha256


def resolve_engine_config(desktop: DesktopScenarioConfig) -> SimulationConfig:
    """Resolve UI-only aliases into a validated engine configuration copy."""

    engine = desktop.engine.model_copy(deep=True)
    if desktop.emissions is not None:
        engine.source = engine.source.model_copy(
            update={
                "pm25_emission_g_s": desktop.emissions.pm25_emission_g_s,
                "pm10_total_emission_g_s": desktop.emissions.pm10_total_emission_g_s,
            }
        )
    if desktop.background is not None:
        engine.background = engine.background.model_copy(
            update={
                "pm25_ug_m3": desktop.background.pm25_ug_m3,
                "coarse_pm_ug_m3": desktop.background.coarse_pm_ug_m3,
            }
        )
    wind_mode = {
        "synthetic_fallback": "synthetic",
        "strict_measured": "measured",
        "measured": "measured_interpolated",
    }[desktop.wind_policy.mode]
    wind_updates: dict[str, Any] = {"mode": wind_mode}
    if "interpolation_method" in type(engine.wind).model_fields:
        wind_updates["interpolation_method"] = desktop.wind_policy.interpolation
    engine.wind = engine.wind.model_copy(update=wind_updates)
    if desktop.sensor_region is not None:
        updates = {
            "sampling_region_size_x_m": desktop.sensor_region.size_x_m,
            "sampling_region_size_y_m": desktop.sensor_region.size_y_m,
            "sampling_region_size_z_m": desktop.sensor_region.size_z_m,
        }
        known = {
            name: value
            for name, value in updates.items()
            if name in type(engine.sensor).model_fields
        }
        if known:
            engine.sensor = engine.sensor.model_copy(update=known)
    if desktop.sensor_model is not None:
        updates = {
            "additive_noise_std_pm25_ug_m3": desktop.sensor_model.pm25_noise_std_ug_m3,
            "additive_noise_std_pm10_ug_m3": desktop.sensor_model.pm10_noise_std_ug_m3,
            "bias_pm25_ug_m3": desktop.sensor_model.pm25_bias_ug_m3,
            "bias_pm10_ug_m3": desktop.sensor_model.pm10_bias_ug_m3,
        }
        known = {
            name: value
            for name, value in updates.items()
            if name in type(engine.sensor).model_fields
        }
        if known:
            engine.sensor = engine.sensor.model_copy(update=known)
    if desktop.playback is not None:
        updates = {
            "playback_frame_interval_s": desktop.playback.frame_interval_s,
            "visualization_parcel_limit": desktop.playback.visualization_parcel_limit,
        }
        known = {
            name: value
            for name, value in updates.items()
            if name in type(engine.simulation).model_fields
        }
        if known:
            engine.simulation = engine.simulation.model_copy(update=known)
    return SimulationConfig.model_validate(engine.model_dump(mode="python"))


def validate_desktop_config(
    desktop: DesktopScenarioConfig,
    upload: UploadRecord | None,
) -> dict[str, Any]:
    engine = resolve_engine_config(desktop)
    warnings: list[str] = []
    errors: list[str] = []
    inspection = upload.inspection if upload is not None else None
    if inspection is not None and not inspection.get("valid", False):
        errors.extend(inspection.get("validation_errors", ["flight mapping is incomplete"]))
    if inspection is not None:
        coverage = float(inspection.get("valid_paired_wind_coverage", 0.0))
        paired_count = int(inspection.get("valid_paired_wind_rows", 0))
        enough_pairs = paired_count >= 2
        strict_usable = enough_pairs and coverage >= engine.wind.minimum_numeric_coverage
        if desktop.wind_policy.mode == "measured" and not enough_pairs:
            errors.append(
                "Logged measured wind requires at least two paired numeric "
                "wind-speed and wind-direction observations."
            )
        if desktop.wind_policy.mode == "strict_measured" and not strict_usable:
            errors.append(
                "Measured wind cannot be used: paired numeric coverage "
                f"{coverage:.1%} is below the configured "
                f"{engine.wind.minimum_numeric_coverage:.1%} threshold. "
                "Select synthetic fallback explicitly or supply adequate atmospheric wind."
            )
        if desktop.wind_policy.mode == "strict_measured":
            maximum_gap = inspection.get("maximum_bracketed_wind_gap_s")
            if maximum_gap is not None and maximum_gap > engine.wind.maximum_interpolation_gap_s:
                errors.append(
                    "Strict measured-wind mode rejects the largest bracketed gap "
                    f"({maximum_gap:.1f} s), which exceeds the configured "
                    f"{engine.wind.maximum_interpolation_gap_s:.1f} s."
                )
        if desktop.wind_policy.mode == "synthetic_fallback":
            warnings.append(
                "Synthetic fallback wind is explicitly selected; raw uploaded wind remains stored separately."
            )
        if desktop.wind_policy.mode == "measured" and enough_pairs:
            if inspection.get("input_format") == "sfd_xlsx":
                original_rows = int(inspection.get("sfd_original_wind_rows", 0))
                generated_rows = int(inspection.get("sfd_generated_wind_rows", 0))
                warnings.append(
                    "The SFD complete wind timeline is selected: "
                    f"{original_rows:,} original AirData estimates and "
                    f"{generated_rows:,} generated/interpolated values are used with their provenance retained."
                )
            else:
                warnings.append(
                    "Logged measured wind is selected: exact valid pairs are used, "
                    "internal missing intervals are vector-interpolated, and flight "
                    "boundaries hold the nearest measured vector."
                )
    if engine.source.latitude is None or engine.source.longitude is None:
        warnings.append(
            "Stack coordinates are not explicit; the engine will use the configured offset from the flight centre."
        )
    if desktop.wind_policy.interpolation == "nearest" and "interpolation_method" not in type(engine.wind).model_fields:
        errors.append("Nearest wind interpolation is not available in this engine build.")

    duration = float(inspection.get("duration_s", 0.0)) if inspection else 0.0
    steps = int(math.floor(duration / engine.simulation.timestep_s)) + 1 if duration else 0
    start = engine.source.emission_start_s
    end = engine.source.emission_end_s if engine.source.emission_end_s is not None else duration
    emission_duration = max(0.0, min(duration, end) - min(duration, start))
    emission_steps = int(math.ceil(emission_duration / engine.simulation.timestep_s))
    emitted_parcels = emission_steps * engine.simulation.particles_per_timestep
    frame_interval = getattr(engine.simulation, "playback_frame_interval_s", 2.0)
    frame_count = int(math.floor(duration / frame_interval)) + 1 if duration else 0
    sensor_sample_count = (
        int(inspection.get("usable_row_count", inspection.get("row_count", 0)))
        if inspection
        else 0
    )
    estimate = {
        "simulation_steps": steps,
        "emitted_parcels": emitted_parcels,
        "playback_frames": frame_count,
        "sensor_samples": sensor_sample_count,
        "estimated_memory_mb": round(emitted_parcels * 160 / (1024 * 1024), 1),
        "estimated_runtime_s": round(max(0.2, steps * max(emitted_parcels, 1) / 4_000_000), 1),
    }
    normalized = desktop.model_copy(update={"engine": engine})
    return {
        "valid": not errors,
        "config": normalized.model_dump(mode="json", exclude_none=True),
        "warnings": warnings,
        "errors": errors,
        "estimate": estimate,
    }


@dataclass(slots=True)
class JobRecord:
    job_id: str
    upload_id: str
    scenario_name: str
    output_root: Path
    final_dir: Path
    partial_dir: Path
    config: DesktopScenarioConfig
    engine_config: SimulationConfig
    state: JobState = JobState.QUEUED
    progress: JobProgress = field(default_factory=JobProgress)
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def as_dict(self) -> dict[str, Any]:
        now = self.finished_at or utc_now()
        origin = self.started_at or self.created_at
        return {
            "job_id": self.job_id,
            "state": self.state.value,
            "progress": self.progress.fraction,
            "stage": self.progress.stage,
            "stage_index": self.progress.stage_index,
            "stage_count": self.progress.stage_count,
            "message": self.progress.message,
            "elapsed_s": max(0.0, (now - origin).total_seconds()),
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
            "result": self.result,
        }


TraceRunner = Callable[..., Any]


def default_trace_runner(*args: Any, **kwargs: Any) -> Any:
    from .trace import run_trace_simulation

    return run_trace_simulation(*args, **kwargs)


def _stage_details(stage: str) -> tuple[str, int]:
    normalized = _header_key(stage)
    aliases = {
        "validatingcsv": 0,
        "validation": 0,
        "preparingcoordinatesystem": 1,
        "coordinates": 1,
        "preparingwindtimeline": 2,
        "wind": 2,
        "emittingandtransportingparcels": 3,
        "emittingtransportingparcels": 3,
        "simulation": 3,
        "transport": 3,
        "samplingthedronesensor": 4,
        "samplingdronesensor": 4,
        "sensor": 4,
        "generatingplaybackdata": 5,
        "playback": 5,
        "validatingoutputs": 6,
        "outputvalidation": 6,
        "writingoutputfiles": 7,
        "writing": 7,
        "complete": 7,
    }
    index = aliases.get(normalized)
    if index is None:
        for position, (slug, label) in enumerate(STAGES):
            if _header_key(slug) in normalized or _header_key(label) in normalized:
                index = position
                break
    if index is None:
        index = 0
        label = stage or STAGES[0][1]
    else:
        label = STAGES[index][1]
    return label, index + 1


class JobManager:
    def __init__(
        self,
        uploads: UploadStore,
        *,
        trace_runner: TraceRunner | None = None,
        completion_callback: Callable[[Path], str | None] | None = None,
    ):
        self.uploads = uploads
        self.trace_runner = trace_runner or default_trace_runner
        self.completion_callback = completion_callback
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voxmaps-simulation")
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.RLock()

    def create(self, request: JobCreateRequest, config: DesktopScenarioConfig) -> JobRecord:
        upload = self.uploads.get(request.upload_id)
        if upload.normalized_result is None or not upload.inspection.get("valid", False):
            raise JobConflictError("flight upload requires a valid column mapping before it can run")
        validation = validate_desktop_config(config, upload)
        if not validation["valid"]:
            raise JobConflictError("; ".join(validation["errors"]))
        output_root = request.output_folder.expanduser().resolve()
        filesystem_output_root = _filesystem_path(output_root)
        filesystem_output_root.mkdir(parents=True, exist_ok=True)
        if not filesystem_output_root.is_dir():
            raise JobConflictError(f"output folder is not a directory: {output_root}")
        final_dir = output_root / request.scenario_name
        if _filesystem_path(final_dir).exists():
            raise JobConflictError(
                f"scenario output folder already exists: {final_dir}. Choose a new scenario name."
            )
        job_id = uuid.uuid4().hex
        partial_dir = output_root / f".{request.scenario_name}.{job_id}.partial"
        if _filesystem_path(partial_dir).exists():
            raise JobConflictError(f"temporary scenario folder already exists: {partial_dir}")
        record = JobRecord(
            job_id=job_id,
            upload_id=request.upload_id,
            scenario_name=request.scenario_name,
            output_root=output_root,
            final_dir=final_dir,
            partial_dir=partial_dir,
            config=config,
            engine_config=resolve_engine_config(config),
        )
        with self._lock:
            self._jobs[job_id] = record
        self._executor.submit(self._run, record, upload)
        return record

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._jobs.get(job_id)
        if record is None:
            raise JobNotFoundError(f"unknown job: {job_id}")
        return record

    def status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return self.get(job_id).as_dict()

    def cancel(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self.get(job_id)
            if record.state in TERMINAL_JOB_STATES:
                return record
            record.cancel_event.set()
            record.state = JobState.CANCELLING
            record.progress.message = "Cancellation requested; stopping at a safe simulation boundary"
            return record

    def reset(self, job_id: str) -> None:
        with self._lock:
            record = self.get(job_id)
            if record.state not in TERMINAL_JOB_STATES:
                raise JobConflictError("an active simulation must finish or cancel before reset")
            del self._jobs[job_id]

    def has_active_upload(self, upload_id: str) -> bool:
        with self._lock:
            return any(
                job.upload_id == upload_id and job.state not in TERMINAL_JOB_STATES
                for job in self._jobs.values()
            )

    def _progress(self, record: JobRecord, fraction: float, stage: str, message: str) -> None:
        label, stage_index = _stage_details(stage)
        with self._lock:
            if record.state in TERMINAL_JOB_STATES:
                return
            record.progress = JobProgress(
                fraction=min(0.999, max(record.progress.fraction, float(fraction))),
                stage=label,
                stage_index=stage_index,
                message=message or label,
            )

    def _safe_cleanup_partial(self, record: JobRecord) -> None:
        partial = record.partial_dir.resolve()
        root = record.output_root.resolve()
        if partial.parent != root or not partial.name.startswith(".") or not partial.name.endswith(".partial"):
            raise OutputContractError("refusing to clean an unverified temporary output path")
        filesystem_partial = _filesystem_path(partial)
        if filesystem_partial.exists():
            shutil.rmtree(filesystem_partial)

    def _safe_cleanup_published(self, record: JobRecord) -> None:
        final = record.final_dir.resolve()
        root = record.output_root.resolve()
        if final.parent != root or final.name != record.scenario_name:
            raise OutputContractError("refusing to clean an unverified published output path")
        filesystem_final = _filesystem_path(final)
        if filesystem_final.exists():
            shutil.rmtree(filesystem_final)

    def _run(self, record: JobRecord, upload: UploadRecord) -> None:
        published = False
        with self._lock:
            record.started_at = utc_now()
            if record.cancel_event.is_set():
                record.state = JobState.CANCELLED
                record.finished_at = utc_now()
                record.progress.message = "Cancelled before simulation started"
                return
            record.state = JobState.RUNNING
            record.progress = JobProgress(
                fraction=0.0,
                stage=STAGES[0][1],
                stage_index=1,
                message="Verifying the immutable uploaded CSV",
            )
        try:
            if not self.uploads.verify_checksum(upload):
                raise OutputContractError("private upload checksum changed after inspection")
            partial_dir = _filesystem_path(record.partial_dir)
            final_dir = _filesystem_path(record.final_dir)
            partial_dir.mkdir(parents=False, exist_ok=False)
            trace_config = record.config.model_copy(
                update={"engine": record.engine_config}, deep=True
            )
            result = self.trace_runner(
                _filesystem_path(upload.path),
                trace_config,
                partial_dir,
                scenario_name=record.scenario_name,
                progress_callback=lambda fraction, stage, message: self._progress(
                    record, fraction, stage, message
                ),
                cancel_check=record.cancel_event.is_set,
            )
            if record.cancel_event.is_set():
                raise JobCancelled("simulation was cancelled")
            expected_names = {
                f"{record.scenario_name}_simulation_trace.h5",
                f"{record.scenario_name}_simulated_drone_sensor_log.csv",
            }
            actual_names = {path.name for path in partial_dir.iterdir() if path.is_file()}
            directories = [path.name for path in partial_dir.iterdir() if path.is_dir()]
            if actual_names != expected_names or directories:
                raise OutputContractError(
                    "completed scenario must expose exactly the HDF5 trace and clean sensor CSV; "
                    f"found files={sorted(actual_names)}, directories={directories}"
                )
            for name in expected_names:
                if (partial_dir / name).stat().st_size <= 0:
                    raise OutputContractError(f"output file is empty: {name}")
            if record.final_dir.exists():
                raise JobConflictError(f"scenario output folder appeared during the run: {record.final_dir}")
            os.replace(partial_dir, final_dir)
            published = True
            h5_path = record.final_dir / f"{record.scenario_name}_simulation_trace.h5"
            csv_path = record.final_dir / f"{record.scenario_name}_simulated_drone_sensor_log.csv"
            filesystem_h5 = _filesystem_path(h5_path)
            filesystem_csv = _filesystem_path(csv_path)
            playback_id: str | None = None
            if self.completion_callback is not None:
                try:
                    playback_id = self.completion_callback(filesystem_h5)
                except Exception:
                    playback_id = None
            mass_balance = getattr(result, "mass_balance", {})
            warnings = list(getattr(result, "warnings", []))
            simulation_id = str(getattr(result, "simulation_id", record.job_id))
            frame_count = int(getattr(result, "frame_count", 0))
            response = {
                "job_id": record.job_id,
                "simulation_id": simulation_id,
                "scenario_name": record.scenario_name,
                "output_folder": str(record.final_dir),
                "output_files": [
                    {
                        "name": h5_path.name,
                        "path": str(h5_path),
                        "size_bytes": filesystem_h5.stat().st_size,
                    },
                    {
                        "name": csv_path.name,
                        "path": str(csv_path),
                        "size_bytes": filesystem_csv.stat().st_size,
                    },
                ],
                "trace_path": str(h5_path),
                "sensor_log_path": str(csv_path),
                "playback_id": playback_id,
                "mass_balance": mass_balance,
                "warnings": warnings,
                "summary": {"frame_count": frame_count},
            }
            with self._lock:
                record.state = JobState.COMPLETED
                record.finished_at = utc_now()
                record.progress = JobProgress(
                    fraction=1.0,
                    stage=STAGES[-1][1],
                    stage_index=len(STAGES),
                    message="Simulation complete; exactly two scenario files were published",
                )
                record.result = response
        except Exception as exc:
            LOGGER.exception("VoxMaps simulation job %s failed", record.job_id)
            try:
                if published:
                    self._safe_cleanup_published(record)
                else:
                    self._safe_cleanup_partial(record)
            except Exception as cleanup_exc:
                exc = OutputContractError(f"{exc}; incomplete-output cleanup also failed: {cleanup_exc}")
            cancelled = (
                record.cancel_event.is_set()
                or isinstance(exc, JobCancelled)
                or type(exc).__name__ == "SimulationCancelled"
            )
            with self._lock:
                record.state = JobState.CANCELLED if cancelled else JobState.FAILED
                record.finished_at = utc_now()
                record.error = None if cancelled else str(exc)
                record.progress.message = "Simulation cancelled cleanly" if cancelled else f"Simulation failed: {exc}"

    def close(self) -> None:
        with self._lock:
            for record in self._jobs.values():
                if record.state not in TERMINAL_JOB_STATES:
                    record.cancel_event.set()
        self._executor.shutdown(wait=True, cancel_futures=True)


__all__ = [
    "DesktopServiceError",
    "JobCancelled",
    "JobConflictError",
    "JobManager",
    "JobNotFoundError",
    "MAX_UPLOAD_BYTES",
    "OutputContractError",
    "STAGES",
    "UploadNotFoundError",
    "UploadRecord",
    "UploadStore",
    "inspect_flight_copy",
    "resolve_engine_config",
    "validate_desktop_config",
]
