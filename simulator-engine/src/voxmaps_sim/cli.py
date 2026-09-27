"""Typer command-line interface for validation, runs, batches, and reports."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .batch import generate_batch
from .config import load_config
from .ingestion import validate_flight_csv
from .pipeline import run_simulation
from .visualization import create_visualization

app = typer.Typer(
    name="voxmaps-sim",
    help="Research plume simulator and VoxMaps synthetic-data generator.",
    no_args_is_help=True,
    add_completion=False,
)


def _logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _progress() -> callable:
    last = {"bucket": -1}

    def callback(fraction: float, message: str) -> None:
        bucket = int(fraction * 20)
        if bucket != last["bucket"]:
            last["bucket"] = bucket
            typer.echo(f"[{fraction:6.1%}] {message}")

    return callback


@app.command("validate")
def validate_command(
    flight: Path = typer.Option(..., "--flight", exists=True, file_okay=True, dir_okay=False),
    output: Path = typer.Option(Path("outputs/validation"), "--output", "-o"),
    config: Optional[Path] = typer.Option(None, "--config", "-c", exists=True),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Inspect and normalize a flight CSV; this never changes the input file."""

    _logging(verbose)
    try:
        resolved = load_config(config) if config else None
        result = validate_flight_csv(
            flight,
            mapping={} if resolved is None else resolved.input.column_mapping,
            min_wind_coverage=(
                0.8 if resolved is None else resolved.wind.minimum_numeric_coverage
            ),
            invalid_row_policy=(
                "drop_and_report" if resolved is None else resolved.input.invalid_row_policy
            ),
            output_dir=output,
        )
        summary = {
            "source": str(flight.resolve()),
            "normalized_rows": len(result.normalized_flight),
            "invalid_rows": result.report["trajectory"]["invalid_row_count"],
            "wind": result.report["wind"],
            "artifacts": str(output.resolve()),
        }
        typer.echo(json.dumps(summary, indent=2))
    except Exception as exc:
        typer.echo(f"Validation failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("run")
def run_command(
    flight: Path = typer.Option(..., "--flight", exists=True, file_okay=True, dir_okay=False),
    config: Path = typer.Option(
        Path("configs/demo_synthetic_wind.yaml"), "--config", "-c", exists=True
    ),
    output: Path = typer.Option(Path("outputs/demo"), "--output", "-o"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run one complete scenario and write VoxMaps artifacts."""

    _logging(verbose)
    try:
        result = run_simulation(flight, config, output, _progress())
        typer.echo(
            json.dumps(
                {
                    "status": result.run_report["status"],
                    "scenario_id": result.run_report["scenario_id"],
                    "wind_source": result.run_report["wind_source_used"],
                    "samples": len(result.samples),
                    "output": str(output.resolve()),
                },
                indent=2,
            )
        )
    except Exception as exc:
        typer.echo(f"Simulation failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("batch")
def batch_command(
    flight: Path = typer.Option(..., "--flight", exists=True, file_okay=True, dir_okay=False),
    config: Path = typer.Option(
        Path("configs/batch_generation.yaml"), "--config", "-c", exists=True
    ),
    scenarios: Optional[int] = typer.Option(None, "--scenarios", "-n", min=1),
    output: Path = typer.Option(Path("outputs/dataset"), "--output", "-o"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Generate labelled scenarios and leak-free dataset splits."""

    _logging(verbose)
    try:
        result = generate_batch(flight, config, output, scenarios, _progress())
        typer.echo(
            json.dumps(
                {
                    "status": "success",
                    "scenarios": len(result.manifest),
                    "splits": {key: len(value) for key, value in result.split_ids.items()},
                    "output": str(output.resolve()),
                },
                indent=2,
            )
        )
    except Exception as exc:
        typer.echo(f"Batch generation failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("visualize")
def visualize_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False, dir_okay=True),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
) -> None:
    """Rebuild a self-contained Plotly report from an existing run directory."""

    try:
        path = create_visualization(run, output_path=output)
        typer.echo(str(path.resolve()))
    except Exception as exc:
        typer.echo(f"Visualisation failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("version")
def version_command() -> None:
    typer.echo(__version__)


if __name__ == "__main__":
    app()
