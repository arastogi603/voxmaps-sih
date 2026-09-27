from __future__ import annotations

import numpy as np
import pytest

from voxmaps_sim.particles import COARSE, FINE, ParticleSet
from voxmaps_sim.voxels import VoxelGrid


def test_voxel_indexing_is_lower_inclusive_upper_exclusive() -> None:
    grid = VoxelGrid([0.0, 0.0, 0.0], [40.0, 40.0, 20.0], 20.0, 20.0, 10.0)
    assert grid.index_of([0.0, 0.0, 0.0]) == (0, 0, 0)
    assert grid.index_of([19.999, 20.0, 9.999]) == (0, 1, 0)
    assert grid.index_of([20.0, 20.0, 10.0]) == (1, 1, 1)
    assert grid.index_of([-0.001, 0.0, 0.0]) is None
    assert grid.index_of([40.0, 1.0, 1.0]) is None


def test_stable_id_centre_bounds_and_volume() -> None:
    grid = VoxelGrid([-20.0, -20.0, 0.0], [40.0, 40.0, 20.0], 20.0, 20.0, 10.0)
    assert grid.voxel_id((1, 2, 1)) == "vx000001_vy000002_vz000001"
    np.testing.assert_allclose(grid.centre((1, 2, 1)), [10.0, 30.0, 15.0])
    lower, upper = grid.boundaries((1, 2, 1))
    np.testing.assert_allclose(lower, [0.0, 20.0, 10.0])
    np.testing.assert_allclose(upper, [20.0, 40.0, 20.0])
    assert grid.voxel_volume_m3 == 4000.0


def test_voxel_concentration_uses_grams_to_micrograms_and_pm_sum() -> None:
    grid = VoxelGrid([0.0, 0.0, 0.0], [10.0, 10.0, 10.0], 10.0, 10.0, 10.0)
    particles = ParticleSet.from_arrays(
        [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0], [20.0, 20.0, 20.0]],
        [0.001, 0.002, 100.0],
        [FINE, COARSE, FINE],
    )
    sample = grid.concentration(
        particles,
        (0, 0, 0),
        background_pm25_ug_m3=20.0,
        background_coarse_pm_ug_m3=15.0,
    )
    assert sample.fine_mass_g == pytest.approx(0.001)
    assert sample.coarse_mass_g == pytest.approx(0.002)
    assert sample.pm25_true_ug_m3 == pytest.approx(21.0)
    assert sample.coarse_pm_true_ug_m3 == pytest.approx(17.0)
    assert sample.pm10_true_ug_m3 == pytest.approx(38.0)
    assert sample.pm10_true_ug_m3 == pytest.approx(
        sample.pm25_true_ug_m3 + sample.coarse_pm_true_ug_m3
    )


def test_source_off_empty_voxel_is_background_only() -> None:
    grid = VoxelGrid([0.0, 0.0, 0.0], [10.0, 10.0, 10.0], 10.0, 10.0, 10.0)
    sample = grid.sample_position(
        ParticleSet.empty(),
        [5.0, 5.0, 5.0],
        background_pm25_ug_m3=7.0,
        background_coarse_pm_ug_m3=3.0,
    )
    assert sample.pm25_true_ug_m3 == 7.0
    assert sample.coarse_pm_true_ug_m3 == 3.0
    assert sample.pm10_true_ug_m3 == 10.0


def test_aggregate_matches_exact_voxel_samples() -> None:
    grid = VoxelGrid([0.0, 0.0, 0.0], [20.0, 10.0, 10.0], 10.0, 10.0, 10.0)
    particles = ParticleSet.from_arrays(
        [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0], [11.0, 1.0, 1.0]],
        [0.001, 0.002, 0.004],
        [FINE, COARSE, FINE],
    )
    data = grid.aggregate(particles)
    assert data["voxel_id"].tolist() == [grid.voxel_id((0, 0, 0)), grid.voxel_id((1, 0, 0))]
    np.testing.assert_allclose(data["pm25_true_ug_m3"], [1.0, 4.0])
    np.testing.assert_allclose(data["coarse_pm_true_ug_m3"], [2.0, 0.0])


def test_out_of_domain_sample_is_flagged() -> None:
    grid = VoxelGrid([0.0, 0.0, 0.0], [10.0, 10.0, 10.0], 10.0, 10.0, 10.0)
    sample = grid.sample_position(ParticleSet.empty(), [11.0, 5.0, 5.0])
    assert sample.voxel_id is None
    assert "out_of_domain_position" in sample.quality_flags
    assert np.isnan(sample.pm10_true_ug_m3)

