"""Transparent vectorised Lagrangian parcel dispersion.

The model is intentionally an MVP rather than CFD: wind is spatially uniform,
turbulence is a seeded Gaussian random walk, settling is prescribed, and dry
deposition is represented as a configurable first-order loss plus ground
contact.  Every removal pathway is tracked separately for mass accounting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import exp, inf, isfinite
from typing import Any, Mapping

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .particles import COARSE, FINE, ParticleSet
from .source import SourceModel


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _section(config: Any, name: str) -> Any:
    value = _value(config, name, None)
    return config if value is None else value


def _first(obj: Any, names: tuple[str, ...], default: Any) -> Any:
    for name in names:
        value = _value(obj, name, None)
        if value is not None:
            return value
    return default


@dataclass(frozen=True, slots=True)
class DomainBounds:
    """Axis-aligned ENU domain with lower-inclusive, upper-exclusive bounds."""

    min_x_m: float
    max_x_m: float
    min_y_m: float
    max_y_m: float
    min_z_m: float
    max_z_m: float

    def __post_init__(self) -> None:
        for lo, hi, axis in (
            (self.min_x_m, self.max_x_m, "x"),
            (self.min_y_m, self.max_y_m, "y"),
            (self.min_z_m, self.max_z_m, "z"),
        ):
            if np.isnan(lo) or np.isnan(hi) or hi <= lo:
                raise ValueError(f"domain {axis} maximum must be greater than minimum")

    @classmethod
    def unbounded_above_ground(cls) -> "DomainBounds":
        return cls(-inf, inf, -inf, inf, 0.0, inf)

    @classmethod
    def from_value(cls, value: Any) -> "DomainBounds":
        if value is None:
            return cls.unbounded_above_ground()
        if isinstance(value, cls):
            return value
        if hasattr(value, "domain_bounds"):
            nested = value.domain_bounds
            if nested is not value:
                return cls.from_value(nested)
        if isinstance(value, Mapping) or hasattr(value, "min_x_m"):
            def get(names: tuple[str, ...], default: Any = None) -> float:
                return float(_first(value, names, default))

            return cls(
                get(("min_x_m", "x_min_m", "xmin")),
                get(("max_x_m", "x_max_m", "xmax")),
                get(("min_y_m", "y_min_m", "ymin")),
                get(("max_y_m", "y_max_m", "ymax")),
                get(("min_z_m", "z_min_m", "zmin"), 0.0),
                get(("max_z_m", "z_max_m", "zmax"), inf),
            )
        array = np.asarray(value, dtype=float)
        if array.shape == (2, 3):
            return cls(array[0, 0], array[1, 0], array[0, 1], array[1, 1], array[0, 2], array[1, 2])
        if array.shape == (6,):
            # Explicit order is xmin, xmax, ymin, ymax, zmin, zmax.
            return cls(*map(float, array))
        raise ValueError("domain_bounds must be DomainBounds, a bounds mapping, (2,3), or 6 values")

    @property
    def minimum_m(self) -> NDArray[np.float64]:
        return np.array([self.min_x_m, self.min_y_m, self.min_z_m], dtype=float)

    @property
    def maximum_m(self) -> NDArray[np.float64]:
        return np.array([self.max_x_m, self.max_y_m, self.max_z_m], dtype=float)

    def contains(self, positions_m: ArrayLike) -> NDArray[np.bool_]:
        positions = np.asarray(positions_m, dtype=float)
        return np.all((positions >= self.minimum_m) & (positions < self.maximum_m), axis=-1)


def _zeros2() -> NDArray[np.float64]:
    return np.zeros(2, dtype=float)


@dataclass(slots=True)
class MassBalance:
    """Cumulative fine/coarse mass ledger, in grams."""

    emitted_g: NDArray[np.float64] = field(default_factory=_zeros2)
    deposited_g: NDArray[np.float64] = field(default_factory=_zeros2)
    exited_g: NDArray[np.float64] = field(default_factory=_zeros2)
    decay_removed_g: NDArray[np.float64] = field(default_factory=_zeros2)

    def reset(self) -> None:
        self.emitted_g.fill(0.0)
        self.deposited_g.fill(0.0)
        self.exited_g.fill(0.0)
        self.decay_removed_g.fill(0.0)

    def error_g(self, airborne_g: ArrayLike) -> NDArray[np.float64]:
        airborne = np.asarray(airborne_g, dtype=float)
        return self.emitted_g - self.deposited_g - self.exited_g - self.decay_removed_g - airborne

    def as_dict(self, airborne_g: ArrayLike) -> dict[str, float]:
        airborne = np.asarray(airborne_g, dtype=float)
        error = self.error_g(airborne)
        return {
            "total_emitted_fine_mass_g": float(self.emitted_g[FINE]),
            "total_emitted_coarse_mass_g": float(self.emitted_g[COARSE]),
            "airborne_fine_mass_g": float(airborne[FINE]),
            "airborne_coarse_mass_g": float(airborne[COARSE]),
            "deposited_fine_mass_g": float(self.deposited_g[FINE]),
            "deposited_coarse_mass_g": float(self.deposited_g[COARSE]),
            "deposited_mass_g": float(self.deposited_g.sum()),
            "exited_fine_mass_g": float(self.exited_g[FINE]),
            "exited_coarse_mass_g": float(self.exited_g[COARSE]),
            "exited_domain_mass_g": float(self.exited_g.sum()),
            "decay_removed_fine_mass_g": float(self.decay_removed_g[FINE]),
            "decay_removed_coarse_mass_g": float(self.decay_removed_g[COARSE]),
            "removed_by_decay_mass_g": float(self.decay_removed_g.sum()),
            "numerical_mass_balance_error_fine_g": float(error[FINE]),
            "numerical_mass_balance_error_coarse_g": float(error[COARSE]),
            "numerical_mass_balance_error_g": float(error.sum()),
        }


@dataclass(frozen=True, slots=True)
class DispersionStepResult:
    """Summary of a completed vectorised step."""

    particle_count: int
    airborne_g: tuple[float, float]
    deposited_g: tuple[float, float]
    exited_g: tuple[float, float]
    decay_removed_g: tuple[float, float]
    step_mass_balance_error_g: tuple[float, float]
    grounded_particles: ParticleSet = field(default_factory=ParticleSet.empty, repr=False, compare=False)
    exited_particles: ParticleSet = field(default_factory=ParticleSet.empty, repr=False, compare=False)


class DispersionModel:
    """Own parcel state and advance it with advection/diffusion/settling."""

    def __init__(
        self,
        config: Any,
        domain_bounds: Any = None,
        seed: int | None = None,
    ) -> None:
        dispersion = _section(config, "dispersion")
        simulation = _section(config, "simulation")
        project = _section(config, "project")
        self.horizontal_diffusivity_m2_s = float(
            _value(dispersion, "horizontal_diffusivity_m2_s", 5.0)
        )
        self.vertical_diffusivity_m2_s = float(
            _value(dispersion, "vertical_diffusivity_m2_s", 1.0)
        )
        self.fine_settling_velocity_mps = float(
            _value(dispersion, "fine_settling_velocity_mps", 0.001)
        )
        self.coarse_settling_velocity_mps = float(
            _value(dispersion, "coarse_settling_velocity_mps", 0.02)
        )
        self.fine_deposition_velocity_mps = float(
            _value(dispersion, "fine_deposition_velocity_mps", 0.001)
        )
        self.coarse_deposition_velocity_mps = float(
            _value(dispersion, "coarse_deposition_velocity_mps", 0.01)
        )
        self.deposition_reference_height_m = float(
            _value(dispersion, "deposition_reference_height_m", 10.0)
        )
        self.fine_decay_rate_s = float(_value(dispersion, "fine_decay_rate_s", 0.0))
        self.coarse_decay_rate_s = float(_value(dispersion, "coarse_decay_rate_s", 0.0))
        self.release_jitter_std_m = float(_value(dispersion, "release_jitter_std_m", 0.0))
        self.particles_per_timestep = int(_value(simulation, "particles_per_timestep", 50))
        self.maximum_particles = int(_value(simulation, "maximum_particles", 500_000))
        resolved_seed = seed
        if resolved_seed is None:
            resolved_seed = int(_value(project, "random_seed", _value(config, "random_seed", 42)))
        self.seed = int(resolved_seed)
        self.rng = np.random.default_rng(self.seed)
        self.domain = DomainBounds.from_value(domain_bounds)
        self.particles = ParticleSet.empty()
        self.emitted_particles = ParticleSet.empty()
        self.deposited_particles = ParticleSet.empty()
        self.exited_particles = ParticleSet.empty()
        self._next_parcel_id = 0
        self.mass = MassBalance()
        self._validate_parameters()

    def _validate_parameters(self) -> None:
        nonnegative = {
            "horizontal_diffusivity_m2_s": self.horizontal_diffusivity_m2_s,
            "vertical_diffusivity_m2_s": self.vertical_diffusivity_m2_s,
            "fine_settling_velocity_mps": self.fine_settling_velocity_mps,
            "coarse_settling_velocity_mps": self.coarse_settling_velocity_mps,
            "fine_deposition_velocity_mps": self.fine_deposition_velocity_mps,
            "coarse_deposition_velocity_mps": self.coarse_deposition_velocity_mps,
            "fine_decay_rate_s": self.fine_decay_rate_s,
            "coarse_decay_rate_s": self.coarse_decay_rate_s,
            "release_jitter_std_m": self.release_jitter_std_m,
        }
        for label, value in nonnegative.items():
            if not isfinite(value) or value < 0.0:
                raise ValueError(f"{label} must be finite and non-negative")
        if not isfinite(self.deposition_reference_height_m) or self.deposition_reference_height_m <= 0:
            raise ValueError("deposition_reference_height_m must be finite and positive")
        if self.particles_per_timestep <= 0:
            raise ValueError("particles_per_timestep must be positive")
        if self.maximum_particles < 2:
            raise ValueError("maximum_particles must be at least 2")

    @property
    def mass_balance(self) -> MassBalance:
        return self.mass

    def reset(self) -> None:
        self.particles.clear()
        self.emitted_particles.clear()
        self.deposited_particles.clear()
        self.exited_particles.clear()
        self._next_parcel_id = 0
        self.mass.reset()
        self.rng = np.random.default_rng(self.seed)

    def emit(
        self,
        source: SourceModel,
        time_s: float,
        dt_s: float,
        parcels_per_timestep: int | None = None,
    ) -> ParticleSet:
        """Emit parcels for a timestep and add exact emitted mass to the ledger."""

        fine_mass, coarse_mass = source.emitted_mass(time_s, dt_s)
        if fine_mass == 0.0 and coarse_mass == 0.0:
            return ParticleSet.empty()
        requested = self.particles_per_timestep if parcels_per_timestep is None else int(parcels_per_timestep)
        available = self.maximum_particles - len(self.particles)
        active_channels = int(fine_mass > 0.0) + int(coarse_mass > 0.0)
        new_count = min(requested, max(available, 0))
        if new_count < active_channels:
            if self._merge_limited_emission(source.release_position_m, fine_mass, coarse_mass):
                self.mass.emitted_g += np.array([fine_mass, coarse_mass])
                return ParticleSet.empty()
            # A brand-new model needs one parcel per active channel even when a
            # caller configured an unusably tiny cap.  Constructor validation
            # makes this reachable only with a manually replaced state.
            new_count = active_channels
        emitted = ParticleSet.from_emission(
            source.release_position_m,
            fine_mass,
            coarse_mass,
            new_count,
            rng=self.rng,
            release_jitter_std_m=self.release_jitter_std_m,
            parcel_ids=np.arange(
                self._next_parcel_id,
                self._next_parcel_id + new_count,
                dtype=np.int64,
            ),
            emission_time_s=float(time_s),
            source_id=source.name,
        )
        self._next_parcel_id += len(emitted)
        self.particles.append(emitted)
        self.emitted_particles.append(emitted)
        self.mass.emitted_g += emitted.channel_masses_g()
        return emitted

    def _merge_limited_emission(
        self, release_position_m: ArrayLike, fine_mass_g: float, coarse_mass_g: float
    ) -> bool:
        """Mass-weight new emissions into existing same-channel parcels at the cap."""

        release = np.asarray(release_position_m, dtype=float)
        targets: list[tuple[int, float]] = []
        for channel, added in ((FINE, fine_mass_g), (COARSE, coarse_mass_g)):
            if added <= 0.0:
                continue
            matching = np.flatnonzero(self.particles.channel == channel)
            if not matching.size:
                return False
            targets.append((int(matching[-1]), added))
        for index, added in targets:
            old = self.particles.mass_g[index]
            total = old + added
            self.particles.positions_m[index] = (
                self.particles.positions_m[index] * old + release * added
            ) / total
            self.particles.mass_g[index] = total
            self.particles.initial_mass_g[index] += added
            self.particles.age_s[index] = min(self.particles.age_s[index], 0.0)
            parcel_id = self.particles.parcel_id[index]
            registry_index = np.flatnonzero(self.emitted_particles.parcel_id == parcel_id)
            if registry_index.size:
                self.emitted_particles.initial_mass_g[registry_index[-1]] += added
                self.emitted_particles.mass_g[registry_index[-1]] += added
        return True

    def step(
        self,
        wind_east_mps: float,
        wind_north_mps: float,
        dt_s: float,
        wind_vertical_mps: float = 0.0,
    ) -> DispersionStepResult:
        """Advance every airborne parcel once using fully vectorised arrays."""

        dt = float(dt_s)
        wind = np.array([wind_east_mps, wind_north_mps, wind_vertical_mps], dtype=float)
        if not isfinite(dt) or dt <= 0.0:
            raise ValueError("dt_s must be finite and positive")
        if not np.all(np.isfinite(wind)):
            raise ValueError("wind components must be finite")
        if not len(self.particles):
            zero = (0.0, 0.0)
            return DispersionStepResult(0, zero, zero, zero, zero, zero)

        start_g = self.particles.channel_masses_g()
        channels = self.particles.channel
        scales = np.array(
            [
                np.sqrt(2.0 * self.horizontal_diffusivity_m2_s * dt),
                np.sqrt(2.0 * self.horizontal_diffusivity_m2_s * dt),
                np.sqrt(2.0 * self.vertical_diffusivity_m2_s * dt),
            ]
        )
        random_walk = self.rng.normal(size=self.particles.positions_m.shape) * scales
        settling = np.where(
            channels == FINE,
            self.fine_settling_velocity_mps,
            self.coarse_settling_velocity_mps,
        )
        self.particles.positions_m += wind * dt + random_walk
        self.particles.positions_m[:, 2] -= settling * dt
        self.particles.age_s += dt

        deposition_velocity = np.where(
            channels == FINE,
            self.fine_deposition_velocity_mps,
            self.coarse_deposition_velocity_mps,
        )
        dep_fraction = -np.expm1(-deposition_velocity * dt / self.deposition_reference_height_m)
        dep_loss = self.particles.mass_g * dep_fraction
        self.particles.mass_g -= dep_loss
        deposited_g = _sum_by_channel(channels, dep_loss)

        decay_rates = np.where(channels == FINE, self.fine_decay_rate_s, self.coarse_decay_rate_s)
        decay_fraction = -np.expm1(-decay_rates * dt)
        decay_loss = self.particles.mass_g * decay_fraction
        self.particles.mass_g -= decay_loss
        decay_removed_g = _sum_by_channel(channels, decay_loss)

        grounded = ParticleSet.empty()
        ground_contact = self.particles.positions_m[:, 2] < self.domain.min_z_m
        if np.any(ground_contact):
            grounded = self.particles.remove(ground_contact)
            grounded.positions_m[:, 2] = self.domain.min_z_m
            self.deposited_particles.append(grounded)
            contact_g = grounded.channel_masses_g()
            deposited_g += contact_g

        outside = ~self.domain.contains(self.particles.positions_m)
        exited = ParticleSet.empty()
        if np.any(outside):
            exited = self.particles.remove(outside)
            self.exited_particles.append(exited)
            exited_g = exited.channel_masses_g()
        else:
            exited_g = np.zeros(2, dtype=float)

        airborne_g = self.particles.channel_masses_g()
        step_error = start_g - deposited_g - decay_removed_g - exited_g - airborne_g
        # Roundoff at this scale is expected, but a material negative mass is not.
        if np.any(self.particles.mass_g < -1e-14):
            raise RuntimeError("dispersion step created negative parcel mass")
        self.particles.mass_g[self.particles.mass_g < 0.0] = 0.0
        self.mass.deposited_g += deposited_g
        self.mass.exited_g += exited_g
        self.mass.decay_removed_g += decay_removed_g
        return DispersionStepResult(
            len(self.particles),
            tuple(map(float, airborne_g)),
            tuple(map(float, deposited_g)),
            tuple(map(float, exited_g)),
            tuple(map(float, decay_removed_g)),
            tuple(map(float, step_error)),
            grounded,
            exited,
        )

    advance = step

    def mass_balance_report(self) -> dict[str, float]:
        return self.mass.as_dict(self.particles.channel_masses_g())


def _sum_by_channel(channels: NDArray[np.uint8], values: NDArray[np.float64]) -> NDArray[np.float64]:
    if values.size == 0:
        return np.zeros(2, dtype=float)
    return np.bincount(channels, weights=values, minlength=2).astype(float)


__all__ = [
    "DispersionModel",
    "DispersionStepResult",
    "DomainBounds",
    "MassBalance",
]
