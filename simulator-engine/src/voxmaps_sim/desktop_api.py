"""Local FastAPI service for the VoxMaps desktop-style browser application."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import __version__
from .desktop_jobs import (
    DesktopServiceError,
    JobConflictError,
    JobManager,
    JobNotFoundError,
    UploadNotFoundError,
    UploadStore,
    validate_desktop_config,
)
from .desktop_models import (
    ConfigFileRequest,
    ConfigValidateRequest,
    DesktopScenarioConfig,
    FolderSelectRequest,
    JobCreateRequest,
    JobState,
    PlaybackLoadRequest,
    UploadMappingRequest,
)
from .ingestion import FlightDataError


APP_NAME = "VoxMaps Pollution Source Simulator"
RESEARCH_DISCLAIMER = (
    "Testing and research forward simulation; not CFD and not a regulatory dispersion model."
)


def frontend_dist_path() -> Path:
    """Return the expected production frontend directory.

    ``VOXMAPS_FRONTEND_DIST`` is useful for a packaged build; source checkouts
    use ``desktop/dist``.  Merely importing the API never builds or mutates it.
    """

    override = os.environ.get("VOXMAPS_FRONTEND_DIST")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "desktop" / "dist"


def default_workspace_path() -> Path:
    local_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_data) if local_data else Path(tempfile.gettempdir())
    return (base / "VoxMapsSimulator" / "workspace").resolve()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is pd.NA:
        return None
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    return value


def _native_choose_directory(suggested: Path | None = None) -> Path | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title="Choose a folder for VoxMaps scenario outputs",
            initialdir=str(suggested) if suggested else None,
            mustexist=True,
        )
        return Path(selected).resolve() if selected else None
    finally:
        root.destroy()


def _native_choose_config(save: bool) -> Path | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if save:
            selected = filedialog.asksaveasfilename(
                parent=root,
                title="Save VoxMaps configuration",
                defaultextension=".json",
                filetypes=(("VoxMaps configuration", "*.json"), ("JSON", "*.json")),
            )
        else:
            selected = filedialog.askopenfilename(
                parent=root,
                title="Load VoxMaps configuration",
                filetypes=(("VoxMaps configuration", "*.json"), ("JSON", "*.json")),
            )
        return Path(selected).resolve() if selected else None
    finally:
        root.destroy()


def _native_choose_trace() -> Path | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askopenfilename(
            parent=root,
            title="Load a VoxMaps simulation trace",
            filetypes=(("VoxMaps HDF5 trace", "*.h5"), ("HDF5", "*.hdf5")),
        )
        return Path(selected).resolve() if selected else None
    finally:
        root.destroy()


def _call_with_index(function: Callable[..., Any], path: Path, index: int) -> Any:
    signature = inspect.signature(function)
    names = list(signature.parameters)
    if "frame_index" in names:
        return function(path, frame_index=index)
    if "sample_index" in names:
        return function(path, sample_index=index)
    return function(path, index)


def _read_trace_summary(path: Path) -> dict[str, Any]:
    from .trace import read_trace_summary

    return _json_safe(read_trace_summary(path))


def _load_trace_frame(
    path: Path,
    index: int,
    particle_limit: int | None = None,
    sensor_region: dict[str, float] | None = None,
) -> dict[str, Any]:
    from .trace import load_playback_frame

    if "max_particles" in inspect.signature(load_playback_frame).parameters:
        loaded = load_playback_frame(
            path,
            frame_index=index,
            max_particles=particle_limit,
        )
    else:
        loaded = _call_with_index(load_playback_frame, path, index)
    raw = _json_safe(loaded)
    if not isinstance(raw, dict):
        raise DesktopServiceError("trace frame loader returned an invalid payload")
    # Convert the compact HDF5 helper form into the frontend's particle list.
    if "particles" not in raw:
        identifiers = raw.get("parcel_id", raw.get("parcel_ids", []))
        positions = raw.get("position_m", raw.get("positions_m", []))
        channels = raw.get("particle_class", raw.get("channel", []))
        masses = raw.get("mass_g", [])
        states = raw.get("state", [])
        particles: list[dict[str, Any]] = []
        for position, identifier in enumerate(identifiers):
            xyz = positions[position] if position < len(positions) else [0.0, 0.0, 0.0]
            channel = channels[position] if position < len(channels) else "fine"
            state = states[position] if position < len(states) else "active"
            if isinstance(channel, (int, float)):
                channel = "fine" if int(channel) == 0 else "coarse"
            if isinstance(state, (int, float)):
                state = {0: "active", 1: "deposited", 2: "removed"}.get(int(state), "active")
            particle_class = "deposited" if state == "deposited" else str(channel)
            particles.append(
                {
                    "id": int(identifier),
                    "parcel_id": int(identifier),
                    "particle_class": particle_class,
                    "x": float(xyz[0]),
                    "y": float(xyz[1]),
                    "z": float(xyz[2]),
                    "mass_g": float(masses[position]) if position < len(masses) else None,
                    "state": state,
                }
            )
        raw["particles"] = particles
    sensor_sample = raw.get("sensor_sample")
    if isinstance(sensor_sample, dict):
        raw.setdefault(
            "drone",
            {
                "x": sensor_sample.get("x_east_m", 0.0),
                "y": sensor_sample.get("y_north_m", 0.0),
                "z": sensor_sample.get("z_up_m", 0.0),
            },
        )
        for name in (
            "utc_time",
            "pm25_true_ug_m3",
            "pm10_true_ug_m3",
            "pm25_sensor_ug_m3",
            "pm10_sensor_ug_m3",
        ):
            if name in sensor_sample:
                raw.setdefault(name, sensor_sample[name])
    wind = raw.get("wind")
    if isinstance(wind, dict):
        raw.setdefault("wind_speed_mps", wind.get("speed_mps", 0.0))
        raw.setdefault("wind_direction_deg", wind.get("direction_from_deg", 0.0))
        raw.setdefault("wind_source", wind.get("source", ""))
    raw.setdefault("index", int(raw.get("frame_index", index)))
    particles = raw.get("particles", [])
    numerical_count = int(
        raw.get(
            "full_numerical_particle_count",
            raw.get("numerical_particle_count", len(particles)),
        )
    )
    if particle_limit is not None and len(particles) > particle_limit:
        # Evenly sample the stable HDF5 parcel ordering.  The index calculation
        # is deterministic, includes both ends, and never alters numerical data.
        selected = np.linspace(0, len(particles) - 1, particle_limit, dtype=int)
        raw["particles"] = [particles[int(position)] for position in selected]
    raw["numerical_particle_count"] = numerical_count
    raw["rendered_particle_count"] = len(raw.get("particles", []))
    if sensor_region is not None:
        raw.setdefault("sensor_region", sensor_region)
    return raw


def _sensor_csv_for_trace(path: Path) -> Path:
    suffix = "_simulation_trace.h5"
    if path.name.endswith(suffix):
        return path.with_name(
            path.name[: -len(suffix)] + "_simulated_drone_sensor_log.csv"
        )
    return path.with_suffix(".csv")


@dataclass(slots=True)
class PlaybackRecord:
    playback_id: str
    trace_path: Path
    summary: dict[str, Any]


class PlaybackStore:
    def __init__(self):
        self._records: dict[str, PlaybackRecord] = {}
        self._lock = threading.RLock()

    def register(self, path: Path) -> str:
        trace = path.expanduser().resolve()
        if not trace.is_file() or trace.suffix.casefold() not in {".h5", ".hdf5"}:
            raise DesktopServiceError(f"HDF5 trace does not exist: {trace}")
        summary = _read_trace_summary(trace)
        if "sensor_region" not in summary:
            try:
                import h5py

                with h5py.File(trace, "r") as handle:
                    sensor = handle["sensor"]
                    summary["sensor_region"] = {
                        "size_x_m": float(sensor.attrs["sampling_region_size_x_m"]),
                        "size_y_m": float(sensor.attrs["sampling_region_size_y_m"]),
                        "size_z_m": float(sensor.attrs["sampling_region_size_z_m"]),
                    }
            except (ImportError, KeyError, OSError):
                pass
        playback_id = uuid.uuid4().hex
        with self._lock:
            self._records[playback_id] = PlaybackRecord(playback_id, trace, summary)
        return playback_id

    def get(self, playback_id: str) -> PlaybackRecord:
        with self._lock:
            record = self._records.get(playback_id)
        if record is None:
            raise DesktopServiceError(f"unknown playback: {playback_id}")
        return record

    def metadata(self, playback_id: str) -> dict[str, Any]:
        record = self.get(playback_id)
        summary = dict(record.summary)
        frame_times = summary.get("frame_times_s", [])
        if frame_times:
            summary.setdefault("duration_s", float(frame_times[-1] - frame_times[0]))
            if len(frame_times) > 1:
                summary.setdefault(
                    "frame_interval_s",
                    float(np.median(np.diff(np.asarray(frame_times, dtype=float)))),
                )
        sensor_samples = summary.pop("samples", [])
        # The path is needed by the 3D overview, while full sensor samples are
        # served independently to keep metadata requests bounded.
        if sensor_samples:
            summary.setdefault(
                "flight_path",
                [
                    {
                        "x": sample.get("x_east_m", 0.0),
                        "y": sample.get("y_north_m", 0.0),
                        "z": sample.get("z_up_m", 0.0),
                    }
                    for sample in sensor_samples
                ],
            )
        summary.pop("wind", None)
        source = summary.get("source")
        if isinstance(source, dict):
            normalized_source = dict(source)
            normalized_source.setdefault("x", source.get("x_east_m", 0.0))
            normalized_source.setdefault("y", source.get("y_north_m", 0.0))
            normalized_source.setdefault("z", source.get("ground_z_m", 0.0))
            normalized_source.setdefault(
                "diameter_m", source.get("stack_diameter_m", 1.0)
            )
            summary["source"] = normalized_source
        summary["playback_id"] = playback_id
        summary.setdefault("simulation_id", record.trace_path.stem)
        summary.setdefault("sample_count", summary.get("sensor_sample_count", 0))
        summary.setdefault("duration_s", summary.get("flight_duration_s", 0.0))
        return _json_safe(summary)

    def frames(
        self,
        playback_id: str,
        start: int,
        end: int | None,
        limit: int,
        particle_limit: int,
    ) -> dict[str, Any]:
        record = self.get(playback_id)
        frame_count = int(record.summary.get("frame_count", 0))
        stop = frame_count if end is None else min(frame_count, end)
        stop = min(stop, start + limit)
        if start < 0 or start > frame_count or stop < start:
            raise DesktopServiceError("invalid playback frame range")
        return {
            "playback_id": playback_id,
            "start": start,
            "end": stop,
            "count": max(0, stop - start),
            "total": frame_count,
            "frames": [
                _load_trace_frame(
                    record.trace_path,
                    index,
                    particle_limit,
                    record.summary.get("sensor_region"),
                )
                for index in range(start, stop)
            ],
        }

    def samples(self, playback_id: str, start: int, end: int | None, limit: int) -> dict[str, Any]:
        record = self.get(playback_id)
        csv_path = _sensor_csv_for_trace(record.trace_path)
        if csv_path.is_file():
            frame = pd.read_csv(csv_path, low_memory=False)
            total = len(frame)
            stop = total if end is None else min(total, end)
            stop = min(stop, start + limit)
            if start < 0 or start > total or stop < start:
                raise DesktopServiceError("invalid sensor sample range")
            rows = frame.iloc[start:stop].to_dict(orient="records")
            return {
                "playback_id": playback_id,
                "start": start,
                "end": stop,
                "total": total,
                "samples": _json_safe(rows),
            }
        return self._samples_from_hdf5(record, start, end, limit)

    @staticmethod
    def _samples_from_hdf5(
        record: PlaybackRecord, start: int, end: int | None, limit: int
    ) -> dict[str, Any]:
        import h5py

        with h5py.File(record.trace_path, "r") as trace:
            if "sensor" not in trace:
                raise DesktopServiceError("trace contains no sensor dataset")
            sensor = trace["sensor"]
            if isinstance(sensor, h5py.Dataset):
                total = len(sensor)
                stop = min(total, total if end is None else end, start + limit)
                rows = sensor[start:stop]
                samples = [
                    {name: _json_safe(row[name]) for name in rows.dtype.names or ()}
                    for row in rows
                ]
            else:
                names = list(sensor.keys())
                total = len(sensor[names[0]]) if names else 0
                stop = min(total, total if end is None else end, start + limit)
                samples = [
                    {
                        name: _json_safe(sensor[name][index])
                        for name in names
                        if getattr(sensor[name], "shape", ())
                    }
                    for index in range(start, stop)
                ]
        return {
            "playback_id": record.playback_id,
            "start": start,
            "end": stop,
            "total": total,
            "samples": samples,
        }

    def contributors(self, playback_id: str, sample_id: int) -> dict[str, Any]:
        record = self.get(playback_id)
        from .trace import reconstruct_sensor_row

        reconstructed = _json_safe(
            _call_with_index(reconstruct_sensor_row, record.trace_path, sample_id)
        )
        contributors: list[dict[str, Any]] = []
        try:
            import h5py

            with h5py.File(record.trace_path, "r") as trace:
                group = trace.get("contributors")
                if group is not None and "offsets" in group:
                    offsets = group["offsets"]
                    if sample_id < 0 or sample_id + 1 >= len(offsets):
                        raise DesktopServiceError("sensor sample is outside the trace range")
                    first, stop = int(offsets[sample_id]), int(offsets[sample_id + 1])
                    ids = group["parcel_id"][first:stop]
                    masses = group["mass_g"][first:stop]
                    channels = group["channel"][first:stop]
                    for identifier, mass, channel in zip(ids, masses, channels, strict=True):
                        decoded = _json_safe(channel)
                        if isinstance(decoded, (int, float)):
                            decoded = "fine" if int(decoded) == 0 else "coarse"
                        contributors.append(
                            {
                                "parcel_id": int(identifier),
                                "mass_g": float(mass),
                                "particle_class": decoded,
                            }
                        )
        except ImportError:
            pass
        return {
            "playback_id": playback_id,
            "sample_id": sample_id,
            "reconstructed": reconstructed,
            "contributors": contributors,
        }


def _write_config(path: Path, config: DesktopScenarioConfig) -> None:
    target = path.expanduser().resolve()
    if target.suffix.casefold() != ".json":
        target = target.with_suffix(".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_text(
            json.dumps(config.model_dump(mode="json", exclude_none=True), indent=2),
            encoding="utf-8",
        )
        os.replace(partial, target)
    finally:
        if partial.exists():
            partial.unlink()


def _load_config(path: Path) -> DesktopScenarioConfig:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise DesktopServiceError(f"configuration file does not exist: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        return DesktopScenarioConfig.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DesktopServiceError(f"invalid VoxMaps configuration: {exc}") from exc


def create_app(
    *,
    workspace_dir: str | Path | None = None,
    trace_runner: Callable[..., Any] | None = None,
    frontend_dir: str | Path | None = None,
) -> FastAPI:
    """Create an isolated local app; injectable paths keep integration tests safe."""

    workspace = Path(workspace_dir).resolve() if workspace_dir else default_workspace_path()
    workspace.mkdir(parents=True, exist_ok=True)
    uploads = UploadStore(workspace / "uploads")
    playbacks = PlaybackStore()
    jobs = JobManager(
        uploads,
        trace_runner=trace_runner,
        completion_callback=playbacks.register,
    )

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        try:
            yield
        finally:
            jobs.close()

    application = FastAPI(
        title=APP_NAME,
        version=__version__,
        description=RESEARCH_DISCLAIMER,
        lifespan=lifespan,
    )
    application.state.workspace = workspace
    application.state.uploads = uploads
    application.state.jobs = jobs
    application.state.playbacks = playbacks
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8765",
            "http://localhost:8765",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:5182",
            "http://localhost:5182",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @application.exception_handler(UploadNotFoundError)
    @application.exception_handler(JobNotFoundError)
    async def not_found_handler(_request: Request, exc: DesktopServiceError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(JobConflictError)
    async def conflict_handler(_request: Request, exc: JobConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(DesktopServiceError)
    async def service_handler(_request: Request, exc: DesktopServiceError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.get("/api/health")
    def health() -> dict[str, Any]:
        dist = Path(frontend_dir).resolve() if frontend_dir else frontend_dist_path()
        return {
            "status": "ok",
            "app_name": APP_NAME,
            "version": __version__,
            "frontend_ready": (dist / "index.html").is_file(),
            "research_disclaimer": RESEARCH_DISCLAIMER,
        }

    @application.get("/api/config/defaults")
    def defaults() -> dict[str, Any]:
        return {
            "config": DesktopScenarioConfig.user_defaults().model_dump(
                mode="json", exclude_none=True
            ),
            "research_disclaimer": RESEARCH_DISCLAIMER,
        }

    @application.post("/api/demo/load-flight")
    def load_bundled_demo_flight() -> dict[str, Any]:
        """Inspect the supplied five-minute AirData-style input with the real upload path."""

        flight = Path(__file__).resolve().parents[2] / "demo-data" / "voxmaps-five-minute-input.csv"
        if not flight.is_file():
            raise HTTPException(status_code=503, detail="bundled five-minute demo flight is missing")
        try:
            with flight.open("rb") as source:
                record = uploads.create(flight.name, source)
        except FlightDataError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        output_folder = workspace / "demo-runs"
        output_folder.mkdir(parents=True, exist_ok=True)
        return {
            "upload_id": record.upload_id,
            "inspection": _json_safe(record.inspection),
            "output_folder": str(output_folder),
            "provenance": "bundled_simulated_five_minute_input",
        }

    @application.post("/api/demo/load-trace")
    def load_bundled_demo_trace() -> dict[str, Any]:
        """Open the matching supplied trace without pretending to rerun its solver."""

        trace = Path(__file__).resolve().parents[2] / "demo-data" / "voxmaps-five-minute-demo-20260926_simulation_trace.h5"
        if not trace.is_file():
            raise HTTPException(status_code=503, detail="bundled five-minute demo trace is missing")
        playback_id = playbacks.register(trace)
        return {"playback_id": playback_id, "metadata": playbacks.metadata(playback_id)}

    @application.post("/api/uploads")
    def upload(file: UploadFile = File(...)) -> dict[str, Any]:
        try:
            record = uploads.create(file.filename or "flight.csv", file.file)
        except FlightDataError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            file.file.close()
        return {"upload_id": record.upload_id, "inspection": _json_safe(record.inspection)}

    @application.put("/api/uploads/{upload_id}/mapping")
    def remap(upload_id: str, body: UploadMappingRequest) -> dict[str, Any]:
        record = uploads.remap(upload_id, body.mapping, body.min_wind_coverage)
        return {"upload_id": record.upload_id, "inspection": _json_safe(record.inspection)}

    @application.delete("/api/uploads/{upload_id}")
    def delete_upload(upload_id: str) -> dict[str, Any]:
        if jobs.has_active_upload(upload_id):
            raise JobConflictError("cancel the active simulation before deleting its upload")
        uploads.delete(upload_id)
        return {"ok": True, "upload_id": upload_id}

    @application.post("/api/config/validate")
    def validate_config(body: ConfigValidateRequest) -> dict[str, Any]:
        try:
            config = DesktopScenarioConfig.model_validate(body.config)
        except ValidationError as exc:
            return {
                "valid": False,
                "config": body.config,
                "warnings": [],
                "errors": [error["msg"] for error in exc.errors()],
                "estimate": {},
            }
        upload_record = uploads.get(body.upload_id) if body.upload_id else None
        return _json_safe(validate_desktop_config(config, upload_record))

    @application.post("/api/folders/select")
    def select_folder(body: FolderSelectRequest) -> dict[str, Any]:
        suggested = body.suggested_path
        documents_default = (Path.home() / "Documents" / "VoxMaps Simulations").resolve()
        configured_fallback = os.environ.get("VOXMAPS_DEFAULT_OUTPUT_DIR", "").strip()
        fallback = (
            suggested
            or (Path(configured_fallback).expanduser() if configured_fallback else None)
            or documents_default
        ).resolve()
        native_disabled = os.environ.get("VOXMAPS_DISABLE_NATIVE_DIALOG", "").strip() == "1"
        if body.use_native_dialog and not native_disabled:
            try:
                selected = _native_choose_directory(suggested or documents_default)
                if selected is None:
                    return {"path": "", "cancelled": True, "source": "native_dialog"}
                return {"path": str(selected), "cancelled": False, "source": "native_dialog"}
            except Exception:
                # Tk can be unavailable in headless test/service contexts.  The
                # fallback is explicit in the response and remains editable by
                # selecting a different folder when a native desktop is present.
                pass
        fallback.mkdir(parents=True, exist_ok=True)
        return {"path": str(fallback), "cancelled": False, "source": "fallback_default"}

    @application.post("/api/config/save")
    def save_config(body: ConfigFileRequest) -> dict[str, Any]:
        if body.config is None:
            raise HTTPException(status_code=422, detail="config is required when saving")
        try:
            config = DesktopScenarioConfig.model_validate(body.config)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        target = body.path
        if target is None:
            try:
                target = _native_choose_config(save=True)
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"native save dialog is unavailable: {exc}",
                ) from exc
        if target is None:
            return {"saved": False, "cancelled": True, "path": None}
        _write_config(target, config)
        final = target if target.suffix.casefold() == ".json" else target.with_suffix(".json")
        return {"saved": True, "cancelled": False, "path": str(final.resolve())}

    @application.post("/api/config/load")
    def load_config_file(body: ConfigFileRequest) -> dict[str, Any]:
        source = body.path
        if source is None:
            try:
                source = _native_choose_config(save=False)
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"native load dialog is unavailable: {exc}",
                ) from exc
        if source is None:
            return {"cancelled": True, "path": None, "config": None}
        config = _load_config(source)
        return {
            "cancelled": False,
            "path": str(source.resolve()),
            "config": config.model_dump(mode="json", exclude_none=True),
        }

    @application.post("/api/jobs")
    def create_job(body: JobCreateRequest) -> dict[str, Any]:
        try:
            config = DesktopScenarioConfig.model_validate(body.config)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record = jobs.create(body, config)
        return record.as_dict()

    @application.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        return _json_safe(jobs.status(job_id))

    @application.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request) -> StreamingResponse:
        jobs.get(job_id)

        async def stream():
            previous = ""
            while True:
                status = jobs.status(job_id)
                serialized = json.dumps(_json_safe(status), separators=(",", ":"))
                if serialized != previous:
                    yield f"event: progress\ndata: {serialized}\n\n"
                    previous = serialized
                if status["state"] in {"completed", "cancelled", "failed"}:
                    break
                if await request.is_disconnected():
                    break
                await asyncio.sleep(0.25)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @application.delete("/api/jobs/{job_id}")
    def cancel_job(job_id: str) -> dict[str, Any]:
        return _json_safe(jobs.cancel(job_id).as_dict())

    @application.post("/api/jobs/{job_id}/reset")
    def reset_job(job_id: str) -> dict[str, Any]:
        jobs.reset(job_id)
        return {"job_id": job_id, "reset": True}

    @application.get("/api/jobs/{job_id}/result")
    def job_result(job_id: str) -> dict[str, Any]:
        record = jobs.get(job_id)
        if record.state != JobState.COMPLETED or record.result is None:
            raise JobConflictError("simulation result is not complete")
        return _json_safe(record.result)

    @application.post("/api/playback/load")
    def load_playback(body: PlaybackLoadRequest) -> dict[str, Any]:
        trace = body.path
        if trace is None:
            acceptance_trace = os.environ.get("VOXMAPS_DEFAULT_TRACE_PATH", "").strip()
            if acceptance_trace:
                trace = Path(acceptance_trace)
            else:
                try:
                    trace = _native_choose_trace()
                except Exception as exc:
                    raise HTTPException(
                        status_code=503,
                        detail=f"native trace dialog is unavailable: {exc}",
                    ) from exc
        if trace is None:
            return {"cancelled": True, "playback_id": None, "metadata": None}
        playback_id = playbacks.register(trace)
        return {
            "cancelled": False,
            "playback_id": playback_id,
            "metadata": playbacks.metadata(playback_id),
        }

    @application.get("/api/playback/{playback_id}/metadata")
    def playback_metadata(playback_id: str) -> dict[str, Any]:
        return playbacks.metadata(playback_id)

    @application.get("/api/playback/{playback_id}/frames")
    def playback_frames(
        playback_id: str,
        start: int = Query(default=0, ge=0),
        end: int | None = Query(default=None, ge=0),
        limit: int = Query(default=60, ge=1, le=500),
        particle_limit: int = Query(default=5_000, ge=100, le=250_000),
        max_particles: int | None = Query(default=None, ge=100, le=250_000),
    ) -> dict[str, Any]:
        visual_limit = max_particles if max_particles is not None else particle_limit
        return _json_safe(
            playbacks.frames(playback_id, start, end, limit, visual_limit)
        )

    @application.get("/api/playback/{playback_id}/frames/{frame_index}")
    def playback_frame(
        playback_id: str,
        frame_index: int,
        particle_limit: int = Query(default=5_000, ge=100, le=250_000),
    ) -> dict[str, Any]:
        record = playbacks.get(playback_id)
        return _load_trace_frame(
            record.trace_path,
            frame_index,
            particle_limit,
            record.summary.get("sensor_region"),
        )

    @application.get("/api/playback/{playback_id}/samples")
    def playback_samples(
        playback_id: str,
        start: int = Query(default=0, ge=0),
        end: int | None = Query(default=None, ge=0),
        limit: int = Query(default=100_000, ge=1, le=250_000),
    ) -> dict[str, Any]:
        return _json_safe(playbacks.samples(playback_id, start, end, limit))

    @application.get("/api/playback/{playback_id}/samples/{sample_id}/contributors")
    def playback_contributors(playback_id: str, sample_id: int) -> dict[str, Any]:
        return _json_safe(playbacks.contributors(playback_id, sample_id))

    dist = Path(frontend_dir).resolve() if frontend_dir else frontend_dist_path()
    if (dist / "index.html").is_file():
        assets = dist / "assets"
        if assets.is_dir():
            application.mount("/assets", StaticFiles(directory=assets), name="assets")

        @application.get("/", include_in_schema=False)
        def frontend_index() -> FileResponse:
            return FileResponse(dist / "index.html")

        @application.get("/{client_path:path}", include_in_schema=False)
        def frontend_route(client_path: str) -> FileResponse:
            if client_path == "api" or client_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="unknown API endpoint")
            candidate = (dist / client_path).resolve()
            if candidate.is_file() and dist in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    else:

        @application.get("/", include_in_schema=False)
        def api_only_root() -> dict[str, Any]:
            return {
                "app_name": APP_NAME,
                "api": "/docs",
                "frontend_ready": False,
                "detail": f"Build the React frontend into {dist}",
            }

    return application


app = create_app()


__all__ = [
    "APP_NAME",
    "PlaybackStore",
    "RESEARCH_DISCLAIMER",
    "app",
    "create_app",
    "default_workspace_path",
    "frontend_dist_path",
]
