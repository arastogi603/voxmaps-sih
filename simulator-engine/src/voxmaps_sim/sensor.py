"""Stateful virtual PM sensor with deterministic, configurable imperfections."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import exp, isfinite
from itertools import repeat
from typing import Any, Iterable, Mapping

import numpy as np


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _sensor_section(config: Any) -> Any:
    section = _value(config, "sensor", None)
    return config if section is None else section


def _first(obj: Any, names: tuple[str, ...], default: Any) -> Any:
    for name in names:
        result = _value(obj, name, None)
        if result is not None:
            return result
    return default


def _channel_value(section: Any, stem: str, channel: str, default: float) -> float:
    names = (
        f"{stem}_{channel}_ug_m3",
        f"{stem}_{channel}",
        f"{channel}_{stem}_ug_m3",
        f"{channel}_{stem}",
        f"{stem}_ug_m3",
        stem,
    )
    return float(_first(section, names, default))


@dataclass(frozen=True, slots=True)
class SensorReading:
    """Reported PM readings and quality flags for one trajectory timestamp."""

    time_s: float
    pm25_ug_m3: float
    pm10_ug_m3: float
    flags: tuple[str, ...] = ()

    @property
    def quality_flags(self) -> tuple[str, ...]:
        return self.flags

    @property
    def readings(self) -> dict[str, float]:
        return {
            "pm25_sensor_ug_m3": self.pm25_ug_m3,
            "pm10_sensor_ug_m3": self.pm10_ug_m3,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "time_s": self.time_s,
            **self.readings,
            "quality_flags": list(self.flags),
        }

    def __iter__(self):
        """Allow ``readings, flags = sensor.sample(...)`` for pipeline adapters."""

        yield self.readings
        yield self.flags


@dataclass(frozen=True, slots=True)
class _ChannelParameters:
    response_time_s: float
    additive_noise_std: float
    multiplicative_noise_std_fraction: float
    calibration_slope: float
    calibration_offset: float
    bias: float
    quantization: float
    lower_detection_limit: float
    upper_saturation_limit: float


class VirtualSensor:
    """Replay true PM through lag, delay, noise, calibration and limits.

    PM2.5 and PM10 share a simultaneous dropout decision but receive independent
    noise draws.  Internal lag state continues to evolve during a dropout.
    """

    def __init__(self, config: Any, seed: int | None = None) -> None:
        section = _sensor_section(config)
        project = _value(config, "project", config)
        resolved_seed = seed
        if resolved_seed is None:
            resolved_seed = int(_value(project, "random_seed", _value(config, "random_seed", 42)))
        self.seed = int(resolved_seed)
        self.rng = np.random.default_rng(self.seed)
        self.sample_interval_s = float(_value(section, "sample_interval_s", 1.0))
        self.delay_s = float(_first(section, ("delay_s", "transport_delay_s"), 0.0))
        self.dropout_probability = float(_value(section, "dropout_probability", 0.0))
        missing = _first(section, ("missing_data_output", "missing_value"), float("nan"))
        self.missing_data_output = float("nan") if missing is None else float(missing)
        self.allow_physically_inconsistent_raw_sensor_noise = bool(
            _first(
                section,
                (
                    "allow_physically_inconsistent_raw_sensor_noise",
                    "allow_pm_inconsistency",
                ),
                False,
            )
        )
        self.humidity_interference_enabled = bool(
            _value(section, "humidity_interference_enabled", False)
        )
        self.humidity_reference_pct = float(
            _first(section, ("humidity_reference_pct", "humidity_threshold_pct"), 60.0)
        )
        self.humidity_interference_fraction_per_pct = float(
            _first(
                section,
                (
                    "humidity_interference_fraction_per_pct",
                    "humidity_interference_coefficient_per_percent",
                    "humidity_coefficient_fraction_per_pct",
                ),
                0.005,
            )
        )
        self.rotor_wash_dilution_factor = float(
            _value(section, "rotor_wash_dilution_factor", 1.0)
        )
        self.initial_true_pm25_ug_m3 = float(_value(section, "initial_true_pm25_ug_m3", 0.0))
        self.initial_true_pm10_ug_m3 = float(_value(section, "initial_true_pm10_ug_m3", 0.0))
        self.pm25 = self._parameters(section, "pm25", 5.0)
        self.pm10 = self._parameters(section, "pm10", 5.0)
        self._validate()
        self._history: deque[tuple[float, float, float]] = deque()
        self._last_input_time_s: float | None = None
        self._last_update_time_s: float | None = None
        self._lag_pm25 = self.initial_true_pm25_ug_m3
        self._lag_pm10 = self.initial_true_pm10_ug_m3
        self._last_reading: SensorReading | None = None

    def _parameters(self, section: Any, channel: str, default_response_s: float) -> _ChannelParameters:
        response = float(
            _first(
                section,
                (f"response_time_{channel}_s", f"{channel}_response_time_s", "response_time_s"),
                default_response_s,
            )
        )
        return _ChannelParameters(
            response_time_s=response,
            additive_noise_std=_channel_value(section, "additive_noise_std", channel, 1.0),
            multiplicative_noise_std_fraction=float(
                _first(
                    section,
                    (
                        f"multiplicative_noise_std_fraction_{channel}",
                        f"{channel}_multiplicative_noise_std_fraction",
                        "multiplicative_noise_std_fraction",
                    ),
                    0.03,
                )
            ),
            calibration_slope=float(
                _first(section, (f"calibration_slope_{channel}", f"{channel}_calibration_slope", "calibration_slope"), 1.0)
            ),
            calibration_offset=_channel_value(section, "calibration_offset", channel, 0.0),
            bias=_channel_value(section, "bias", channel, 0.0),
            quantization=float(
                _first(
                    section,
                    (
                        f"quantization_{channel}_ug_m3",
                        f"quantisation_{channel}_ug_m3",
                        "quantization_ug_m3",
                        "quantisation_ug_m3",
                        "quantization",
                    ),
                    0.0,
                )
            ),
            lower_detection_limit=_channel_value(section, "lower_detection_limit", channel, 0.0),
            upper_saturation_limit=_channel_value(section, "upper_saturation_limit", channel, 1000.0),
        )

    def _validate(self) -> None:
        if not isfinite(self.sample_interval_s) or self.sample_interval_s <= 0.0:
            raise ValueError("sensor sample_interval_s must be finite and positive")
        if not isfinite(self.delay_s) or self.delay_s < 0.0:
            raise ValueError("sensor delay_s must be finite and non-negative")
        if not 0.0 <= self.dropout_probability <= 1.0:
            raise ValueError("sensor dropout_probability must be between 0 and 1")
        if not isfinite(self.rotor_wash_dilution_factor) or self.rotor_wash_dilution_factor < 0.0:
            raise ValueError("rotor_wash_dilution_factor must be finite and non-negative")
        if not isfinite(self.humidity_interference_fraction_per_pct):
            raise ValueError("humidity interference coefficient must be finite")
        if self.initial_true_pm10_ug_m3 < self.initial_true_pm25_ug_m3:
            raise ValueError("initial true PM10 must be greater than or equal to initial PM2.5")
        for name, parameters in (("pm25", self.pm25), ("pm10", self.pm10)):
            values = vars(parameters) if hasattr(parameters, "__dict__") else {
                field: getattr(parameters, field) for field in parameters.__slots__
            }
            if not all(isfinite(float(value)) for value in values.values()):
                raise ValueError(f"all {name} sensor parameters must be finite")
            if parameters.response_time_s < 0.0:
                raise ValueError(f"{name} response time must be non-negative")
            if parameters.additive_noise_std < 0.0 or parameters.multiplicative_noise_std_fraction < 0.0:
                raise ValueError(f"{name} noise standard deviations must be non-negative")
            if parameters.quantization < 0.0:
                raise ValueError(f"{name} quantization must be non-negative")
            if parameters.calibration_slope < 0.0:
                raise ValueError(f"{name} calibration slope must be non-negative")
            if parameters.upper_saturation_limit < parameters.lower_detection_limit:
                raise ValueError(f"{name} upper limit must be >= lower detection limit")

    def reset(self) -> None:
        self.rng = np.random.default_rng(self.seed)
        self._history.clear()
        self._last_input_time_s = None
        self._last_update_time_s = None
        self._lag_pm25 = self.initial_true_pm25_ug_m3
        self._lag_pm10 = self.initial_true_pm10_ug_m3
        self._last_reading = None

    def sample(
        self,
        time_s: float,
        true_pm25: float,
        true_pm10: float,
        rh_pct: float | None = None,
    ) -> SensorReading:
        """Return a sensor reading for a chronological ground-truth sample."""

        time = float(time_s)
        pm25_true = float(true_pm25)
        pm10_true = float(true_pm10)
        if not all(isfinite(value) for value in (time, pm25_true, pm10_true)):
            raise ValueError("sensor time and true concentrations must be finite")
        if pm25_true < 0.0 or pm10_true < 0.0:
            raise ValueError("true concentrations must be non-negative")
        if pm10_true + 1e-12 < pm25_true:
            raise ValueError("true PM10 must equal or exceed true PM2.5")
        if self._last_input_time_s is not None and time < self._last_input_time_s:
            raise ValueError("sensor samples must be supplied in chronological order")

        inlet_pm25, inlet_pm10 = self._inlet_values(pm25_true, pm10_true, rh_pct)
        self._history.append((time, inlet_pm25, inlet_pm10))
        self._last_input_time_s = time

        if (
            self._last_update_time_s is not None
            and time - self._last_update_time_s < self.sample_interval_s - 1e-12
        ):
            if self._last_reading is None:  # pragma: no cover - defensive invariant
                raise RuntimeError("sensor interval state is inconsistent")
            return SensorReading(
                time,
                self._last_reading.pm25_ug_m3,
                self._last_reading.pm10_ug_m3,
                tuple(dict.fromkeys((*self._last_reading.flags, "sensor_interval_hold"))),
            )

        target_pm25, target_pm10 = self._delayed_truth(time - self.delay_s)
        response_dt = (
            self.sample_interval_s
            if self._last_update_time_s is None
            else time - self._last_update_time_s
        )
        self._lag_pm25 = _lag(self._lag_pm25, target_pm25, response_dt, self.pm25.response_time_s)
        self._lag_pm10 = _lag(self._lag_pm10, target_pm10, response_dt, self.pm10.response_time_s)
        reported_pm25, flags25 = self._reported_value(self._lag_pm25, self.pm25, "pm25")
        reported_pm10, flags10 = self._reported_value(self._lag_pm10, self.pm10, "pm10")
        flags = [*flags25, *flags10]

        if not self.allow_physically_inconsistent_raw_sensor_noise and reported_pm10 < reported_pm25:
            reported_pm10 = reported_pm25
            flags.append("pm_consistency_enforced")

        if self.rng.random() < self.dropout_probability:
            reported_pm25 = self.missing_data_output
            reported_pm10 = self.missing_data_output
            flags.append("sensor_dropout")

        reading = SensorReading(
            time,
            float(reported_pm25),
            float(reported_pm10),
            tuple(dict.fromkeys(flags)),
        )
        self._last_update_time_s = time
        self._last_reading = reading
        return reading

    def _inlet_values(
        self, pm25_true: float, pm10_true: float, rh_pct: float | None
    ) -> tuple[float, float]:
        factor = self.rotor_wash_dilution_factor
        if self.humidity_interference_enabled and rh_pct is not None:
            humidity = float(rh_pct)
            if not isfinite(humidity):
                raise ValueError("relative humidity must be finite when supplied")
            excess = max(0.0, humidity - self.humidity_reference_pct)
            factor *= max(0.0, 1.0 + self.humidity_interference_fraction_per_pct * excess)
        return pm25_true * factor, pm10_true * factor

    def _delayed_truth(self, target_time_s: float) -> tuple[float, float]:
        if not self._history:
            return self.initial_true_pm25_ug_m3, self.initial_true_pm10_ug_m3
        while len(self._history) >= 3 and self._history[1][0] <= target_time_s:
            self._history.popleft()
        first = self._history[0]
        if target_time_s < first[0]:
            return self.initial_true_pm25_ug_m3, self.initial_true_pm10_ug_m3
        if len(self._history) == 1 or target_time_s >= self._history[-1][0]:
            last = self._history[-1]
            return last[1], last[2]
        second = self._history[1]
        if second[0] == first[0]:
            return second[1], second[2]
        fraction = (target_time_s - first[0]) / (second[0] - first[0])
        return (
            first[1] + fraction * (second[1] - first[1]),
            first[2] + fraction * (second[2] - first[2]),
        )

    def _reported_value(
        self, lagged_value: float, parameters: _ChannelParameters, channel: str
    ) -> tuple[float, list[str]]:
        noisy = lagged_value + parameters.bias
        noisy *= 1.0 + self.rng.normal(0.0, parameters.multiplicative_noise_std_fraction)
        noisy += self.rng.normal(0.0, parameters.additive_noise_std)
        calibrated = noisy * parameters.calibration_slope + parameters.calibration_offset
        if parameters.quantization > 0.0:
            calibrated = round(calibrated / parameters.quantization) * parameters.quantization
        flags: list[str] = []
        if calibrated < parameters.lower_detection_limit:
            calibrated = parameters.lower_detection_limit
            flags.append(f"sensor_below_detection_{channel}")
        if calibrated > parameters.upper_saturation_limit:
            calibrated = parameters.upper_saturation_limit
            flags.extend(("sensor_saturation", f"sensor_saturation_{channel}"))
        return float(calibrated), flags

    def sample_series(
        self,
        times_s: Iterable[float],
        true_pm25: Iterable[float],
        true_pm10: Iterable[float],
        rh_pct: Iterable[float | None] | None = None,
    ) -> list[SensorReading]:
        humidity = rh_pct if rh_pct is not None else repeat(None)
        return [self.sample(t, p25, p10, rh) for t, p25, p10, rh in zip(times_s, true_pm25, true_pm10, humidity)]


def _lag(previous: float, target: float, dt_s: float, response_time_s: float) -> float:
    if response_time_s <= 0.0:
        return target
    alpha = 1.0 - exp(-dt_s / response_time_s)
    return previous + alpha * (target - previous)


__all__ = ["SensorReading", "VirtualSensor"]
