from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from voxmaps_sim.coordinates import ENUReference, ENUTransformer, add_enu_coordinates


def test_reference_maps_to_enu_origin():
    transformer = ENUTransformer(ENUReference(28.5685, 77.2773, 0.0))
    east, north, up = transformer.to_enu(28.5685, 77.2773, 0.0)
    assert east == pytest.approx(0.0, abs=1e-7)
    assert north == pytest.approx(0.0, abs=1e-7)
    assert up == pytest.approx(0.0, abs=1e-7)


def test_coordinate_round_trip_accuracy():
    transformer = ENUTransformer(ENUReference(28.5685, 77.2773, 0.0))
    latitudes = np.array([28.5685, 28.5690, 28.5678])
    longitudes = np.array([77.2773, 77.2780, 77.2766])
    altitudes = np.array([0.0, 40.0, 125.0])

    east, north, up = transformer.to_enu(latitudes, longitudes, altitudes)
    recovered_lat, recovered_lon, recovered_alt = transformer.to_geodetic(
        east, north, up
    )

    np.testing.assert_allclose(recovered_lat, latitudes, atol=1e-9)
    np.testing.assert_allclose(recovered_lon, longitudes, atol=1e-9)
    np.testing.assert_allclose(recovered_alt, altitudes, atol=1e-5)


def test_flight_reference_and_enu_columns():
    flight = pd.DataFrame(
        {
            "latitude": [28.5685, 28.5686],
            "longitude": [77.2773, 77.2774],
            "altitude_m": [0.0, 10.0],
        }
    )
    transformer = ENUTransformer.from_flight(flight, flat_ground=True)
    transformed = add_enu_coordinates(flight, transformer)

    assert transformed.loc[0, "enu_x_m"] == pytest.approx(0.0, abs=1e-7)
    assert transformed.loc[0, "enu_y_m"] == pytest.approx(0.0, abs=1e-7)
    assert transformed.loc[1, "enu_x_m"] > 0.0
    assert transformed.loc[1, "enu_y_m"] > 0.0
