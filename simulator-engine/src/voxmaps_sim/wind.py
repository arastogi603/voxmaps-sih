"""Atmospheric-wind validation, selection, conversion, and interpolation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from .config import SimulationConfig, SyntheticWindConfig, WindConfig
from .quality import QualityFlag, combine_quality_flags


class WindDataError(ValueError):
    """Base class for invalid or unavailable atmospheric wind data."""


class MeasuredWindUnavailableError(WindDataError):
    """Raised when measured mode cannot meet its explicit quality threshold."""


class AutoWindSelectionRequiredError(WindDataError):
    """Raised when auto mode refuses to silently substitute synthetic wind."""


@dataclass(frozen=True, slots=True)
class WindAvailability:
    row_count: int
    speed_numeric_count: int
    direction_numeric_count: int
    paired_valid_count: int
    speed_numeric_coverage: float
    direction_numeric_coverage: float
    paired_numeric_coverage: float
    minimum_numeric_coverage: float
    usable: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def meteorological_to_uv(
    speed_mps: Any, direction_from_deg: Any
) -> tuple[float, float] | tuple[np.ndarray, np.ndarray]:
    """Convert meteorological direction-from degrees to east/north velocity.

    A west wind (270 degrees) moves air eastward; a north wind (0 degrees)
    moves air southward.
    """

    speed, direction = np.broadcast_arrays(
        np.asarray(speed_mps, dtype=float), np.asarray(direction_from_deg, dtype=float)
    )
    theta = np.deg2rad(direction)
    eastward = -speed * np.sin(theta)
    northward = -speed * np.cos(theta)
    if speed.ndim == 0:
        return float(eastward), float(northward)
    return eastward, northward


def uv_to_meteorological(
    eastward_mps: Any, northward_mps: Any
) -> tuple[float, float] | tuple[np.ndarray, np.ndarray]:
    """Convert east/north movement components to speed and direction-from."""

    eastward, northward = np.broadcast_arrays(
        np.asarray(eastward_mps, dtype=float),
        np.asarray(northward_mps, dtype=float),
    )
    speed = np.hypot(eastward, northward)
    direction = np.mod(np.rad2deg(np.arctan2(-eastward, -northward)), 360.0)
    direction = np.where(np.isclose(direction, 360.0, atol=1e-10), 0.0, direction)
    direction = np.where(
        np.isfinite(eastward) & np.isfinite(northward) & (speed <= 1e-12),
        0.0,
        direction,
    )
    if speed.ndim == 0:
        return float(speed), float(direction)
    return speed, direction


meteorological_wind_to_components = meteorological_to_uv


def assess_measured_wind(
    flight: pd.DataFrame, minimum_numeric_coverage: float = 0.8
) -> WindAvailability:
    """Assess only explicit atmospheric wind fields.

    Drone velocity components and compass heading are deliberately absent from
    this function and can never become a fallback source.
    """

    if not 0.0 <= minimum_numeric_coverage <= 1.0:
        raise ValueError("minimum_numeric_coverage must be in [0, 1]")
    rows = len(flight)
    if "wind_speed_mps" not in flight or "wind_direction_from_deg" not in flight:
        return WindAvailability(
            row_count=rows,
            speed_numeric_count=0,
            direction_numeric_count=0,
            paired_valid_count=0,
            speed_numeric_coverage=0.0,
            direction_numeric_coverage=0.0,
            paired_numeric_coverage=0.0,
            minimum_numeric_coverage=minimum_numeric_coverage,
            usable=False,
            reason=(
                "normalized atmospheric wind_speed_mps and "
                "wind_direction_from_deg columns are required"
            ),
        )

    speed = pd.to_numeric(flight["wind_speed_mps"], errors="coerce")
    direction = pd.to_numeric(flight["wind_direction_from_deg"], errors="coerce")
    speed_valid = speed.notna() & np.isfinite(speed) & speed.ge(0.0)
    direction_valid = (
        direction.notna()
        & np.isfinite(direction)
        & direction.ge(0.0)
        & direction.le(360.0)
    )
    paired = speed_valid & direction_valid
    speed_coverage = float(speed_valid.mean()) if rows else 0.0
    direction_coverage = float(direction_valid.mean()) if rows else 0.0
    paired_coverage = float(paired.mean()) if rows else 0.0
    usable = rows > 0 and int(paired.sum()) >= 2 and paired_coverage >= minimum_numeric_coverage
    if rows == 0:
        reason = "flight data contains no rows"
    elif int(paired.sum()) < 2:
        reason = "fewer than two paired numeric atmospheric-wind observations"
    elif paired_coverage < minimum_numeric_coverage:
        reason = (
            f"paired atmospheric-wind numeric coverage {paired_coverage:.1%} is below "
            f"the configured minimum {minimum_numeric_coverage:.1%}"
        )
    else:
        reason = "paired numeric atmospheric wind meets the configured threshold"
    return WindAvailability(
        row_count=rows,
        speed_numeric_count=int(speed_valid.sum()),
        direction_numeric_count=int(direction_valid.sum()),
        paired_valid_count=int(paired.sum()),
        speed_numeric_coverage=speed_coverage,
        direction_numeric_coverage=direction_coverage,
        paired_numeric_coverage=paired_coverage,
        minimum_numeric_coverage=minimum_numeric_coverage,
        usable=usable,
        reason=reason,
    )


def _finalize_wind_frame(
    elapsed_time_s: np.ndarray,
    speed_mps: np.ndarray,
    direction_from_deg: np.ndarray,
    source: str,
    flags: list[str],
    interpolated: np.ndarray | None = None,
    gap_exceeded: np.ndarray | None = None,
) -> pd.DataFrame:
    eastward, northward = meteorological_to_uv(speed_mps, direction_from_deg)
    frame = pd.DataFrame(
        {
            "elapsed_time_s": elapsed_time_s,
            "wind_speed_mps": speed_mps,
            "wind_direction_from_deg": direction_from_deg,
            "wind_u_mps": eastward,
            "wind_v_mps": northward,
            "wind_w_mps": np.zeros(len(elapsed_time_s), dtype=float),
            "wind_source": source,
            "quality_flags": flags,
        }
    )
    frame["wind_direction_deg"] = frame["wind_direction_from_deg"]
    frame["u_mps"] = frame["wind_u_mps"]
    frame["v_mps"] = frame["wind_v_mps"]
    frame["wind_interpolated"] = (
        np.zeros(len(frame), dtype=bool) if interpolated is None else interpolated
    )
    frame["wind_gap_exceeded"] = (
        np.zeros(len(frame), dtype=bool) if gap_exceeded is None else gap_exceeded
    )
    return frame


def interpolate_measured_wind(
    flight: pd.DataFrame,
    target_times_s: Any,
    maximum_interpolation_gap_s: float = 30.0,
    method: str = "linear_vector",
    *,
    allow_long_gaps: bool = False,
    edge_policy: str = "missing",
) -> pd.DataFrame:
    """Interpolate paired measured wind in vector-component space.

    Strict callers leave long gaps and values outside the observation range
    missing. The explicit logged-timeline mode can interpolate all internal gaps
    and hold the nearest measured vector at the flight boundaries. Neither mode
    substitutes synthetic wind.
    """

    if maximum_interpolation_gap_s <= 0.0:
        raise ValueError("maximum_interpolation_gap_s must be positive")
    if method not in {"linear_vector", "nearest"}:
        raise ValueError("wind interpolation method must be 'linear_vector' or 'nearest'")
    if edge_policy not in {"missing", "hold_nearest"}:
        raise ValueError("wind edge_policy must be 'missing' or 'hold_nearest'")
    required = {"elapsed_time_s", "wind_speed_mps", "wind_direction_from_deg"}
    missing = sorted(required.difference(flight.columns))
    if missing:
        raise WindDataError(f"cannot interpolate measured wind; missing columns: {missing}")

    times = pd.to_numeric(flight["elapsed_time_s"], errors="coerce")
    speed = pd.to_numeric(flight["wind_speed_mps"], errors="coerce")
    direction = pd.to_numeric(flight["wind_direction_from_deg"], errors="coerce")
    valid = (
        times.notna()
        & np.isfinite(times)
        & speed.notna()
        & np.isfinite(speed)
        & speed.ge(0.0)
        & direction.notna()
        & np.isfinite(direction)
        & direction.ge(0.0)
        & direction.le(360.0)
    )
    if int(valid.sum()) < 2:
        raise MeasuredWindUnavailableError(
            "measured wind requires at least two paired numeric speed/direction observations"
        )

    observation_times = times[valid].to_numpy(dtype=float)
    observation_u, observation_v = meteorological_to_uv(
        speed[valid].to_numpy(dtype=float), direction[valid].to_numpy(dtype=float)
    )
    observations = (
        pd.DataFrame(
            {
                "time": observation_times,
                "u": observation_u,
                "v": observation_v,
            }
        )
        .groupby("time", as_index=False, sort=True)[["u", "v"]]
        .mean()
    )
    observation_times = observations["time"].to_numpy(dtype=float)
    observation_u = observations["u"].to_numpy(dtype=float)
    observation_v = observations["v"].to_numpy(dtype=float)
    if len(observation_times) < 2:
        raise MeasuredWindUnavailableError(
            "measured wind requires observations at two distinct elapsed times"
        )

    target = np.asarray(target_times_s, dtype=float)
    if target.ndim != 1:
        raise ValueError("target_times_s must be one-dimensional")
    output_u = np.full(target.shape, np.nan, dtype=float)
    output_v = np.full(target.shape, np.nan, dtype=float)
    interpolated = np.zeros(target.shape, dtype=bool)
    gap_exceeded = np.zeros(target.shape, dtype=bool)

    right = np.searchsorted(observation_times, target, side="left")
    right_clipped = np.minimum(right, len(observation_times) - 1)
    left_clipped = np.maximum(right - 1, 0)
    right_distance = np.abs(observation_times[right_clipped] - target)
    left_distance = np.abs(observation_times[left_clipped] - target)
    nearest_observation = np.where(
        left_distance <= right_distance, left_clipped, right_clipped
    )
    exact = np.isfinite(target) & np.isclose(
        observation_times[nearest_observation], target, atol=1e-9, rtol=0.0
    )
    exact_indices = nearest_observation[exact]
    output_u[exact] = observation_u[exact_indices]
    output_v[exact] = observation_v[exact_indices]

    between = ~exact & (right > 0) & (right < len(observation_times))
    between_positions = np.flatnonzero(between)
    if len(between_positions):
        left_indices = right[between_positions] - 1
        right_indices = right[between_positions]
        gaps = observation_times[right_indices] - observation_times[left_indices]
        allowed = (
            np.ones(gaps.shape, dtype=bool)
            if allow_long_gaps
            else gaps <= maximum_interpolation_gap_s
        )
        allowed_positions = between_positions[allowed]
        if len(allowed_positions):
            left_allowed = right[allowed_positions] - 1
            right_allowed = right[allowed_positions]
            fraction = (
                target[allowed_positions] - observation_times[left_allowed]
            ) / (observation_times[right_allowed] - observation_times[left_allowed])
            if method == "nearest":
                chosen = np.where(fraction <= 0.5, left_allowed, right_allowed)
                output_u[allowed_positions] = observation_u[chosen]
                output_v[allowed_positions] = observation_v[chosen]
            else:
                output_u[allowed_positions] = observation_u[left_allowed] + fraction * (
                    observation_u[right_allowed] - observation_u[left_allowed]
                )
                output_v[allowed_positions] = observation_v[left_allowed] + fraction * (
                    observation_v[right_allowed] - observation_v[left_allowed]
                )
            interpolated[allowed_positions] = True
        gap_exceeded[between_positions[~allowed]] = True

    if edge_policy == "hold_nearest":
        finite_target = np.isfinite(target)
        before = ~exact & finite_target & (target < observation_times[0])
        after = ~exact & finite_target & (target > observation_times[-1])
        output_u[before] = observation_u[0]
        output_v[before] = observation_v[0]
        output_u[after] = observation_u[-1]
        output_v[after] = observation_v[-1]
        interpolated[before | after] = True

    output_speed, output_direction = uv_to_meteorological(output_u, output_v)
    missing_output = ~np.isfinite(output_speed) | ~np.isfinite(output_direction)
    flags: list[str] = []
    for is_interpolated, is_gap, is_missing in zip(
        interpolated, gap_exceeded, missing_output, strict=True
    ):
        row_flags: list[str] = []
        if is_interpolated:
            row_flags.append(QualityFlag.INTERPOLATION)
        if is_gap:
            row_flags.append(QualityFlag.INTERPOLATION_GAP_EXCEEDED)
        if is_missing:
            row_flags.append(QualityFlag.MISSING_WIND)
        flags.append(combine_quality_flags(row_flags))
    return _finalize_wind_frame(
        target,
        np.asarray(output_speed, dtype=float),
        np.asarray(output_direction, dtype=float),
        "measured",
        flags,
        interpolated,
        gap_exceeded,
    )


def generate_synthetic_wind(
    target_times_s: Any,
    config: SyntheticWindConfig,
    random_seed: int = 42,
) -> pd.DataFrame:
    """Generate an explicitly labelled, deterministic synthetic wind series."""

    target = np.asarray(target_times_s, dtype=float)
    if target.ndim != 1:
        raise ValueError("target_times_s must be one-dimensional")
    rng = np.random.default_rng(random_seed)
    phase_speed, phase_direction = rng.uniform(0.0, 2.0 * np.pi, size=2)
    angular_frequency = 2.0 * np.pi / config.gust_period_s
    speed = config.base_speed_mps + config.speed_variability_mps * np.sin(
        angular_frequency * target + phase_speed
    )
    speed = np.maximum(speed, 0.0)
    direction = np.mod(
        config.base_direction_from_deg
        + config.direction_variability_deg
        * np.sin(angular_frequency * target + phase_direction),
        360.0,
    )
    flags = [str(QualityFlag.SYNTHETIC_WIND)] * len(target)
    return _finalize_wind_frame(target, speed, direction, "synthetic", flags)


def _wind_config(value: WindConfig | SimulationConfig) -> WindConfig:
    return value.wind if isinstance(value, SimulationConfig) else value


def build_wind_series(
    flight: pd.DataFrame,
    config: WindConfig | SimulationConfig,
    target_times_s: Any | None = None,
    *,
    random_seed: int | None = None,
) -> pd.DataFrame:
    """Build the selected wind series without any silent mode fallback."""

    wind_config = _wind_config(config)
    if target_times_s is None:
        if "elapsed_time_s" not in flight:
            raise WindDataError("normalized flight data lacks elapsed_time_s")
        target_times_s = pd.to_numeric(
            flight["elapsed_time_s"], errors="coerce"
        ).to_numpy(dtype=float)
    availability = assess_measured_wind(
        flight, wind_config.minimum_numeric_coverage
    )
    if wind_config.mode == "synthetic":
        seed = (
            config.project.random_seed
            if isinstance(config, SimulationConfig) and random_seed is None
            else (42 if random_seed is None else random_seed)
        )
        return generate_synthetic_wind(target_times_s, wind_config.synthetic, seed)

    if wind_config.mode == "measured_interpolated":
        if availability.paired_valid_count < 2:
            raise MeasuredWindUnavailableError(
                "Logged measured wind requires at least two paired numeric "
                "speed/direction observations. Drone motion is not used as wind."
            )
        return interpolate_measured_wind(
            flight,
            target_times_s,
            wind_config.maximum_interpolation_gap_s,
            wind_config.interpolation_method,
            allow_long_gaps=True,
            edge_policy="hold_nearest",
        )

    if not availability.usable:
        message = (
            f"Measured atmospheric wind is unavailable: {availability.reason}. "
            "Drone xSpeed/ySpeed/zSpeed and compass heading are not atmospheric wind."
        )
        if wind_config.mode == "auto":
            raise AutoWindSelectionRequiredError(
                message
                + " Auto mode will not silently switch to synthetic wind; explicitly "
                "select wind.mode='synthetic' to use configured synthetic wind."
            )
        raise MeasuredWindUnavailableError(message)
    return interpolate_measured_wind(
        flight,
        target_times_s,
        wind_config.maximum_interpolation_gap_s,
        wind_config.interpolation_method,
    )


def adjust_wind_for_height(
    speed_mps: Any,
    height_m: Any,
    *,
    reference_height_m: float = 10.0,
    power_law_exponent: float = 0.143,
) -> np.ndarray:
    """Optional power-law speed adjustment; callers opt in explicitly."""

    if reference_height_m <= 0.0:
        raise ValueError("reference_height_m must be positive")
    if power_law_exponent < 0.0:
        raise ValueError("power_law_exponent must be non-negative")
    speed, height = np.broadcast_arrays(
        np.asarray(speed_mps, dtype=float), np.asarray(height_m, dtype=float)
    )
    safe_height = np.maximum(height, np.finfo(float).eps)
    return speed * np.power(safe_height / reference_height_m, power_law_exponent)


__all__ = [
    "AutoWindSelectionRequiredError",
    "MeasuredWindUnavailableError",
    "WindAvailability",
    "WindDataError",
    "adjust_wind_for_height",
    "assess_measured_wind",
    "build_wind_series",
    "generate_synthetic_wind",
    "interpolate_measured_wind",
    "meteorological_to_uv",
    "meteorological_wind_to_components",
    "uv_to_meteorological",
]
