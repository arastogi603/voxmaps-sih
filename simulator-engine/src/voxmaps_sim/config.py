"""Validated YAML configuration for the VoxMaps research simulator.

Configuration models intentionally describe assumptions used by the simplified
research simulator; they are not calibrated atmospheric-model parameters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConfigurationError(ValueError):
    """Raised when a YAML file cannot be resolved into a valid configuration."""


class ConfigModel(BaseModel):
    """Strict base class shared by every configuration section."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ProjectConfig(ConfigModel):
    name: str = "voxmaps-pollution-simulator"
    scenario_id: str = "scenario"
    random_seed: int = 42


class InputConfig(ConfigModel):
    flight_csv: Path | None = None
    column_mapping: dict[str, str] = Field(default_factory=dict)
    invalid_row_policy: Literal["drop_and_report", "fail"] = "drop_and_report"


class CoordinatesConfig(ConfigModel):
    reference_mode: Literal[
        "first_valid_flight_point", "configured", "source_location"
    ] = "first_valid_flight_point"
    reference_latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    reference_longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    reference_altitude_m: float = 0.0
    flat_ground: bool = True

    @model_validator(mode="after")
    def configured_reference_is_complete(self) -> "CoordinatesConfig":
        if self.reference_mode == "configured" and (
            self.reference_latitude is None or self.reference_longitude is None
        ):
            raise ValueError(
                "coordinates.reference_latitude and reference_longitude are required "
                "when reference_mode='configured'"
            )
        return self


class SyntheticWindConfig(ConfigModel):
    base_speed_mps: float = Field(default=3.0, ge=0.0)
    base_direction_from_deg: float = Field(default=270.0)
    speed_variability_mps: float = Field(default=0.5, ge=0.0)
    direction_variability_deg: float = Field(default=10.0, ge=0.0)
    gust_period_s: float = Field(default=60.0, gt=0.0)


class WindConfig(ConfigModel):
    mode: Literal["measured", "measured_interpolated", "synthetic", "auto"] = "auto"
    interpolation_method: Literal["linear_vector", "nearest"] = "linear_vector"
    minimum_numeric_coverage: float = Field(default=0.8, ge=0.0, le=1.0)
    maximum_interpolation_gap_s: float = Field(default=30.0, gt=0.0)
    height_adjustment_enabled: bool = False
    reference_height_m: float = Field(default=10.0, gt=0.0)
    power_law_exponent: float = Field(default=0.143, ge=0.0)
    synthetic: SyntheticWindConfig = Field(default_factory=SyntheticWindConfig)


class EmissionSchedulePoint(ConfigModel):
    """A step-change in the fine and total-PM10 emission schedule."""

    time_s: float = Field(ge=0.0)
    pm25_emission_g_s: float = Field(ge=0.0)
    pm10_total_emission_g_s: float = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_mass_channels(self) -> "EmissionSchedulePoint":
        if self.pm10_total_emission_g_s < self.pm25_emission_g_s:
            raise ValueError(
                "PM10 total emission must be greater than or equal to PM2.5 emission"
            )
        return self

    @property
    def coarse_pm_emission_g_s(self) -> float:
        return self.pm10_total_emission_g_s - self.pm25_emission_g_s


