"""Smokestack source and non-overlapping particulate emission channels.

PM2.5 is the fine fraction of PM10.  The simulator therefore carries fine PM
and coarse PM (2.5--10 micrometres) as separate mass channels and derives total
PM10 from their sum.  This module deliberately models a prescribed effective
release height; it is not a plume-rise or stack-flow CFD solver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import inf, isfinite
from typing import Any, Mapping, Sequence

import numpy as np


def _value(obj: Any, name: str, default: Any = None) -> Any:
    """Read a key/attribute from mapping, dataclass, or Pydantic-like objects."""

    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _source_section(config: Any) -> Any:
    section = _value(config, "source", None)
    return config if section is None else section


def validate_pm_emissions(pm25_emission_g_s: float, pm10_total_emission_g_s: float) -> None:
    """Validate the PM mass relationship and non-negative finite rates.

    Raises:
        ValueError: if a rate is negative/non-finite or total PM10 is smaller
            than its PM2.5 subset.
    """

    pm25 = float(pm25_emission_g_s)
    pm10 = float(pm10_total_emission_g_s)
    if not (isfinite(pm25) and isfinite(pm10)):
        raise ValueError("PM emission rates must be finite numbers")
    if pm25 < 0.0 or pm10 < 0.0:
        raise ValueError("PM emission rates must be non-negative")
    if pm10 + 1e-15 < pm25:
        raise ValueError(
            "total PM10 emission must be greater than or equal to PM2.5 emission"
        )


def split_pm_emissions(
    pm25_emission_g_s: float, pm10_total_emission_g_s: float
) -> tuple[float, float]:
    """Return non-overlapping ``(fine, coarse)`` emission rates in g/s."""

    validate_pm_emissions(pm25_emission_g_s, pm10_total_emission_g_s)
    fine = float(pm25_emission_g_s)
    coarse = max(0.0, float(pm10_total_emission_g_s) - fine)
    return fine, coarse


@dataclass(frozen=True, slots=True)
class EmissionRates:
    """Non-overlapping fine and coarse particulate rates in grams/second."""

    fine_g_s: float
    coarse_g_s: float

    def __post_init__(self) -> None:
        if not (isfinite(self.fine_g_s) and isfinite(self.coarse_g_s)):
            raise ValueError("emission rates must be finite")
        if self.fine_g_s < 0.0 or self.coarse_g_s < 0.0:
            raise ValueError("emission rates must be non-negative")

    @classmethod
    def from_pm25_pm10(cls, pm25_g_s: float, pm10_total_g_s: float) -> "EmissionRates":
        fine, coarse = split_pm_emissions(pm25_g_s, pm10_total_g_s)
        return cls(fine, coarse)

    @property
    def pm25_g_s(self) -> float:
        return self.fine_g_s

    @property
    def pm10_total_g_s(self) -> float:
        return self.fine_g_s + self.coarse_g_s

    def as_array(self) -> np.ndarray:
        return np.array([self.fine_g_s, self.coarse_g_s], dtype=float)


@dataclass(frozen=True, slots=True)
class EmissionSegment:
    """A half-open, constant-rate segment of a time-varying schedule."""

    start_s: float
    end_s: float | None
    rates: EmissionRates

    def __post_init__(self) -> None:
        if not isfinite(self.start_s):
            raise ValueError("emission segment start_s must be finite")
        if self.end_s is not None:
            if not isfinite(self.end_s) or self.end_s < self.start_s:
                raise ValueError("emission segment end_s must be >= start_s")

    @property
    def stop_s(self) -> float:
        return inf if self.end_s is None else self.end_s

    def overlap_s(self, start_s: float, end_s: float) -> float:
        return max(0.0, min(end_s, self.stop_s) - max(start_s, self.start_s))


@dataclass(slots=True)
class SourceModel:
    """Configurable point-source representation in local ENU coordinates."""

    name: str = "demo_stack"
    x_m: float = 0.0
    y_m: float = 0.0
    ground_z_m: float = 0.0
    latitude: float | None = None
    longitude: float | None = None
    ground_elevation_m: float = 0.0
    stack_height_m: float = 60.0
    plume_rise_m: float = 10.0
    stack_diameter_m: float = 2.0
    exit_velocity_mps: float = 12.0
    exhaust_temperature_k: float | None = None
    ambient_temperature_k: float | None = None
    pm25_emission_g_s: float = 0.20
    pm10_total_emission_g_s: float = 0.50
    emission_start_s: float = 0.0
    emission_end_s: float | None = None
    schedule: tuple[EmissionSegment, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        validate_pm_emissions(self.pm25_emission_g_s, self.pm10_total_emission_g_s)
        numeric_nonnegative = {
            "stack_height_m": self.stack_height_m,
            "plume_rise_m": self.plume_rise_m,
            "stack_diameter_m": self.stack_diameter_m,
            "exit_velocity_mps": self.exit_velocity_mps,
        }
        for label, raw in numeric_nonnegative.items():
            value = float(raw)
            if not isfinite(value) or value < 0.0:
                raise ValueError(f"{label} must be a finite non-negative value")
        if not isfinite(self.emission_start_s):
            raise ValueError("emission_start_s must be finite")
        if self.emission_end_s is not None:
            if not isfinite(self.emission_end_s) or self.emission_end_s < self.emission_start_s:
                raise ValueError("emission_end_s must be >= emission_start_s")
        ordered = sorted(self.schedule, key=lambda segment: segment.start_s)
        for previous, current in zip(ordered, ordered[1:]):
            if previous.stop_s > current.start_s:
                raise ValueError("time-varying emission schedule segments must not overlap")
        self.schedule = tuple(ordered)

    @classmethod
    def from_config(
        cls,
        config: Any,
        source_x_m: float = 0.0,
        source_y_m: float = 0.0,
        source_ground_z_m: float = 0.0,
    ) -> "SourceModel":
        """Construct a source from a root config or its ``source`` section."""

        section = _source_section(config)
        schedule_raw = list(
            _value(section, "emission_schedule", _value(section, "schedule", ())) or ()
        )
        schedule: list[EmissionSegment] = []
        if schedule_raw and any(_value(item, "time_s", None) is not None for item in schedule_raw):
            # Config.EmissionSchedulePoint describes step changes rather than
            # explicit intervals.  Preserve the source's base rate from its
            # emission start until the first step and cap all segments at the
            # source-level emission end.
            ordered = sorted(schedule_raw, key=lambda item: float(_value(item, "time_s", 0.0)))
            global_start = float(_value(section, "emission_start_s", 0.0))
            global_end = _optional_float(_value(section, "emission_end_s", None))
            first_time = float(_value(ordered[0], "time_s", 0.0))
            if first_time > global_start:
                base_pm25 = float(_value(section, "pm25_emission_g_s", 0.20))
                base_pm10 = float(_value(section, "pm10_total_emission_g_s", 0.50))
                schedule.append(
                    EmissionSegment(
                        global_start,
                        min(first_time, global_end) if global_end is not None else first_time,
                        EmissionRates.from_pm25_pm10(base_pm25, base_pm10),
                    )
                )
            for position, item in enumerate(ordered):
                start = max(global_start, float(_value(item, "time_s", 0.0)))
                next_time = (
                    float(_value(ordered[position + 1], "time_s", 0.0))
                    if position + 1 < len(ordered)
                    else global_end
                )
                end = next_time
                if global_end is not None and (end is None or end > global_end):
                    end = global_end
                if end is not None and end <= start:
                    continue
                pm25 = float(_value(item, "pm25_emission_g_s", _value(item, "pm25_g_s", 0.0)))
                pm10 = float(
                    _value(item, "pm10_total_emission_g_s", _value(item, "pm10_total_g_s", pm25))
                )
                schedule.append(
                    EmissionSegment(start, end, EmissionRates.from_pm25_pm10(pm25, pm10))
                )
        else:
            for item in schedule_raw:
                pm25 = float(_value(item, "pm25_emission_g_s", _value(item, "pm25_g_s", 0.0)))
                pm10 = float(
                    _value(item, "pm10_total_emission_g_s", _value(item, "pm10_total_g_s", pm25))
                )
                schedule.append(
                    EmissionSegment(
                        start_s=float(
                            _value(item, "start_s", _value(item, "emission_start_s", 0.0))
                        ),
                        end_s=_optional_float(
                            _value(item, "end_s", _value(item, "emission_end_s", None))
                        ),
                        rates=EmissionRates.from_pm25_pm10(pm25, pm10),
                    )
                )

        plume_rise = _value(section, "plume_rise_m", None)
        if plume_rise is None:
            plume_rise = _value(section, "fixed_plume_rise_m", 0.0)
        return cls(
            name=str(_value(section, "name", "demo_stack")),
            x_m=float(source_x_m),
            y_m=float(source_y_m),
            ground_z_m=float(source_ground_z_m),
            latitude=_optional_float(_value(section, "latitude", None)),
            longitude=_optional_float(_value(section, "longitude", None)),
            ground_elevation_m=float(_value(section, "ground_elevation_m", 0.0)),
            stack_height_m=float(_value(section, "stack_height_m", 60.0)),
            plume_rise_m=float(plume_rise),
            stack_diameter_m=float(_value(section, "stack_diameter_m", 2.0)),
            exit_velocity_mps=float(_value(section, "exit_velocity_mps", 12.0)),
            exhaust_temperature_k=_temperature_k(section, "exhaust_temperature"),
            ambient_temperature_k=_temperature_k(section, "ambient_temperature"),
            pm25_emission_g_s=float(_value(section, "pm25_emission_g_s", 0.20)),
            pm10_total_emission_g_s=float(
                _value(section, "pm10_total_emission_g_s", 0.50)
            ),
            emission_start_s=float(_value(section, "emission_start_s", 0.0)),
            emission_end_s=_optional_float(_value(section, "emission_end_s", None)),
            schedule=tuple(schedule),
        )

    @property
    def effective_release_height_m(self) -> float:
        """Stack-relative effective release height (stack plus prescribed rise)."""

        return self.stack_height_m + self.plume_rise_m

    @property
    def release_position_m(self) -> np.ndarray:
        return np.array(
            [self.x_m, self.y_m, self.ground_z_m + self.effective_release_height_m],
            dtype=float,
        )

    @property
    def coarse_emission_g_s(self) -> float:
        return max(0.0, self.pm10_total_emission_g_s - self.pm25_emission_g_s)

    @property
    def rates(self) -> EmissionRates:
        return EmissionRates(self.pm25_emission_g_s, self.coarse_emission_g_s)

    def rates_at(self, time_s: float) -> EmissionRates:
        """Return the active rate at ``time_s``; segment intervals are half-open."""

        time_s = float(time_s)
        if self.schedule:
            for segment in self.schedule:
                if segment.start_s <= time_s < segment.stop_s:
                    return segment.rates
            return EmissionRates(0.0, 0.0)
        stop = inf if self.emission_end_s is None else self.emission_end_s
        if self.emission_start_s <= time_s < stop:
            return self.rates
        return EmissionRates(0.0, 0.0)

    def emitted_mass(self, time_s: float, dt_s: float) -> tuple[float, float]:
        """Integrate exact fine/coarse mass over ``[time_s, time_s + dt_s)``."""

        start = float(time_s)
        dt = float(dt_s)
        if not isfinite(start) or not isfinite(dt) or dt < 0.0:
            raise ValueError("time_s must be finite and dt_s must be finite and non-negative")
        end = start + dt
        if self.schedule:
            mass = np.zeros(2, dtype=float)
            for segment in self.schedule:
                mass += segment.rates.as_array() * segment.overlap_s(start, end)
            return float(mass[0]), float(mass[1])
        segment = EmissionSegment(self.emission_start_s, self.emission_end_s, self.rates)
        overlap = segment.overlap_s(start, end)
        return self.pm25_emission_g_s * overlap, self.coarse_emission_g_s * overlap


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _temperature_k(section: Any, stem: str) -> float | None:
    kelvin = _value(section, f"{stem}_k", None)
    if kelvin is not None:
        return float(kelvin)
    celsius = _value(section, f"{stem}_c", None)
    if celsius is not None:
        return float(celsius) + 273.15
    return None


__all__ = [
    "EmissionRates",
    "EmissionSegment",
    "SourceModel",
    "split_pm_emissions",
    "validate_pm_emissions",
]
