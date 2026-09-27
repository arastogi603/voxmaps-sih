from __future__ import annotations

import numpy as np
import pytest

from voxmaps_sim.dispersion import DispersionModel, DomainBounds
from voxmaps_sim.particles import COARSE, FINE, ParticleSet
from voxmaps_sim.source import SourceModel


DOMAIN = DomainBounds(-1000.0, 1000.0, -1000.0, 1000.0, 0.0, 1000.0)


def _config(**overrides: float) -> dict:
    dispersion = {
        "horizontal_diffusivity_m2_s": 0.0,
        "vertical_diffusivity_m2_s": 0.0,
        "fine_settling_velocity_mps": 0.0,
        "coarse_settling_velocity_mps": 0.0,
        "fine_deposition_velocity_mps": 0.0,
        "coarse_deposition_velocity_mps": 0.0,
        "fine_decay_rate_s": 0.0,
        "coarse_decay_rate_s": 0.0,
    }
    dispersion.update(overrides)
    return {
        "project": {"random_seed": 123},
        "simulation": {"particles_per_timestep": 10, "maximum_particles": 100_000},
        "dispersion": dispersion,
    }


def test_constant_wind_advection_is_vectorised_and_exact() -> None:
    model = DispersionModel(_config(), DOMAIN, seed=3)
    model.particles = ParticleSet.from_arrays(
        [[0.0, 0.0, 20.0], [2.0, -1.0, 20.0]], [1.0, 2.0], [FINE, COARSE]
    )
    model.step(wind_east_mps=4.0, wind_north_mps=-2.0, dt_s=2.5)
    np.testing.assert_allclose(model.particles.positions_m, [[10.0, -5.0, 20.0], [12.0, -6.0, 20.0]])


def test_same_seed_produces_identical_diffusion() -> None:
    config = _config(horizontal_diffusivity_m2_s=5.0, vertical_diffusivity_m2_s=1.0)
    left = DispersionModel(config, DOMAIN, seed=42)
    right = DispersionModel(config, DOMAIN, seed=42)
    initial = ParticleSet.from_emission([0.0, 0.0, 100.0], 1.0, 1.0, 100)
    left.particles = initial.copy()
    right.particles = initial.copy()
    left.step(1.0, 2.0, 1.0)
    right.step(1.0, 2.0, 1.0)
    np.testing.assert_array_equal(left.particles.positions_m, right.particles.positions_m)


def test_zero_wind_random_walk_is_approximately_symmetric() -> None:
    config = _config(horizontal_diffusivity_m2_s=5.0, vertical_diffusivity_m2_s=0.0)
    model = DispersionModel(config, DOMAIN, seed=11)
    model.particles = ParticleSet.from_emission([0.0, 0.0, 100.0], 1.0, 0.0, 20_000)
    model.step(0.0, 0.0, 1.0)
    horizontal = model.particles.positions_m[:, :2]
    assert np.all(np.abs(horizontal.mean(axis=0)) < 0.08)
    assert horizontal.std(axis=0)[0] == pytest.approx(horizontal.std(axis=0)[1], rel=0.03)


def test_channel_specific_settling_and_ground_deposition() -> None:
    config = _config(
        fine_settling_velocity_mps=0.1,
        coarse_settling_velocity_mps=1.0,
    )
    model = DispersionModel(config, DOMAIN, seed=1)
    model.particles = ParticleSet.from_arrays(
        [[0.0, 0.0, 1.0], [0.0, 0.0, 0.5]], [2.0, 3.0], [FINE, COARSE]
    )
    result = model.step(0.0, 0.0, 1.0)
    assert len(model.particles) == 1
    assert model.particles.positions_m[0, 2] == pytest.approx(0.9)
    assert result.deposited_g == pytest.approx((0.0, 3.0))
    assert result.step_mass_balance_error_g == pytest.approx((0.0, 0.0), abs=1e-12)


def test_domain_exit_and_mass_balance_after_emit() -> None:
    bounds = DomainBounds(-1.0, 1.0, -1.0, 1.0, 0.0, 100.0)
    model = DispersionModel(_config(), bounds, seed=5)
    source = SourceModel(
        stack_height_m=10.0,
        plume_rise_m=0.0,
        pm25_emission_g_s=0.2,
        pm10_total_emission_g_s=0.5,
    )
    model.emit(source, 0.0, 1.0)
    result = model.step(2.0, 0.0, 1.0)
    assert result.exited_g == pytest.approx((0.2, 0.3))
    report = model.mass_balance_report()
    assert report["airborne_fine_mass_g"] == 0.0
    assert report["numerical_mass_balance_error_g"] == pytest.approx(0.0, abs=1e-12)


def test_continuous_deposition_loss_is_accounted() -> None:
    config = _config(
        fine_deposition_velocity_mps=1.0,
        coarse_deposition_velocity_mps=2.0,
        deposition_reference_height_m=10.0,
    )
    model = DispersionModel(config, DOMAIN, seed=0)
    model.particles = ParticleSet.from_arrays(
        [[0.0, 0.0, 50.0], [0.0, 0.0, 50.0]], [1.0, 1.0], [FINE, COARSE]
    )
    result = model.step(0.0, 0.0, 1.0)
    assert result.deposited_g[COARSE] > result.deposited_g[FINE] > 0.0
    assert sum(result.airborne_g) + sum(result.deposited_g) == pytest.approx(2.0)

