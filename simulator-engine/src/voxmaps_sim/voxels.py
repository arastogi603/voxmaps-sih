"""Three-dimensional ENU voxel indexing and parcel-mass concentration sampling."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite
from typing import Any, Mapping, Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .dispersion import DomainBounds
from .particles import COARSE, FINE, ParticleSet


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _voxel_section(config: Any) -> Any:
    section = _value(config, "voxel", None)
    return config if section is None else section


@dataclass(frozen=True, slots=True)
class VoxelConcentration:
    """Exact-voxel parcel mass and derived ground-truth concentrations."""

    voxel_index: tuple[int, int, int]
    voxel_id: str | None
    fine_mass_g: float
    coarse_mass_g: float
    fine_parcel_count: int
    coarse_parcel_count: int
    pm25_true_ug_m3: float
    coarse_pm_true_ug_m3: float
    pm10_true_ug_m3: float
    quality_flags: tuple[str, ...] = ()

    @property
    def fine_count(self) -> int:
        return self.fine_parcel_count

    @property
    def coarse_count(self) -> int:
        return self.coarse_parcel_count

    def as_dict(self) -> dict[str, Any]:
        return {
            "voxel_x_index": self.voxel_index[0],
            "voxel_y_index": self.voxel_index[1],
            "voxel_z_index": self.voxel_index[2],
            "voxel_id": self.voxel_id,
            "fine_mass_g": self.fine_mass_g,
            "coarse_mass_g": self.coarse_mass_g,
            "fine_parcel_count": self.fine_parcel_count,
            "coarse_parcel_count": self.coarse_parcel_count,
            "pm25_true_ug_m3": self.pm25_true_ug_m3,
            "coarse_pm_true_ug_m3": self.coarse_pm_true_ug_m3,
            "pm10_true_ug_m3": self.pm10_true_ug_m3,
            "quality_flags": list(self.quality_flags),
        }


class VoxelGrid:
    """Regular axis-aligned grid with lower-inclusive/upper-exclusive cells."""

    def __init__(
        self,
        minimum_m: ArrayLike,
        maximum_m: ArrayLike,
        size_x_m: float = 20.0,
        size_y_m: float = 20.0,
        size_z_m: float = 10.0,
    ) -> None:
        minimum = np.asarray(minimum_m, dtype=float)
        maximum = np.asarray(maximum_m, dtype=float)
        sizes = np.array([size_x_m, size_y_m, size_z_m], dtype=float)
        if minimum.shape != (3,) or maximum.shape != (3,):
            raise ValueError("minimum_m and maximum_m must each be 3-vectors")
        if not np.all(np.isfinite(minimum)) or not np.all(np.isfinite(maximum)):
            raise ValueError("voxel grid bounds must be finite")
        if not np.all(np.isfinite(sizes)) or np.any(sizes <= 0.0):
            raise ValueError("voxel sizes must be finite and positive")
        if np.any(maximum <= minimum):
            raise ValueError("each voxel-grid maximum must exceed its minimum")
        shape = np.ceil((maximum - minimum) / sizes).astype(np.int64)
        self.minimum_m: NDArray[np.float64] = minimum
        self.size_m: NDArray[np.float64] = sizes
        self.shape: tuple[int, int, int] = tuple(map(int, shape))
        # Expand the supplied maximum by less than one cell if necessary so all
        # cells have identical volume and indexing remains stable.
        self.maximum_m: NDArray[np.float64] = minimum + shape * sizes

    @classmethod
    def from_bounds(
        cls,
        bounds_or_minimum: Any,
        maximum_m: ArrayLike | None = None,
        config: Any = None,
        *,
        size_x_m: float | None = None,
        size_y_m: float | None = None,
        size_z_m: float | None = None,
        padding_m: float = 0.0,
    ) -> "VoxelGrid":
        """Create from ``DomainBounds``, ``(min,max)``, or two 3-vectors."""

        if maximum_m is None:
            bounds = DomainBounds.from_value(bounds_or_minimum)
            minimum = bounds.minimum_m
            maximum = bounds.maximum_m
        else:
            minimum = np.asarray(bounds_or_minimum, dtype=float)
            maximum = np.asarray(maximum_m, dtype=float)
        if not np.all(np.isfinite(minimum)) or not np.all(np.isfinite(maximum)):
            raise ValueError("VoxelGrid requires finite domain bounds")
        pad = float(padding_m)
        if not isfinite(pad) or pad < 0.0:
            raise ValueError("padding_m must be finite and non-negative")
        minimum = np.asarray(minimum, dtype=float) - np.array([pad, pad, 0.0])
        maximum = np.asarray(maximum, dtype=float) + np.array([pad, pad, 0.0])
        section = _voxel_section({} if config is None else config)
        sx = float(size_x_m if size_x_m is not None else _value(section, "size_x_m", 20.0))
        sy = float(size_y_m if size_y_m is not None else _value(section, "size_y_m", 20.0))
        sz = float(size_z_m if size_z_m is not None else _value(section, "size_z_m", 10.0))
        return cls(minimum, maximum, sx, sy, sz)

    @property
    def size_x_m(self) -> float:
        return float(self.size_m[0])

    @property
    def size_y_m(self) -> float:
        return float(self.size_m[1])

    @property
    def size_z_m(self) -> float:
        return float(self.size_m[2])

    @property
    def voxel_volume_m3(self) -> float:
        return float(np.prod(self.size_m))

    @property
    def volume_m3(self) -> float:
        return self.voxel_volume_m3

    @property
    def voxel_count(self) -> int:
        return int(np.prod(self.shape, dtype=np.int64))

    @property
    def domain_bounds(self) -> DomainBounds:
        return DomainBounds(
            float(self.minimum_m[0]),
            float(self.maximum_m[0]),
            float(self.minimum_m[1]),
            float(self.maximum_m[1]),
            float(self.minimum_m[2]),
            float(self.maximum_m[2]),
        )

    def indices(self, positions_m: ArrayLike, outside_value: int = -1) -> NDArray[np.int64]:
        """Return ``(ix, iy, iz)`` rows; all components mark outside positions."""

        positions = np.asarray(positions_m, dtype=float)
        scalar = positions.ndim == 1
        if scalar:
            positions = positions.reshape(1, 3)
        if positions.ndim != 2 or positions.shape[1] != 3:
            raise ValueError("positions_m must have shape (3,) or (n, 3)")
        with np.errstate(invalid="ignore", divide="ignore"):
            raw = np.floor((positions - self.minimum_m) / self.size_m)
        finite = np.all(np.isfinite(raw), axis=1)
        result = np.full(raw.shape, int(outside_value), dtype=np.int64)
        if np.any(finite):
            safe = raw[finite].astype(np.int64)
            inside = np.all((safe >= 0) & (safe < np.asarray(self.shape)), axis=1)
            finite_rows = np.flatnonzero(finite)
            result[finite_rows[inside]] = safe[inside]
        return result[0] if scalar else result

    def index_of(
        self,
        position_or_x: ArrayLike | float,
        y_m: float | None = None,
        z_m: float | None = None,
    ) -> tuple[int, int, int] | None:
        if y_m is None and z_m is None:
            position = np.asarray(position_or_x, dtype=float)
        elif y_m is not None and z_m is not None:
            position = np.array([position_or_x, y_m, z_m], dtype=float)
        else:
            raise ValueError("provide either a 3-vector or x, y, and z")
        index = self.indices(position)
        if np.any(index < 0):
            return None
        return tuple(map(int, index))

    def is_valid_index(self, index: Sequence[int]) -> bool:
        array = np.asarray(index, dtype=np.int64)
        return array.shape == (3,) and bool(np.all((array >= 0) & (array < np.asarray(self.shape))))

    def flat_indices(self, indices: ArrayLike) -> NDArray[np.int64] | np.int64:
        values = np.asarray(indices, dtype=np.int64)
        scalar = values.ndim == 1
        if scalar:
            values = values.reshape(1, 3)
        if values.ndim != 2 or values.shape[1] != 3:
            raise ValueError("indices must have shape (3,) or (n, 3)")
        valid = np.all((values >= 0) & (values < np.asarray(self.shape)), axis=1)
        result = np.full(len(values), -1, dtype=np.int64)
        ny, nz = self.shape[1], self.shape[2]
        result[valid] = (values[valid, 0] * ny + values[valid, 1]) * nz + values[valid, 2]
        return result[0] if scalar else result

    def unravel_indices(self, flat_indices: ArrayLike) -> NDArray[np.int64]:
        flat = np.asarray(flat_indices, dtype=np.int64)
        if np.any((flat < 0) | (flat >= self.voxel_count)):
            raise ValueError("flat voxel index is outside the grid")
        ix, remainder = np.divmod(flat, self.shape[1] * self.shape[2])
        iy, iz = np.divmod(remainder, self.shape[2])
        return np.stack((ix, iy, iz), axis=-1).astype(np.int64)

    def voxel_id(self, index_or_x: Sequence[int] | int, y: int | None = None, z: int | None = None) -> str:
        if y is None and z is None:
            index = tuple(map(int, index_or_x))  # type: ignore[arg-type]
        elif y is not None and z is not None:
            index = (int(index_or_x), int(y), int(z))  # type: ignore[arg-type]
        else:
            raise ValueError("provide either a 3-index or x, y, and z indices")
        if not self.is_valid_index(index):
            raise ValueError("voxel index is outside the grid")
        return f"vx{index[0]:06d}_vy{index[1]:06d}_vz{index[2]:06d}"

    id_for_index = voxel_id

    def centre(self, index: Sequence[int]) -> NDArray[np.float64]:
        if not self.is_valid_index(index):
            raise ValueError("voxel index is outside the grid")
        return self.minimum_m + (np.asarray(index, dtype=float) + 0.5) * self.size_m

    center = centre

    def centres(self, indices: ArrayLike | None = None) -> NDArray[np.float64]:
        if indices is None:
            flat = np.arange(self.voxel_count, dtype=np.int64)
            values = self.unravel_indices(flat)
        else:
            values = np.asarray(indices, dtype=np.int64)
            if values.ndim == 1:
                values = values.reshape(1, 3)
            if values.ndim != 2 or values.shape[1] != 3:
                raise ValueError("indices must have shape (3,) or (n, 3)")
            if not np.all([self.is_valid_index(row) for row in values]):
                raise ValueError("one or more voxel indices are outside the grid")
        return self.minimum_m + (values.astype(float) + 0.5) * self.size_m

    centers = centres

    def boundaries(self, index: Sequence[int]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        if not self.is_valid_index(index):
            raise ValueError("voxel index is outside the grid")
        lower = self.minimum_m + np.asarray(index, dtype=float) * self.size_m
        return lower, lower + self.size_m

    voxel_boundaries = boundaries

    def concentration_from_mass(
        self,
        fine_mass_g: float,
        coarse_mass_g: float,
        *,
        background_pm25_ug_m3: float = 0.0,
        background_coarse_pm_ug_m3: float = 0.0,
    ) -> tuple[float, float, float]:
        """Return ``(PM2.5, coarse PM, total PM10)`` in micrograms/m3."""

        masses = np.array([fine_mass_g, coarse_mass_g], dtype=float)
        backgrounds = np.array([background_pm25_ug_m3, background_coarse_pm_ug_m3], dtype=float)
        if not np.all(np.isfinite(masses)) or np.any(masses < 0.0):
            raise ValueError("voxel masses must be finite and non-negative")
        if not np.all(np.isfinite(backgrounds)) or np.any(backgrounds < 0.0):
            raise ValueError("background concentrations must be finite and non-negative")
        concentrations = masses * 1_000_000.0 / self.voxel_volume_m3 + backgrounds
        return float(concentrations[FINE]), float(concentrations[COARSE]), float(concentrations.sum())

    def concentration(
        self,
        particles: ParticleSet,
        voxel_index: Sequence[int],
        *,
        background_pm25_ug_m3: float = 0.0,
        background_coarse_pm_ug_m3: float = 0.0,
    ) -> VoxelConcentration:
        """Sample exact parcel content in one voxel (smoothing is not applied)."""

        index = tuple(map(int, voxel_index))
        if not self.is_valid_index(index):
            raise ValueError("voxel index is outside the grid")
        parcel_indices = self.indices(particles.positions_m)
        mask = np.all(parcel_indices == np.asarray(index), axis=1)
        selected = particles.subset(mask)
        fine_mass, coarse_mass = selected.channel_masses_g()
        pm25, coarse_pm, pm10 = self.concentration_from_mass(
            fine_mass,
            coarse_mass,
            background_pm25_ug_m3=background_pm25_ug_m3,
            background_coarse_pm_ug_m3=background_coarse_pm_ug_m3,
        )
        counts = selected.channel_counts()
        return VoxelConcentration(
            index,
            self.voxel_id(index),
            float(fine_mass),
            float(coarse_mass),
            int(counts[FINE]),
            int(counts[COARSE]),
            pm25,
            coarse_pm,
            pm10,
        )

    def sample_position(
        self,
        particles: ParticleSet,
        position_m: ArrayLike,
        *,
        background_pm25_ug_m3: float = 0.0,
        background_coarse_pm_ug_m3: float = 0.0,
    ) -> VoxelConcentration:
        index = self.index_of(position_m)
        if index is None:
            return VoxelConcentration(
                (-1, -1, -1),
                None,
                0.0,
                0.0,
                0,
                0,
                float("nan"),
                float("nan"),
                float("nan"),
                ("out_of_domain_position",),
            )
        return self.concentration(
            particles,
            index,
            background_pm25_ug_m3=background_pm25_ug_m3,
            background_coarse_pm_ug_m3=background_coarse_pm_ug_m3,
        )

    def aggregate(
        self,
        particles: ParticleSet,
        *,
        include_empty: bool = False,
        background_pm25_ug_m3: float = 0.0,
        background_coarse_pm_ug_m3: float = 0.0,
    ) -> dict[str, NDArray[Any]]:
        """Vectorised per-voxel mass/concentration arrays for map export."""

        indices = self.indices(particles.positions_m)
        inside = np.all(indices >= 0, axis=1)
        flat = np.asarray(self.flat_indices(indices[inside]), dtype=np.int64)
        if include_empty:
            chosen = np.arange(self.voxel_count, dtype=np.int64)
        else:
            chosen = np.unique(flat)
        fine_mass = _weighted_voxel_sum(flat, particles.mass_g[inside], particles.channel[inside] == FINE, chosen)
        coarse_mass = _weighted_voxel_sum(flat, particles.mass_g[inside], particles.channel[inside] == COARSE, chosen)
        fine_count = _weighted_voxel_sum(flat, np.ones(len(flat)), particles.channel[inside] == FINE, chosen).astype(np.int64)
        coarse_count = _weighted_voxel_sum(flat, np.ones(len(flat)), particles.channel[inside] == COARSE, chosen).astype(np.int64)
        pm25 = fine_mass * 1_000_000.0 / self.voxel_volume_m3 + float(background_pm25_ug_m3)
        coarse_pm = coarse_mass * 1_000_000.0 / self.voxel_volume_m3 + float(background_coarse_pm_ug_m3)
        grid_indices = self.unravel_indices(chosen) if len(chosen) else np.empty((0, 3), dtype=np.int64)
        ids = np.array([self.voxel_id(row) for row in grid_indices], dtype=object)
        return {
            "flat_index": chosen,
            "indices": grid_indices,
            "voxel_id": ids,
            "centres_m": self.centres(grid_indices) if len(grid_indices) else np.empty((0, 3)),
            "fine_mass_g": fine_mass,
            "coarse_mass_g": coarse_mass,
            "fine_parcel_count": fine_count,
            "coarse_parcel_count": coarse_count,
            "pm25_true_ug_m3": pm25,
            "coarse_pm_true_ug_m3": coarse_pm,
            "pm10_true_ug_m3": pm25 + coarse_pm,
        }


def _weighted_voxel_sum(
    flat: NDArray[np.int64],
    values: NDArray[np.float64],
    channel_mask: NDArray[np.bool_],
    chosen: NDArray[np.int64],
) -> NDArray[np.float64]:
    if not len(chosen):
        return np.empty(0, dtype=float)
    selected_flat = flat[channel_mask]
    selected_values = values[channel_mask]
    if not len(selected_flat):
        return np.zeros(len(chosen), dtype=float)
    unique, inverse = np.unique(selected_flat, return_inverse=True)
    totals = np.bincount(inverse, weights=selected_values).astype(float)
    locations = np.searchsorted(unique, chosen)
    result = np.zeros(len(chosen), dtype=float)
    matches = (locations < len(unique)) & (unique[np.minimum(locations, len(unique) - 1)] == chosen)
    result[matches] = totals[locations[matches]]
    return result


__all__ = ["VoxelConcentration", "VoxelGrid"]
