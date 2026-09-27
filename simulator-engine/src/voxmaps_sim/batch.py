"""Labelled scenario generation with leak-free scenario-level splits."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from .config import BatchConfig, SimulationConfig, load_config
from .ingestion import load_flight_csv
from .pipeline import RunResult, run_simulation


@dataclass(slots=True)
class BatchResult:
    output_dir: Path
    manifest: pd.DataFrame
    split_ids: dict[str, list[str]]
    scenario_results: list[RunResult]


def split_scenario_ids(
    scenario_ids: Sequence[str],
    train_fraction: float,
    validation_fraction: float,
    test_fraction: float,
    *,
    seed: int,
) -> dict[str, list[str]]:
    """Shuffle and split complete scenario identifiers without row leakage."""

    fractions = np.array([train_fraction, validation_fraction, test_fraction], dtype=float)
    if np.any(fractions < 0.0) or not np.isclose(fractions.sum(), 1.0):
        raise ValueError("train, validation, and test fractions must be non-negative and sum to 1")
    ids = np.asarray(list(scenario_ids), dtype=object)
    rng = np.random.default_rng(seed)
    shuffled = ids[rng.permutation(len(ids))]
    ideal = fractions * len(ids)
    counts = np.floor(ideal).astype(int)
    remainder = len(ids) - int(counts.sum())
    if remainder:
        order = np.argsort(-(ideal - counts), kind="stable")
        counts[order[:remainder]] += 1
    train_end = counts[0]
    validation_end = train_end + counts[1]
    result = {
        "train": shuffled[:train_end].astype(str).tolist(),
        "validation": shuffled[train_end:validation_end].astype(str).tolist(),
        "test": shuffled[validation_end:].astype(str).tolist(),
    }
    all_assigned = result["train"] + result["validation"] + result["test"]
    if len(all_assigned) != len(set(all_assigned)) or set(all_assigned) != set(map(str, ids)):
        raise RuntimeError("scenario-level split invariant failed")
    return result


def _uniform(rng: np.random.Generator, bounds: tuple[float, float]) -> float:
    low, high = map(float, bounds)
    return low if high == low else float(rng.uniform(low, high))


def _scenario_config(
    base: SimulationConfig,
    batch: BatchConfig,
    index: int,
    duration_s: float,
    rng: np.random.Generator,
) -> SimulationConfig:
    scenario = base.model_copy(deep=True)
    scenario.project.scenario_id = f"scenario_{index:05d}"
    scenario.project.random_seed = int(base.project.random_seed + index)
    scenario.source = scenario.source.model_copy(
        update={
            "latitude": None,
            "longitude": None,
            "offset_east_from_flight_centre_m": _uniform(rng, batch.source_offset_east_m),
            "offset_north_from_flight_centre_m": _uniform(rng, batch.source_offset_north_m),
            "stack_height_m": _uniform(rng, batch.stack_height_m),
        }
    )
    pm25 = _uniform(rng, batch.pm25_emission_g_s)
    pm10 = max(pm25, _uniform(rng, batch.pm10_total_emission_g_s))
    start_s = duration_s * _uniform(rng, batch.emission_start_fraction)
    end_s = duration_s * _uniform(rng, batch.emission_end_fraction)
    end_s = min(duration_s, max(end_s, start_s + scenario.simulation.timestep_s))
    if end_s <= start_s:
        start_s = max(0.0, end_s - scenario.simulation.timestep_s)
    scenario.source = scenario.source.model_copy(
        update={
            "pm25_emission_g_s": pm25,
            "pm10_total_emission_g_s": pm10,
            "emission_start_s": start_s,
            "emission_end_s": end_s,
        }
    )
    scenario.wind.mode = "synthetic"
    scenario.wind.synthetic.base_speed_mps = _uniform(rng, batch.wind_speed_mps)
    scenario.wind.synthetic.base_direction_from_deg = _uniform(
        rng, batch.wind_direction_from_deg
    )
    scenario.wind.synthetic.speed_variability_mps = _uniform(
        rng, batch.wind_variability_mps
    )
    scenario.dispersion.horizontal_diffusivity_m2_s = _uniform(
        rng, batch.horizontal_diffusivity_m2_s
    )
    scenario.sensor.additive_noise_std_ug_m3 = _uniform(
        rng, batch.additive_noise_std_ug_m3
    )
    bias = _uniform(rng, batch.sensor_bias_ug_m3)
    scenario.sensor.bias_pm25_ug_m3 = bias
    scenario.sensor.bias_pm10_ug_m3 = bias
    lag = _uniform(rng, batch.response_lag_s)
    scenario.sensor.response_time_pm25_s = lag
    scenario.sensor.response_time_pm10_s = lag
    scenario.background.pm25_ug_m3 = _uniform(rng, batch.background_pm25_ug_m3)
    return SimulationConfig.model_validate(scenario.model_dump())


def _write_splits(output_dir: Path, split_ids: dict[str, list[str]]) -> None:
    for split, ids in split_ids.items():
        (output_dir / f"{split}_scenarios.txt").write_text(
            "".join(f"{scenario_id}\n" for scenario_id in ids), encoding="utf-8"
        )


def generate_batch(
    flight_path: str | Path,
    config: SimulationConfig | str | Path,
    output_dir: str | Path,
    scenarios: int | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> BatchResult:
    """Generate independently labelled runs and dataset-level split files."""

    base = config.model_copy(deep=True) if isinstance(config, SimulationConfig) else load_config(config)
    if base.batch is None:
        raise ValueError("batch generation requires a batch section in the YAML configuration")
    batch = base.batch
    count = batch.scenarios if scenarios is None else int(scenarios)
    if count <= 0:
        raise ValueError("scenario count must be positive")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    flight = load_flight_csv(
        flight_path,
        mapping=base.input.column_mapping,
        min_wind_coverage=base.wind.minimum_numeric_coverage,
        invalid_row_policy=base.input.invalid_row_policy,
    ).normalized_flight
    duration_s = float(flight["elapsed_time_s"].max())
    rng = np.random.default_rng(base.project.random_seed)
    ids = [f"scenario_{index:05d}" for index in range(count)]
    split_ids = split_scenario_ids(
        ids,
        batch.train_fraction,
        batch.validation_fraction,
        batch.test_fraction,
        seed=base.project.random_seed,
    )
    split_for = {
        scenario_id: split for split, split_values in split_ids.items() for scenario_id in split_values
    }
    results: list[RunResult] = []
    rows: list[dict[str, Any]] = []
    for index, scenario_id in enumerate(ids):
        scenario = _scenario_config(base, batch, index, duration_s, rng)
        scenario_dir = destination / scenario_id

        def scenario_progress(fraction: float, message: str) -> None:
            if progress_callback is not None:
                progress_callback(
                    (index + fraction) / count,
                    f"{scenario_id}: {message}",
                )

        result = run_simulation(
            flight_path,
            scenario,
            scenario_dir,
            scenario_progress,
            write_visualization=False,
        )
        results.append(result)
        label = result.source_label
        rows.append(
            {
                "scenario_id": scenario_id,
                "split": split_for[scenario_id],
                "scenario_directory": scenario_id,
                "drone_sensor_samples": f"{scenario_id}/drone_sensor_samples.csv",
                "voxel_observations": f"{scenario_id}/voxel_observations.csv",
                "voxel_map": f"{scenario_id}/voxel_map.geojson",
                "source_label": f"{scenario_id}/source_label.json",
                "scenario_metadata": f"{scenario_id}/scenario_metadata.json",
                "mass_balance": f"{scenario_id}/mass_balance.json",
                "run_report": f"{scenario_id}/run_report.json",
                "source_latitude": label["source_latitude"],
                "source_longitude": label["source_longitude"],
                "stack_height_m": label["stack_height_m"],
                "pm25_emission_rate_g_s": label["pm25_emission_rate_g_s"],
                "total_pm10_emission_rate_g_s": label["total_pm10_emission_rate_g_s"],
                "wind_provenance": result.metadata["wind_provenance"],
                "synthetic_wind_speed_mps": scenario.wind.synthetic.base_speed_mps,
                "synthetic_wind_direction_from_deg": scenario.wind.synthetic.base_direction_from_deg,
                "random_seed": scenario.project.random_seed,
                "mass_balance_error_g": result.mass_balance["numerical_mass_balance_error_g"],
                "status": result.run_report["status"],
            }
        )
    manifest = pd.DataFrame.from_records(rows)
    manifest.to_csv(destination / "dataset_manifest.csv", index=False)
    _write_splits(destination, split_ids)
    if progress_callback is not None:
        progress_callback(1.0, "Batch generation complete")
    return BatchResult(destination, manifest, split_ids, results)


# Command-facing alias.
generate_dataset = generate_batch


__all__ = ["BatchResult", "generate_batch", "generate_dataset", "split_scenario_ids"]