class SourceConfig(ConfigModel):
    name: str = "demo_stack"
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    offset_east_from_flight_centre_m: float = -150.0
    offset_north_from_flight_centre_m: float = 0.0
    ground_elevation_m: float = 0.0
    stack_height_m: float = Field(default=60.0, gt=0.0)
    plume_rise_m: float = Field(default=10.0, ge=0.0)
    stack_diameter_m: float = Field(default=2.0, gt=0.0)
    exit_velocity_mps: float = Field(default=12.0, ge=0.0)
    exhaust_temperature_k: float = Field(default=423.15, gt=0.0)
    ambient_temperature_k: float = Field(default=298.15, gt=0.0)
    pm25_emission_g_s: float = Field(default=0.2, ge=0.0)
    pm10_total_emission_g_s: float = Field(default=0.5, ge=0.0)
    emission_start_s: float = Field(default=0.0, ge=0.0)
    emission_end_s: float | None = Field(default=None, ge=0.0)
    emission_schedule: list[EmissionSchedulePoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source(self) -> "SourceConfig":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("source latitude and longitude must be supplied together")
        if self.pm10_total_emission_g_s < self.pm25_emission_g_s:
            raise ValueError(
                "source.pm10_total_emission_g_s must be greater than or equal to "
                "source.pm25_emission_g_s"
            )
        if self.emission_end_s is not None and self.emission_end_s <= self.emission_start_s:
            raise ValueError("source.emission_end_s must be later than emission_start_s")
        times = [point.time_s for point in self.emission_schedule]
        if times != sorted(times) or len(times) != len(set(times)):
            raise ValueError("source.emission_schedule time_s values must be unique and sorted")
        return self

    @property
    def coarse_pm_emission_g_s(self) -> float:
        """Non-overlapping coarse mass (2.5 < diameter <= 10 micrometres)."""

        return self.pm10_total_emission_g_s - self.pm25_emission_g_s

    @property
    def effective_release_height_m(self) -> float:
        return self.stack_height_m + self.plume_rise_m


class SimulationRuntimeConfig(ConfigModel):
    timestep_s: float = Field(default=1.0, gt=0.0)
    trajectory_sample_interval_s: float = Field(default=1.0, gt=0.0)
    particles_per_timestep: int = Field(default=20, gt=0)
    warmup_s: float = Field(default=0.0, ge=0.0)
    preflight_wind_policy: Literal["none", "hold_first_wind"] = "none"
    horizontal_padding_m: float = Field(default=500.0, ge=0.0)
    vertical_min_m: float = 0.0
    vertical_max_m: float = 180.0
    maximum_particles: int = Field(default=250_000, gt=0)
    playback_frame_interval_s: float = Field(default=1.0, gt=0.0)
    visualization_parcel_limit: int = Field(default=5_000, gt=0)

    @model_validator(mode="after")
    def validate_vertical_domain(self) -> "SimulationRuntimeConfig":
        if self.vertical_max_m <= self.vertical_min_m:
            raise ValueError("simulation.vertical_max_m must exceed vertical_min_m")
        return self


class DispersionConfig(ConfigModel):
    horizontal_diffusivity_m2_s: float = Field(default=5.0, ge=0.0)
    vertical_diffusivity_m2_s: float = Field(default=1.0, ge=0.0)
    fine_settling_velocity_mps: float = Field(default=0.001, ge=0.0)
    coarse_settling_velocity_mps: float = Field(default=0.02, ge=0.0)
    fine_deposition_velocity_mps: float = Field(default=0.001, ge=0.0)
    coarse_deposition_velocity_mps: float = Field(default=0.01, ge=0.0)
    fine_decay_rate_s: float = Field(default=0.0, ge=0.0)
    coarse_decay_rate_s: float = Field(default=0.0, ge=0.0)


class VoxelConfig(ConfigModel):
    size_x_m: float = Field(default=20.0, gt=0.0)
    size_y_m: float = Field(default=20.0, gt=0.0)
    size_z_m: float = Field(default=10.0, gt=0.0)
    smoothing_enabled: bool = False
    smoothing_radius_voxels: int = Field(default=1, ge=1)


class BackgroundConfig(ConfigModel):
    pm25_ug_m3: float = Field(default=20.0, ge=0.0)
    coarse_pm_ug_m3: float = Field(default=15.0, ge=0.0)

    @property
    def pm10_ug_m3(self) -> float:
        return self.pm25_ug_m3 + self.coarse_pm_ug_m3


class SensorConfig(ConfigModel):
    sample_interval_s: float = Field(default=1.0, gt=0.0)
    output_sampling_mode: Literal["every_flight_sample", "fixed_interval"] = (
        "every_flight_sample"
    )
    sampling_region_size_x_m: float = Field(default=20.0, gt=0.0)
    sampling_region_size_y_m: float = Field(default=20.0, gt=0.0)
    sampling_region_size_z_m: float = Field(default=10.0, gt=0.0)
    response_time_pm25_s: float = Field(default=5.0, ge=0.0)
    response_time_pm10_s: float = Field(default=5.0, ge=0.0)
    transport_delay_s: float = Field(default=1.0, ge=0.0)
    additive_noise_std_ug_m3: float = Field(default=1.0, ge=0.0)
    additive_noise_std_pm25_ug_m3: float | None = Field(default=None, ge=0.0)
    additive_noise_std_pm10_ug_m3: float | None = Field(default=None, ge=0.0)
    multiplicative_noise_std_fraction: float = Field(default=0.03, ge=0.0)
    calibration_slope_pm25: float = Field(default=1.0, ge=0.0)
    calibration_slope_pm10: float = Field(default=1.0, ge=0.0)
    calibration_offset_pm25_ug_m3: float = 0.0
    calibration_offset_pm10_ug_m3: float = 0.0
    bias_pm25_ug_m3: float = 0.0
    bias_pm10_ug_m3: float = 0.0
    quantization_ug_m3: float = Field(default=0.1, ge=0.0)
    lower_detection_limit_ug_m3: float = Field(default=0.0, ge=0.0)
    upper_saturation_limit_ug_m3: float = Field(default=1_000.0, gt=0.0)
    dropout_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_value: float | None = None
    humidity_interference_enabled: bool = False
    humidity_coefficient_fraction_per_pct: float = 0.0
    rotor_wash_dilution_factor: float = Field(default=1.0, ge=0.0, le=1.0)
    allow_physically_inconsistent_raw_sensor_noise: bool = False

    @model_validator(mode="after")
    def validate_sensor_limits(self) -> "SensorConfig":
        if self.upper_saturation_limit_ug_m3 < self.lower_detection_limit_ug_m3:
            raise ValueError(
                "sensor.upper_saturation_limit_ug_m3 must be at least the lower "
                "detection limit"
            )
        return self


class OutputConfig(ConfigModel):
    write_particle_ground_truth: bool = True
    parquet_compression: str = "snappy"


class BatchConfig(ConfigModel):
    scenarios: int = Field(default=100, gt=0)
    train_fraction: float = Field(default=0.7, ge=0.0, le=1.0)
    validation_fraction: float = Field(default=0.15, ge=0.0, le=1.0)
    test_fraction: float = Field(default=0.15, ge=0.0, le=1.0)
    source_offset_east_m: tuple[float, float] = (-300.0, 100.0)
    source_offset_north_m: tuple[float, float] = (-200.0, 200.0)
    stack_height_m: tuple[float, float] = (30.0, 100.0)
    pm25_emission_g_s: tuple[float, float] = (0.05, 0.5)
    pm10_total_emission_g_s: tuple[float, float] = (0.2, 1.0)
    emission_start_fraction: tuple[float, float] = (0.0, 0.25)
    emission_end_fraction: tuple[float, float] = (0.7, 1.0)
    wind_speed_mps: tuple[float, float] = (1.0, 7.0)
    wind_direction_from_deg: tuple[float, float] = (0.0, 360.0)
    wind_variability_mps: tuple[float, float] = (0.0, 1.0)
    horizontal_diffusivity_m2_s: tuple[float, float] = (1.0, 12.0)
    additive_noise_std_ug_m3: tuple[float, float] = (0.0, 3.0)
    sensor_bias_ug_m3: tuple[float, float] = (-2.0, 2.0)
    response_lag_s: tuple[float, float] = (1.0, 15.0)
    background_pm25_ug_m3: tuple[float, float] = (5.0, 40.0)

    @model_validator(mode="after")
    def validate_batch(self) -> "BatchConfig":
        total = self.train_fraction + self.validation_fraction + self.test_fraction
        if abs(total - 1.0) > 1e-9:
            raise ValueError("batch train/validation/test fractions must sum to 1")
        range_fields = (
            "source_offset_east_m",
            "source_offset_north_m",
            "stack_height_m",
            "pm25_emission_g_s",
            "pm10_total_emission_g_s",
            "emission_start_fraction",
            "emission_end_fraction",
            "wind_speed_mps",
            "wind_direction_from_deg",
            "wind_variability_mps",
            "horizontal_diffusivity_m2_s",
            "additive_noise_std_ug_m3",
            "sensor_bias_ug_m3",
            "response_lag_s",
            "background_pm25_ug_m3",
        )
        for field_name in range_fields:
            low, high = getattr(self, field_name)
            if high < low:
                raise ValueError(f"batch.{field_name} upper bound must be >= lower bound")
        return self


class SimulationConfig(ConfigModel):
    """Top-level resolved simulator configuration."""

    project: ProjectConfig = Field(default_factory=ProjectConfig)
    input: InputConfig = Field(default_factory=InputConfig)
    coordinates: CoordinatesConfig = Field(default_factory=CoordinatesConfig)
    wind: WindConfig = Field(default_factory=WindConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
    simulation: SimulationRuntimeConfig = Field(default_factory=SimulationRuntimeConfig)
    dispersion: DispersionConfig = Field(default_factory=DispersionConfig)
    voxel: VoxelConfig = Field(default_factory=VoxelConfig)
    background: BackgroundConfig = Field(default_factory=BackgroundConfig)
    sensor: SensorConfig = Field(default_factory=SensorConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    batch: BatchConfig | None = None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml_mapping(path: Path, stack: tuple[Path, ...] = ()) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if resolved in stack:
        chain = " -> ".join(str(item) for item in (*stack, resolved))
        raise ConfigurationError(f"cyclic YAML extends chain: {chain}")
    if not resolved.is_file():
        raise ConfigurationError(f"configuration file does not exist: {resolved}")

    try:
        payload = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML in {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError(f"top-level YAML value must be a mapping: {resolved}")

    extends = payload.pop("extends", None)
    if extends is None:
        return payload
    parents = [extends] if isinstance(extends, str) else extends
    if not isinstance(parents, list) or not all(isinstance(item, str) for item in parents):
        raise ConfigurationError("extends must be a path string or a list of path strings")

    merged: dict[str, Any] = {}
    for parent in parents:
        parent_path = (resolved.parent / parent).resolve()
        merged = _deep_merge(merged, _load_yaml_mapping(parent_path, (*stack, resolved)))
    return _deep_merge(merged, payload)


def load_config(path: str | Path) -> SimulationConfig:
    """Load, resolve ``extends``, and validate a YAML configuration file."""

    config_path = Path(path)
    try:
        return SimulationConfig.model_validate(_load_yaml_mapping(config_path))
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError(f"invalid configuration {config_path}: {exc}") from exc


def save_resolved_config(config: SimulationConfig, path: str | Path) -> Path:
    """Write a fully expanded, JSON-compatible YAML configuration."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump(mode="json", exclude_none=False)
    target.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return target


AppConfig = SimulationConfig


__all__ = [
    "AppConfig",
    "BackgroundConfig",
    "BatchConfig",
    "ConfigurationError",
    "CoordinatesConfig",
    "DispersionConfig",
    "EmissionSchedulePoint",
    "InputConfig",
    "OutputConfig",
    "ProjectConfig",
    "SensorConfig",
    "SimulationConfig",
    "SimulationRuntimeConfig",
    "SourceConfig",
    "SyntheticWindConfig",
    "VoxelConfig",
    "WindConfig",
    "load_config",
    "save_resolved_config",
]
