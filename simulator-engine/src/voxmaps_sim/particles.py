"""Vectorised computational particle-parcel storage.

Each row is a *computational parcel* representing a finite particulate mass;
it is not an individual dust particle.  Fine and coarse masses are kept in
non-overlapping channels so PM10 can always be obtained without double count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable

import numpy as np
from numpy.typing import ArrayLike, NDArray


class ParticleChannel(IntEnum):
    FINE = 0
    COARSE = 1


FINE = int(ParticleChannel.FINE)
COARSE = int(ParticleChannel.COARSE)


def _empty_positions() -> NDArray[np.float64]:
    return np.empty((0, 3), dtype=np.float64)


def _empty_float() -> NDArray[np.float64]:
    return np.empty(0, dtype=np.float64)


def _empty_channel() -> NDArray[np.uint8]:
    return np.empty(0, dtype=np.uint8)


def _empty_int64() -> NDArray[np.int64]:
    return np.empty(0, dtype=np.int64)


def _empty_source_id() -> NDArray[np.str_]:
    return np.empty(0, dtype="U64")


@dataclass(slots=True)
class ParticleSet:
    """Structure-of-arrays parcel state designed for vectorised operations."""

    positions_m: NDArray[np.float64] = field(default_factory=_empty_positions)
    mass_g: NDArray[np.float64] = field(default_factory=_empty_float)
    channel: NDArray[np.uint8] = field(default_factory=_empty_channel)
    age_s: NDArray[np.float64] = field(default_factory=_empty_float)
    parcel_id: NDArray[np.int64] = field(default_factory=_empty_int64)
    emission_time_s: NDArray[np.float64] = field(default_factory=_empty_float)
    source_id: NDArray[np.str_] = field(default_factory=_empty_source_id)
    initial_mass_g: NDArray[np.float64] = field(default_factory=_empty_float)

    def __post_init__(self) -> None:
        self.positions_m = np.asarray(self.positions_m, dtype=np.float64)
        self.mass_g = np.asarray(self.mass_g, dtype=np.float64)
        self.channel = np.asarray(self.channel, dtype=np.uint8)
        self.age_s = np.asarray(self.age_s, dtype=np.float64)
        self.parcel_id = np.asarray(self.parcel_id, dtype=np.int64)
        self.emission_time_s = np.asarray(self.emission_time_s, dtype=np.float64)
        self.source_id = np.asarray(self.source_id, dtype="U64")
        self.initial_mass_g = np.asarray(self.initial_mass_g, dtype=np.float64)
        if self.positions_m.ndim != 2 or self.positions_m.shape[1:] != (3,):
            raise ValueError("positions_m must have shape (n, 3)")
        n = self.positions_m.shape[0]
        if self.mass_g.shape != (n,) or self.channel.shape != (n,):
            raise ValueError("mass_g and channel must have one value per position")
        if self.age_s.size == 0 and n:
            self.age_s = np.zeros(n, dtype=np.float64)
        if self.age_s.shape != (n,):
            raise ValueError("age_s must have one value per position")
        if self.parcel_id.size == 0 and n:
            self.parcel_id = np.full(n, -1, dtype=np.int64)
        if self.emission_time_s.size == 0 and n:
            self.emission_time_s = np.full(n, np.nan, dtype=np.float64)
        if self.source_id.size == 0 and n:
            self.source_id = np.full(n, "", dtype="U64")
        if self.initial_mass_g.size == 0 and n:
            self.initial_mass_g = self.mass_g.copy()
        for name, values in (
            ("parcel_id", self.parcel_id),
            ("emission_time_s", self.emission_time_s),
            ("source_id", self.source_id),
            ("initial_mass_g", self.initial_mass_g),
        ):
            if values.shape != (n,):
                raise ValueError(f"{name} must have one value per position")
        if not np.all(np.isfinite(self.positions_m)):
            raise ValueError("particle positions must be finite")
        if not np.all(np.isfinite(self.mass_g)) or np.any(self.mass_g < 0.0):
            raise ValueError("particle masses must be finite and non-negative")
        if not np.all(np.isin(self.channel, (FINE, COARSE))):
            raise ValueError("particle channel values must be FINE (0) or COARSE (1)")
        if not np.all(np.isfinite(self.age_s)) or np.any(self.age_s < 0.0):
            raise ValueError("particle ages must be finite and non-negative")
        if not np.all(np.isfinite(self.initial_mass_g)) or np.any(self.initial_mass_g < 0.0):
            raise ValueError("particle initial masses must be finite and non-negative")

    @classmethod
    def empty(cls) -> "ParticleSet":
        return cls()

    @classmethod
    def from_arrays(
        cls,
        positions_m: ArrayLike,
        mass_g: ArrayLike,
        channel: ArrayLike,
        age_s: ArrayLike | None = None,
        *,
        parcel_id: ArrayLike | None = None,
        emission_time_s: ArrayLike | None = None,
        source_id: ArrayLike | None = None,
        initial_mass_g: ArrayLike | None = None,
    ) -> "ParticleSet":
        positions = np.asarray(positions_m, dtype=np.float64)
        n = 1 if positions.ndim == 1 else len(positions)
        if positions.ndim == 1:
            positions = positions.reshape(1, 3)
        ages = np.zeros(n, dtype=float) if age_s is None else np.asarray(age_s, dtype=float)
        masses = np.asarray(mass_g)
        return cls(
            positions,
            masses,
            np.asarray(channel),
            ages,
            _empty_int64() if parcel_id is None else np.asarray(parcel_id),
            _empty_float() if emission_time_s is None else np.asarray(emission_time_s),
            _empty_source_id() if source_id is None else np.asarray(source_id),
            masses.copy() if initial_mass_g is None else np.asarray(initial_mass_g),
        )

    @classmethod
    def from_emission(
        cls,
        release_position_m: ArrayLike,
        fine_mass_g: float,
        coarse_mass_g: float,
        parcel_count: int,
        *,
        rng: np.random.Generator | None = None,
        release_jitter_std_m: float = 0.0,
        parcel_ids: ArrayLike | None = None,
        emission_time_s: float | ArrayLike | None = None,
        source_id: str | ArrayLike = "",
    ) -> "ParticleSet":
        """Split emitted mass exactly among a requested total parcel count.

        At least one parcel is allocated to every non-zero channel.  If both
        channels are non-zero and ``parcel_count`` is one, two parcels are used
        because a parcel cannot belong to two overlapping mass channels.
        """

        fine_mass = float(fine_mass_g)
        coarse_mass = float(coarse_mass_g)
        count = int(parcel_count)
        if count < 0:
            raise ValueError("parcel_count must be non-negative")
        if not np.isfinite(fine_mass) or not np.isfinite(coarse_mass):
            raise ValueError("emitted masses must be finite")
        if fine_mass < 0.0 or coarse_mass < 0.0:
            raise ValueError("emitted masses must be non-negative")
        active = np.array([fine_mass > 0.0, coarse_mass > 0.0])
        n_active = int(active.sum())
        if n_active == 0:
            return cls.empty()
        if count == 0:
            raise ValueError("parcel_count must be positive when emitted mass is non-zero")
        count = max(count, n_active)
        allocations = _allocate_counts(np.array([fine_mass, coarse_mass]), count, active)
        channels = np.repeat(np.array([FINE, COARSE], dtype=np.uint8), allocations)
        masses = np.concatenate(
            [
                np.full(allocations[FINE], fine_mass / allocations[FINE], dtype=float)
                if allocations[FINE]
                else np.empty(0),
                np.full(allocations[COARSE], coarse_mass / allocations[COARSE], dtype=float)
                if allocations[COARSE]
                else np.empty(0),
            ]
        )
        release = np.asarray(release_position_m, dtype=float)
        if release.shape != (3,) or not np.all(np.isfinite(release)):
            raise ValueError("release_position_m must be a finite 3-vector")
        positions = np.repeat(release[None, :], count, axis=0)
        jitter = float(release_jitter_std_m)
        if jitter < 0.0 or not np.isfinite(jitter):
            raise ValueError("release_jitter_std_m must be finite and non-negative")
        if jitter:
            generator = rng if rng is not None else np.random.default_rng()
            positions += generator.normal(0.0, jitter, size=(count, 3))
        ids = (
            np.full(count, -1, dtype=np.int64)
            if parcel_ids is None
            else np.asarray(parcel_ids, dtype=np.int64)
        )
        if emission_time_s is None:
            emitted_at = np.full(count, np.nan, dtype=float)
        else:
            emitted_at = np.broadcast_to(
                np.asarray(emission_time_s, dtype=float), (count,)
            ).copy()
        sources = np.broadcast_to(np.asarray(source_id, dtype="U64"), (count,)).copy()
        return cls(
            positions,
            masses,
            channels,
            np.zeros(count, dtype=float),
            ids,
            emitted_at,
            sources,
            masses.copy(),
        )

    def __len__(self) -> int:
        return int(self.mass_g.size)

    @property
    def positions(self) -> NDArray[np.float64]:
        return self.positions_m

    @property
    def masses_g(self) -> NDArray[np.float64]:
        return self.mass_g

    @property
    def channels(self) -> NDArray[np.uint8]:
        return self.channel

    @property
    def fine_mask(self) -> NDArray[np.bool_]:
        return self.channel == FINE

    @property
    def coarse_mask(self) -> NDArray[np.bool_]:
        return self.channel == COARSE

    @property
    def fine_mass_g(self) -> float:
        return float(self.mass_g[self.fine_mask].sum(dtype=float))

    @property
    def coarse_mass_g(self) -> float:
        return float(self.mass_g[self.coarse_mask].sum(dtype=float))

    @property
    def pm10_total_mass_g(self) -> float:
        return self.fine_mass_g + self.coarse_mass_g

    @property
    def total_mass_g(self) -> float:
        return float(self.mass_g.sum(dtype=float))

    @property
    def fine_count(self) -> int:
        return int(np.count_nonzero(self.fine_mask))

    @property
    def coarse_count(self) -> int:
        return int(np.count_nonzero(self.coarse_mask))

    def channel_masses_g(self) -> NDArray[np.float64]:
        if not len(self):
            return np.zeros(2, dtype=float)
        return np.bincount(self.channel, weights=self.mass_g, minlength=2).astype(float)

    def channel_counts(self) -> NDArray[np.int64]:
        if not len(self):
            return np.zeros(2, dtype=np.int64)
        return np.bincount(self.channel, minlength=2).astype(np.int64)

    def append(self, other: "ParticleSet") -> None:
        """Append parcels in-place, preserving exact mass values."""

        if not len(other):
            return
        if not len(self):
            self.positions_m = other.positions_m.copy()
            self.mass_g = other.mass_g.copy()
            self.channel = other.channel.copy()
            self.age_s = other.age_s.copy()
            self.parcel_id = other.parcel_id.copy()
            self.emission_time_s = other.emission_time_s.copy()
            self.source_id = other.source_id.copy()
            self.initial_mass_g = other.initial_mass_g.copy()
            return
        self.positions_m = np.concatenate((self.positions_m, other.positions_m), axis=0)
        self.mass_g = np.concatenate((self.mass_g, other.mass_g))
        self.channel = np.concatenate((self.channel, other.channel))
        self.age_s = np.concatenate((self.age_s, other.age_s))
        self.parcel_id = np.concatenate((self.parcel_id, other.parcel_id))
        self.emission_time_s = np.concatenate((self.emission_time_s, other.emission_time_s))
        self.source_id = np.concatenate((self.source_id, other.source_id))
        self.initial_mass_g = np.concatenate((self.initial_mass_g, other.initial_mass_g))

    extend = append

    def subset(self, selector: ArrayLike) -> "ParticleSet":
        selection = selector if isinstance(selector, slice) else np.asarray(selector)
        return ParticleSet(
            self.positions_m[selection].copy(),
            self.mass_g[selection].copy(),
            self.channel[selection].copy(),
            self.age_s[selection].copy(),
            self.parcel_id[selection].copy(),
            self.emission_time_s[selection].copy(),
            self.source_id[selection].copy(),
            self.initial_mass_g[selection].copy(),
        )

    def remove(self, remove_mask: ArrayLike) -> "ParticleSet":
        """Remove selected parcels in-place and return their former state."""

        mask = np.asarray(remove_mask, dtype=bool)
        if mask.shape != (len(self),):
            raise ValueError("remove_mask must have one boolean per particle")
        removed = self.subset(mask)
        keep = ~mask
        self.positions_m = self.positions_m[keep]
        self.mass_g = self.mass_g[keep]
        self.channel = self.channel[keep]
        self.age_s = self.age_s[keep]
        self.parcel_id = self.parcel_id[keep]
        self.emission_time_s = self.emission_time_s[keep]
        self.source_id = self.source_id[keep]
        self.initial_mass_g = self.initial_mass_g[keep]
        return removed

    def copy(self) -> "ParticleSet":
        return self.subset(slice(None))

    def clear(self) -> None:
        self.positions_m = _empty_positions()
        self.mass_g = _empty_float()
        self.channel = _empty_channel()
        self.age_s = _empty_float()
        self.parcel_id = _empty_int64()
        self.emission_time_s = _empty_float()
        self.source_id = _empty_source_id()
        self.initial_mass_g = _empty_float()


def _allocate_counts(masses: NDArray[np.float64], count: int, active: NDArray[np.bool_]) -> NDArray[np.int64]:
    allocations = active.astype(np.int64)
    remaining = count - int(allocations.sum())
    if remaining <= 0:
        return allocations
    weights = np.where(active, masses, 0.0)
    if weights.sum() <= 0.0:
        weights = active.astype(float)
    ideal = remaining * weights / weights.sum()
    base = np.floor(ideal).astype(np.int64)
    allocations += base
    left = remaining - int(base.sum())
    if left:
        remainders = ideal - base
        order = np.argsort(-remainders, kind="stable")
        allocations[order[:left]] += 1
    return allocations


# Descriptive alias used in documentation and convenient downstream imports.
ParticleParcels = ParticleSet


__all__ = [
    "COARSE",
    "FINE",
    "ParticleChannel",
    "ParticleParcels",
    "ParticleSet",
]
