from __future__ import annotations

import pytest

from voxmaps_sim.config import SimulationConfig
from voxmaps_sim.particles import ParticleSet
from voxmaps_sim.source import SourceModel, split_pm_emissions, validate_pm_emissions


def test_pm10_must_contain_pm25_and_coarse_is_non_overlapping() -> None:
    fine, coarse = split_pm_emissions(0.2, 0.5)
    assert fine == pytest.approx(0.2)
    assert coarse == pytest.approx(0.3)
    assert fine + coarse == pytest.approx(0.5)
    with pytest.raises(ValueError, match="greater than or equal"):
        validate_pm_emissions(0.6, 0.5)


@pytest.mark.parametrize("fine,total", [(-0.1, 0.5), (0.1, -0.5), (float("nan"), 1.0)])
def test_invalid_pm_rates_are_rejected(fine: float, total: float) -> None:
    with pytest.raises(ValueError):
        split_pm_emissions(fine, total)


def test_source_effective_height_and_exact_timestep_mass() -> None:
    source = SourceModel(
        x_m=3.0,
        y_m=4.0,
        ground_z_m=5.0,
        stack_height_m=60.0,
        plume_rise_m=10.0,
        pm25_emission_g_s=0.2,
        pm10_total_emission_g_s=0.5,
        emission_start_s=2.5,
        emission_end_s=4.0,
    )
    assert source.effective_release_height_m == 70.0
    assert source.release_position_m.tolist() == [3.0, 4.0, 75.0]
    assert source.emitted_mass(2.0, 1.0) == pytest.approx((0.1, 0.15))
    assert source.emitted_mass(3.0, 2.0) == pytest.approx((0.2, 0.3))


def test_from_config_understands_step_change_schedule() -> None:
    config = SimulationConfig.model_validate(
        {
            "source": {
                "pm25_emission_g_s": 1.0,
                "pm10_total_emission_g_s": 2.0,
                "emission_start_s": 0.0,
                "emission_end_s": 10.0,
                "emission_schedule": [
                    {
                        "time_s": 4.0,
                        "pm25_emission_g_s": 2.0,
                        "pm10_total_emission_g_s": 5.0,
                    }
                ],
            }
        }
    )
    source = SourceModel.from_config(config, source_x_m=10.0, source_y_m=-2.0)
    assert source.emitted_mass(3.0, 2.0) == pytest.approx((3.0, 4.0))
    assert source.emitted_mass(9.0, 2.0) == pytest.approx((2.0, 3.0))


def test_particle_emission_preserves_both_channel_totals() -> None:
    parcels = ParticleSet.from_emission([0.0, 0.0, 10.0], 0.2, 0.3, 7)
    assert len(parcels) == 7
    assert parcels.fine_mass_g == pytest.approx(0.2)
    assert parcels.coarse_mass_g == pytest.approx(0.3)
    assert parcels.pm10_total_mass_g == pytest.approx(0.5)
    assert parcels.fine_count > 0 and parcels.coarse_count > 0

