"""Local East-North-Up (ENU) coordinates backed by PyProj WGS84 transforms."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from pyproj import Transformer


@dataclass(frozen=True, slots=True)
class ENUReference:
    """Origin of a local WGS84 East-North-Up frame."""

    latitude: float
    longitude: float
    altitude_m: float = 0.0
    altitude_datum: str = "WGS84 ellipsoidal or local relative height"

    def __post_init__(self) -> None:
        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError("reference latitude must be in [-90, 90]")
        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError("reference longitude must be in [-180, 180]")


def _return_scalars_if_needed(
    values: tuple[np.ndarray, np.ndarray, np.ndarray], scalar: bool
) -> tuple[float, float, float] | tuple[np.ndarray, np.ndarray, np.ndarray]:
    if scalar:
        return tuple(float(np.asarray(value)) for value in values)  # type: ignore[return-value]
    return values


class ENUTransformer:
    """Convert WGS84 geodetic positions to and from a local ENU frame.

    PyProj performs the authoritative geodetic/ECEF conversion. A standard
    orthonormal rotation then expresses the ECEF displacement in ENU axes.
    """

    def __init__(self, reference: ENUReference):
        self.reference = reference
        self._to_ecef = Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)
        self._from_ecef = Transformer.from_crs("EPSG:4978", "EPSG:4979", always_xy=True)
        x0, y0, z0 = self._to_ecef.transform(
            reference.longitude, reference.latitude, reference.altitude_m
        )
        self._ecef_origin = np.asarray([x0, y0, z0], dtype=float)
        latitude_rad = np.deg2rad(reference.latitude)
        longitude_rad = np.deg2rad(reference.longitude)
        sin_lat, cos_lat = np.sin(latitude_rad), np.cos(latitude_rad)
        sin_lon, cos_lon = np.sin(longitude_rad), np.cos(longitude_rad)
        self._ecef_to_enu = np.asarray(
            [
                [-sin_lon, cos_lon, 0.0],
                [-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat],
                [cos_lat * cos_lon, cos_lat * sin_lon, sin_lat],
            ],
            dtype=float,
        )

    @classmethod
    def from_flight(
        cls,
        flight: pd.DataFrame,
        *,
        latitude_column: str = "latitude",
        longitude_column: str = "longitude",
        altitude_column: str = "altitude_m",
        flat_ground: bool = True,
    ) -> "ENUTransformer":
        """Use the first row with valid position fields as the ENU reference."""

        required = [latitude_column, longitude_column]
        missing = [name for name in required if name not in flight.columns]
        if missing:
            raise ValueError(f"flight data lacks coordinate columns: {missing}")
        valid = flight[required].apply(pd.to_numeric, errors="coerce").notna().all(axis=1)
        if not valid.any():
            raise ValueError("flight data has no valid latitude/longitude pair")
        index = valid[valid].index[0]
        altitude = 0.0
        if not flat_ground and altitude_column in flight.columns:
            parsed_altitude = pd.to_numeric(flight.loc[[index], altitude_column], errors="coerce")
            if parsed_altitude.notna().all():
                altitude = float(parsed_altitude.iloc[0])
        return cls(
            ENUReference(
                latitude=float(flight.at[index, latitude_column]),
                longitude=float(flight.at[index, longitude_column]),
                altitude_m=altitude,
                altitude_datum=(
                    "local takeoff-relative height (flat-ground assumption)"
                    if flat_ground
                    else "input altitude datum"
                ),
            )
        )

    def to_enu(
        self,
        latitude: Any,
        longitude: Any,
        altitude_m: Any = 0.0,
    ) -> tuple[float, float, float] | tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return east, north, and up coordinates in metres."""

        latitude_array, longitude_array, altitude_array = np.broadcast_arrays(
            np.asarray(latitude, dtype=float),
            np.asarray(longitude, dtype=float),
            np.asarray(altitude_m, dtype=float),
        )
        scalar = latitude_array.ndim == 0
        x, y, z = self._to_ecef.transform(
            longitude_array, latitude_array, altitude_array
        )
        delta = np.stack(
            (
                np.asarray(x, dtype=float) - self._ecef_origin[0],
                np.asarray(y, dtype=float) - self._ecef_origin[1],
                np.asarray(z, dtype=float) - self._ecef_origin[2],
            ),
            axis=0,
        )
        enu = np.einsum("ij,j...->i...", self._ecef_to_enu, delta)
        return _return_scalars_if_needed((enu[0], enu[1], enu[2]), scalar)

    def to_geodetic(
        self,
        east_m: Any,
        north_m: Any,
        up_m: Any = 0.0,
    ) -> tuple[float, float, float] | tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return latitude, longitude, and altitude for local ENU coordinates."""

        east, north, up = np.broadcast_arrays(
            np.asarray(east_m, dtype=float),
            np.asarray(north_m, dtype=float),
            np.asarray(up_m, dtype=float),
        )
        scalar = east.ndim == 0
        enu = np.stack((east, north, up), axis=0)
        delta = np.einsum("ij,j...->i...", self._ecef_to_enu.T, enu)
        ecef = delta + self._ecef_origin.reshape((3,) + (1,) * east.ndim)
        longitude, latitude, altitude = self._from_ecef.transform(
            ecef[0], ecef[1], ecef[2]
        )
        return _return_scalars_if_needed(
            (
                np.asarray(latitude, dtype=float),
                np.asarray(longitude, dtype=float),
                np.asarray(altitude, dtype=float),
            ),
            scalar,
        )

    def offset_geodetic(
        self, east_m: float, north_m: float, up_m: float = 0.0
    ) -> tuple[float, float, float]:
        latitude, longitude, altitude = self.to_geodetic(east_m, north_m, up_m)
        return float(latitude), float(longitude), float(altitude)

    def metadata(self) -> dict[str, Any]:
        return {
            "coordinate_system": "local East-North-Up",
            "geodetic_crs": "EPSG:4979 (WGS84 3D)",
            "ecef_crs": "EPSG:4978 (WGS84 geocentric)",
            "reference": asdict(self.reference),
        }


def add_enu_coordinates(
    flight: pd.DataFrame,
    transformer: ENUTransformer,
    *,
    latitude_column: str = "latitude",
    longitude_column: str = "longitude",
    altitude_column: str = "altitude_m",
) -> pd.DataFrame:
    """Return a copy with ``enu_x_m``, ``enu_y_m``, and ``enu_z_m`` columns."""

    missing = [
        name
        for name in (latitude_column, longitude_column, altitude_column)
        if name not in flight.columns
    ]
    if missing:
        raise ValueError(f"flight data lacks columns required for ENU conversion: {missing}")
    result = flight.copy()
    east, north, up = transformer.to_enu(
        pd.to_numeric(result[latitude_column], errors="coerce").to_numpy(),
        pd.to_numeric(result[longitude_column], errors="coerce").to_numpy(),
        pd.to_numeric(result[altitude_column], errors="coerce").to_numpy(),
    )
    result["enu_x_m"] = east
    result["enu_y_m"] = north
    result["enu_z_m"] = up
    return result


LocalENU = ENUTransformer


__all__ = [
    "ENUReference",
    "ENUTransformer",
    "LocalENU",
    "add_enu_coordinates",
]
