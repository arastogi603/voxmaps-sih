"""Typed contracts for the local VoxMaps desktop-style application.

The browser-facing model deliberately presents fine and coarse particulate
mass as non-overlapping channels.  The adapter in :mod:`desktop_jobs` converts
those values to the legacy engine's PM2.5/total-PM10 representation only at
the numerical boundary.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import SimulationConfig


class DesktopModel(BaseModel):
    """Strict base model shared by local API request contracts."""

    model_config = ConfigDict(extra="forbid")


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"


TERMINAL_JOB_STATES = {
    JobState.CANCELLED,
    JobState.FAILED,
    JobState.COMPLETED,
}


class EmissionChannels(DesktopModel):
    """Non-overlapping source channels exposed by the GUI."""

    pm25_emission_g_s: float = Field(default=0.2, ge=0.0)
    coarse_pm_emission_g_s: float = Field(default=0.3, ge=0.0)

    @property
    def pm10_total_emission_g_s(self) -> float:
        return self.pm25_emission_g_s + self.coarse_pm_emission_g_s


class BackgroundChannels(DesktopModel):
    """GUI background fields with an explicit PM10 identity check."""

    pm25_ug_m3: float = Field(default=20.0, ge=0.0)
    pm10_ug_m3: float = Field(default=35.0, ge=0.0)

    @model_validator(mode="after")
    def pm10_contains_pm25_once(self) -> "BackgroundChannels":
        if self.pm10_ug_m3 < self.pm25_ug_m3:
            raise ValueError("background PM10 must be greater than or equal to PM2.5")
        return self

    @property
    def coarse_pm_ug_m3(self) -> float:
        return self.pm10_ug_m3 - self.pm25_ug_m3


class SensorRegionSettings(DesktopModel):
    size_x_m: float = Field(default=20.0, gt=0.0, le=1_000.0)
    size_y_m: float = Field(default=20.0, gt=0.0, le=1_000.0)
    size_z_m: float = Field(default=10.0, gt=0.0, le=1_000.0)


class PlaybackSettings(DesktopModel):
    frame_interval_s: float = Field(default=2.0, gt=0.0, le=300.0)
    visualization_parcel_limit: int = Field(default=5_000, ge=100, le=250_000)


class WindPolicy(DesktopModel):
    mode: Literal["measured", "synthetic_fallback", "strict_measured"] = "measured"
    interpolation: Literal["linear_vector", "nearest"] = "linear_vector"


class SensorModelAliases(DesktopModel):
    pm25_noise_std_ug_m3: float = Field(default=1.0, ge=0.0)
    pm10_noise_std_ug_m3: float = Field(default=1.0, ge=0.0)
    pm25_bias_ug_m3: float = 0.0
    pm10_bias_ug_m3: float = 0.0


class DesktopScenarioConfig(DesktopModel):
    """Scenario configuration accepted and returned by the local API.

    ``engine`` remains the authoritative validated simulation configuration.
    Optional aliases are included so a non-programmer never has to reason
    about total PM10 source mass or the engine's internal coarse-background
    representation.  They are resolved immediately before a run.
    """

    engine: SimulationConfig = Field(default_factory=SimulationConfig)
    wind_policy: WindPolicy = Field(default_factory=WindPolicy)
    emissions: EmissionChannels | None = None
    background: BackgroundChannels | None = None
    sensor_model: SensorModelAliases | None = None
    sensor_region: SensorRegionSettings | None = None
    playback: PlaybackSettings | None = None

    @classmethod
    def user_defaults(cls) -> "DesktopScenarioConfig":
        engine = SimulationConfig()
        fine_noise = getattr(engine.sensor, "additive_noise_std_pm25_ug_m3", None)
        coarse_noise = getattr(engine.sensor, "additive_noise_std_pm10_ug_m3", None)
        return cls(
            engine=engine,
            wind_policy=WindPolicy(
                mode=(
                    "synthetic_fallback"
                    if engine.wind.mode == "synthetic"
                    else "measured"
                ),
                interpolation="linear_vector",
            ),
            emissions=EmissionChannels(
                pm25_emission_g_s=engine.source.pm25_emission_g_s,
                coarse_pm_emission_g_s=engine.source.coarse_pm_emission_g_s,
            ),
            background=BackgroundChannels(
                pm25_ug_m3=engine.background.pm25_ug_m3,
                pm10_ug_m3=engine.background.pm10_ug_m3,
            ),
            sensor_model=SensorModelAliases(
                pm25_noise_std_ug_m3=(
                    engine.sensor.additive_noise_std_ug_m3
                    if fine_noise is None
                    else fine_noise
                ),
                pm10_noise_std_ug_m3=(
                    engine.sensor.additive_noise_std_ug_m3
                    if coarse_noise is None
                    else coarse_noise
                ),
                pm25_bias_ug_m3=engine.sensor.bias_pm25_ug_m3,
                pm10_bias_ug_m3=engine.sensor.bias_pm10_ug_m3,
            ),
            sensor_region=SensorRegionSettings(
                size_x_m=getattr(engine.sensor, "sampling_region_size_x_m", 20.0),
                size_y_m=getattr(engine.sensor, "sampling_region_size_y_m", 20.0),
                size_z_m=getattr(engine.sensor, "sampling_region_size_z_m", 10.0),
            ),
            playback=PlaybackSettings(
                frame_interval_s=getattr(
                    engine.simulation, "playback_frame_interval_s", 2.0
                ),
                visualization_parcel_limit=getattr(
                    engine.simulation, "visualization_parcel_limit", 5_000
                ),
            ),
        )


class UploadMappingRequest(DesktopModel):
    mapping: dict[str, str] = Field(default_factory=dict)
    min_wind_coverage: float = Field(default=0.0, ge=0.0, le=1.0)


class ConfigValidateRequest(DesktopModel):
    upload_id: str | None = None
    config: dict[str, Any]


class ConfigFileRequest(DesktopModel):
    path: Path | None = None
    config: dict[str, Any] | None = None


class FolderSelectRequest(DesktopModel):
    use_native_dialog: bool = True
    suggested_path: Path | None = None


class JobCreateRequest(DesktopModel):
    upload_id: str
    scenario_name: str = Field(min_length=1, max_length=80)
    output_folder: Path
    config: dict[str, Any]

    @model_validator(mode="after")
    def safe_scenario_name(self) -> "JobCreateRequest":
        value = self.scenario_name.strip()
        forbidden = {"/", "\\", ":", "*", "?", '"', "<", ">", "|"}
        if not value or value in {".", ".."} or any(char in value for char in forbidden):
            raise ValueError("scenario name contains characters that are unsafe on Windows")
        if value.endswith((" ", ".")):
            raise ValueError("scenario name cannot end with a space or period")
        reserved = {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
        }
        if value.upper() in reserved:
            raise ValueError("scenario name is reserved by Windows")
        self.scenario_name = value
        return self


class PlaybackLoadRequest(DesktopModel):
    path: Path | None = None

    @field_validator("path", mode="before")
    @classmethod
    def blank_path_opens_dialog(cls, value: Any) -> Any:
        return None if value == "" else value


class JobProgress(DesktopModel):
    fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    stage: str = "queued"
    stage_index: int = Field(default=0, ge=0)
    stage_count: int = 8
    message: str = "Waiting to start"


__all__ = [
    "BackgroundChannels",
    "ConfigFileRequest",
    "ConfigValidateRequest",
    "DesktopScenarioConfig",
    "EmissionChannels",
    "FolderSelectRequest",
    "JobCreateRequest",
    "JobProgress",
    "JobState",
    "PlaybackLoadRequest",
    "PlaybackSettings",
    "SensorRegionSettings",
    "SensorModelAliases",
    "TERMINAL_JOB_STATES",
    "UploadMappingRequest",
    "WindPolicy",
]
