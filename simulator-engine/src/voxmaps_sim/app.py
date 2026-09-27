"""Streamlit interface for validating flight data and running the simulator.

Launch from the project root with::

    streamlit run src/voxmaps_sim/app.py

Imports of the simulator pipeline are intentionally lazy: the page can inspect
an AirData file and explain wind quality even if a later simulation dependency
fails to initialise.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import io
import json
import mimetypes
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    # Required when Streamlit executes this file as a script rather than as a
    # package module.
    sys.path.insert(0, str(SRC_ROOT))


CANONICAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "elapsed_time": ("time(millisecond)", "elapsed_time_s", "time_ms", "time"),
    "timestamp_utc": ("datetime(utc)", "datetime_utc", "timestamp", "utc_datetime"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lon", "lng"),
    "altitude": (
        "height_above_takeoff(feet)",
        "height_above_takeoff(m)",
        "altitude_m",
        "altitude(feet)",
    ),
    "wind_speed": ("wind_speed(mph)", "wind_speed(m/s)", "wind_speed_mps"),
    "wind_direction": (
        "wind_direction(degrees)",
        "wind_direction_from_deg",
        "wind_direction_deg",
    ),
    "temperature": ("temperature(c)", "temperature_c", "temperature"),
    "relative_humidity": ("relative_humidity(%)", "relative_humidity_pct", "humidity"),
    "pressure": ("pressure(hpa)", "pressure_hpa", "pressure"),
}

PLACEHOLDERS = (
    "available with enterprise subscription",
    "enterprise subscription",
    "not available",
    "not licensed",
    "upgrade required",
    "placeholder",
)


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Configuration must contain a YAML mapping: {path}")
    return payload


def _load_resolved_config(path: Path) -> dict[str, Any]:
    """Resolve YAML ``extends`` through the same model used by the CLI."""

    try:
        from voxmaps_sim.config import load_config

        return load_config(path).model_dump(mode="python")
    except ImportError:
        # Keeps read-only CSV inspection usable during partial installations.
        return _load_yaml(path)


def _headers_from_source(path: Path | None, upload_bytes: bytes | None) -> list[str]:
    try:
        if upload_bytes is not None:
            columns = pd.read_csv(io.BytesIO(upload_bytes), nrows=0).columns
        elif path is not None and path.is_file():
            columns = pd.read_csv(path, nrows=0).columns
        else:
            return []
        return [str(column).strip() for column in columns]
    except Exception:
        return []


def _guess_mapping(headers: list[str]) -> dict[str, str | None]:
    lower = {header.casefold(): header for header in headers}
    mapping: dict[str, str | None] = {}
    for canonical, aliases in CANONICAL_COLUMNS.items():
        mapping[canonical] = next(
            (lower[alias.casefold()] for alias in aliases if alias.casefold() in lower),
            None,
        )
    return mapping


def _numeric_quality(series: pd.Series) -> dict[str, Any]:
    text = series.astype("string").str.strip()
    blank = series.isna() | text.eq("")
    placeholder = text.str.casefold().fillna("").map(
        lambda value: any(marker in value for marker in PLACEHOLDERS)
    )
    numeric = pd.to_numeric(series.mask(placeholder), errors="coerce")
    rows = len(series)
    return {
        "numeric_count": int(numeric.notna().sum()),
        "numeric_coverage": float(numeric.notna().mean()) if rows else 0.0,
        "missing_count": int(blank.sum()),
        "missing_percent": float(blank.mean() * 100.0) if rows else 0.0,
        "placeholder_count": int((placeholder & ~blank).sum()),
        "invalid_text_count": int((~blank & ~placeholder & numeric.isna()).sum()),
    }


def inspect_csv(path: Path, mapping: Mapping[str, str | None]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Perform a read-only, UI-level validation of an AirData CSV."""

    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    raw.columns = [str(column).strip() for column in raw.columns]
    columns: dict[str, Any] = {}
    for canonical, source in mapping.items():
        if source and source in raw.columns:
            columns[canonical] = {"source_column": source, **_numeric_quality(raw[source])}
        else:
            columns[canonical] = {"source_column": source, "available": False}

    speed_name = mapping.get("wind_speed")
    direction_name = mapping.get("wind_direction")
    if speed_name in raw.columns and direction_name in raw.columns:
        speed = pd.to_numeric(raw[speed_name].astype("string").str.strip(), errors="coerce")
        direction = pd.to_numeric(
            raw[direction_name].astype("string").str.strip(), errors="coerce"
        )
        paired_coverage = float((speed.notna() & direction.notna()).mean())
    else:
        paired_coverage = 0.0

    report = {
        "input_path": str(path),
        "row_count": int(len(raw)),
        "column_count": int(len(raw.columns)),
        "mapped_columns": columns,
        "atmospheric_wind": {
            "paired_numeric_coverage": paired_coverage,
            "note": "Drone xSpeed/ySpeed/zSpeed and compass heading are intentionally excluded.",
        },
    }
    return raw, report


def _paired_wind_coverage(report: Mapping[str, Any] | None) -> float:
    if not report:
        return 0.0
    wind = report.get("wind")
    if isinstance(wind, Mapping):
        value = wind.get("paired_numeric_coverage", 0.0)
    else:
        atmospheric = report.get("atmospheric_wind", {})
        value = atmospheric.get("paired_numeric_coverage", 0.0) if isinstance(atmospheric, Mapping) else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _materialize_upload(upload_name: str, payload: bytes) -> Path:
    digest = hashlib.sha256(payload).hexdigest()[:12]
    safe_name = "".join(character for character in Path(upload_name).name if character.isalnum() or character in "._-")
    target_dir = PROJECT_ROOT / "outputs" / "_streamlit_inputs"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{digest}-{safe_name or 'flight.csv'}"
    if not target.exists():
        target.write_bytes(payload)
    return target


def _nested_set(payload: dict[str, Any], section: str, key: str, value: Any) -> None:
    payload.setdefault(section, {})[key] = value


def _display_source(st: Any, source: Mapping[str, Any]) -> None:
    st.subheader("Known source location (supervised ground truth)")
    columns = st.columns(4)
    fields = (
        ("Latitude", ("source_latitude", "latitude", "latitude_deg"), "°"),
        ("Longitude", ("source_longitude", "longitude", "longitude_deg"), "°"),
        ("East", ("source_enu_x_m", "x_m", "east_m"), "m"),
        ("North", ("source_enu_y_m", "y_m", "north_m"), "m"),
    )
    for column, (label, candidates, unit) in zip(columns, fields):
        value = next((source[name] for name in candidates if name in source), None)
        column.metric(label, "not recorded" if value is None else f"{float(value):.6g} {unit}")
    st.caption("This location is a generated label, not an inferred source estimate.")


def _display_downloads(st: Any, run_dir: Path, artifacts: Mapping[str, Any] | None) -> None:
    st.subheader("Output downloads")
    candidates: dict[str, Path] = {}
    if artifacts:
        for key, value in artifacts.items():
            try:
                path = Path(value)
            except TypeError:
                continue
            if path.is_file():
                candidates[str(key)] = path
    if not candidates and run_dir.is_dir():
        candidates = {path.stem: path for path in sorted(run_dir.iterdir()) if path.is_file()}
    for index, (label, path) in enumerate(sorted(candidates.items())):
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        st.download_button(
            f"Download {path.name}",
            data=path.read_bytes(),
            file_name=path.name,
            mime=mime,
            key=f"download-{index}-{path.name}",
        )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _display_run(st: Any, run_dir: Path, result: Any | None = None) -> None:
    from voxmaps_sim.visualization import build_figures, load_run_data

    try:
        samples, voxels, source_from_disk = load_run_data(run_dir)
    except (FileNotFoundError, pd.errors.ParserError) as exc:
        st.error(f"Could not load completed run: {exc}")
        return
    source = getattr(result, "source_label", None) or source_from_disk
    figures = build_figures(samples, voxels, source)

    st.success(f"Simulation outputs are ready in {run_dir}")
    _display_source(st, source)
    st.info(
        "Blue traces and voxel colours are simulated ground truth. Orange traces are virtual-sensor readings. "
        "The wind plot labels its measured or synthetic provenance explicitly."
    )
    left, right = st.columns(2)
    left.plotly_chart(figures["flight_path"], use_container_width=True)
    right.plotly_chart(figures["voxel_heatmap"], use_container_width=True)
    st.plotly_chart(figures["plume_3d"], use_container_width=True)
    left, right = st.columns(2)
    left.plotly_chart(figures["pm25_timeseries"], use_container_width=True)
    right.plotly_chart(figures["pm10_timeseries"], use_container_width=True)
    st.plotly_chart(figures["wind"], use_container_width=True)

    mass = getattr(result, "mass_balance", None) or _read_json(run_dir / "mass_balance.json")
    quality = _read_json(run_dir / "data_quality_report.json")
    report = getattr(result, "run_report", None) or _read_json(run_dir / "run_report.json")
    first, second = st.columns(2)
    with first:
        st.subheader("Mass balance")
        if mass:
            st.json(mass)
        else:
            st.caption("No mass-balance report was found.")
    with second:
        st.subheader("Data and run quality")
        if quality:
            st.json(quality)
        if report:
            st.json(report)
        if not quality and not report:
            st.caption("No quality report was found.")
    _display_downloads(st, run_dir, getattr(result, "artifacts", None))


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="VoxMaps Pollution Simulator", page_icon="🌫️", layout="wide")
    st.title("VoxMaps Pollution Source Simulation System")
    st.caption(
        "Research simulator and labelled synthetic-data generator — not CFD, an emissions inventory, or a regulatory atmospheric model."
    )

    config_paths = sorted((PROJECT_ROOT / "configs").glob("*.yaml"))
    if not config_paths:
        st.error("No YAML configurations were found in the project's configs directory.")
        return

    with st.sidebar:
        st.header("1 · Flight data")
        source_kind = st.radio("CSV source", ("Upload", "Local path"), horizontal=True)
        upload = None
        path_text = ""
        if source_kind == "Upload":
            upload = st.file_uploader("AirData CSV", type=("csv",))
        else:
            path_text = st.text_input("CSV path", help="The source file is read only and is never modified.")
        upload_bytes = upload.getvalue() if upload is not None else None
        candidate_path = Path(path_text).expanduser() if path_text.strip() else None

        selected_config = st.selectbox(
            "Base configuration",
            config_paths,
            format_func=lambda path: path.name,
            index=next((i for i, path in enumerate(config_paths) if path.name == "demo_synthetic_wind.yaml"), 0),
        )
        base_config = _load_resolved_config(Path(selected_config))
        minimum_coverage = float(base_config.get("wind", {}).get("minimum_numeric_coverage", 0.0))

    headers = _headers_from_source(candidate_path, upload_bytes)
    guessed = _guess_mapping(headers)
    mapping: dict[str, str | None] = {}
    with st.expander("Column mapping", expanded=bool(headers)):
        if not headers:
            st.caption("Choose a CSV to expose mapping controls.")
        else:
            # Do not create empty mapping widgets before a source is known;
            # Streamlit otherwise persists that initial empty state after a
            # file is selected and obscures the parser's inferred mapping.
            options = ["Not mapped", *headers]
            for canonical in CANONICAL_COLUMNS:
                default = guessed.get(canonical)
                default_index = options.index(default) if default in options else 0
                selected = st.selectbox(
                    canonical.replace("_", " ").title(),
                    options,
                    index=default_index,
                    key=f"mapping-{canonical}",
                )
                mapping[canonical] = None if selected == options[0] else selected
    ingestion_mapping = {key: value for key, value in mapping.items() if value is not None}

    csv_path: Path | None = None
    if upload is not None and upload_bytes is not None:
        csv_path = _materialize_upload(upload.name, upload_bytes)
    elif candidate_path is not None:
        csv_path = candidate_path.resolve()

    raw: pd.DataFrame | None = None
    quality_report: dict[str, Any] | None = None
    if csv_path is not None:
        if not csv_path.is_file():
            st.error(f"CSV path does not exist: {csv_path}")
        else:
            try:
                # Use the simulator's authoritative parser so this view and a
                # later run apply identical unit conversion, placeholder, and
                # forbidden-drone-velocity rules.  The local inspector remains
                # a defensive fallback for partial installations.
                try:
                    from voxmaps_sim.ingestion import validate_flight_csv

                    validation = validate_flight_csv(
                        csv_path,
                        mapping=ingestion_mapping,
                        min_wind_coverage=minimum_coverage,
                        invalid_row_policy=str(base_config.get("input", {}).get("invalid_row_policy", "drop_and_report")),
                    )
                    quality_report = validation.report
                    raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
                    raw.columns = [str(column).strip() for column in raw.columns]
                except ImportError:
                    raw, quality_report = inspect_csv(csv_path, mapping)
                st.subheader("Input validation")
                c1, c2, c3 = st.columns(3)
                input_summary = quality_report.get("input", quality_report)
                c1.metric("Rows", f"{int(input_summary.get('row_count', len(raw))):,}")
                c2.metric("Columns", int(input_summary.get("column_count", len(raw.columns))))
                paired_coverage = _paired_wind_coverage(quality_report)
                c3.metric("Paired atmospheric-wind coverage", f"{paired_coverage:.1%}")
                with st.expander("Data-quality report"):
                    st.json(quality_report)
                    st.dataframe(raw.head(50), use_container_width=True)
            except Exception as exc:
                st.error(f"CSV validation failed: {exc}")

    paired_coverage = _paired_wind_coverage(quality_report)
    measured_available = paired_coverage >= minimum_coverage

    with st.sidebar:
        st.header("2 · Atmospheric wind")
        configured_mode = str(base_config.get("wind", {}).get("mode", "synthetic"))
        wind_mode = st.selectbox(
            "Wind mode",
            ("synthetic", "measured", "auto"),
            index=("synthetic", "measured", "auto").index(configured_mode),
            help="Auto never substitutes synthetic wind. It only accepts measured fields that pass coverage checks.",
        )
        if wind_mode in {"measured", "auto"} and not measured_available:
            st.warning(
                f"Measured wind is unavailable at the {minimum_coverage:.0%} threshold "
                f"(paired numeric coverage: {paired_coverage:.1%}). Select synthetic explicitly to run."
            )
        synthetic_speed = float(base_config["wind"]["synthetic"]["base_speed_mps"])
        synthetic_direction = float(base_config["wind"]["synthetic"]["base_direction_from_deg"])
        if wind_mode == "synthetic":
            synthetic_speed = st.number_input("Synthetic speed (m/s)", min_value=0.0, value=synthetic_speed, step=0.1)
            synthetic_direction = st.number_input(
                "Direction wind comes from (°)", min_value=0.0, max_value=360.0, value=synthetic_direction, step=1.0
            )
            st.caption("Synthetic wind is spatially uniform and every output row is flagged accordingly.")

        st.header("3 · Source")
        lat_values = None
        lon_values = None
        if raw is not None and mapping.get("latitude") in raw.columns and mapping.get("longitude") in raw.columns:
            lat_values = pd.to_numeric(raw[mapping["latitude"]], errors="coerce")
            lon_values = pd.to_numeric(raw[mapping["longitude"]], errors="coerce")
        default_lat = base_config["source"].get("latitude")
        default_lon = base_config["source"].get("longitude")
        if default_lat is None and lat_values is not None and lat_values.notna().any():
            default_lat = float(lat_values.mean())
        if default_lon is None and lon_values is not None and lon_values.notna().any():
            default_lon = float(lon_values.mean())
        source_lat_text = st.text_input(
            "Source latitude (blank = configured ENU offset)",
            value="" if default_lat is None else f"{float(default_lat):.8f}",
        )
        source_lon_text = st.text_input(
            "Source longitude (blank = configured ENU offset)",
            value="" if default_lon is None else f"{float(default_lon):.8f}",
        )
        stack_height = st.number_input(
            "Physical stack height (m)", min_value=0.1, value=float(base_config["source"]["stack_height_m"]), step=1.0
        )
        pm25_rate = st.number_input(
            "PM2.5 emission (g/s)", min_value=0.0, value=float(base_config["source"]["pm25_emission_g_s"]), step=0.01
        )
        pm10_rate = st.number_input(
            "Total PM10 emission (g/s)", min_value=0.0, value=float(base_config["source"]["pm10_total_emission_g_s"]), step=0.01
        )
        invalid_mass = pm10_rate < pm25_rate
        if invalid_mass:
            st.error("Total PM10 must be at least PM2.5; coarse PM is their difference.")

        st.header("4 · Voxels and sensor")
        voxel_x = st.number_input("Voxel east-west size (m)", min_value=1.0, value=float(base_config["voxel"]["size_x_m"]), step=1.0)
        voxel_y = st.number_input("Voxel north-south size (m)", min_value=1.0, value=float(base_config["voxel"]["size_y_m"]), step=1.0)
        voxel_z = st.number_input("Voxel vertical size (m)", min_value=1.0, value=float(base_config["voxel"]["size_z_m"]), step=1.0)
        additive_noise = st.number_input(
            "Sensor additive noise σ (µg/m³)", min_value=0.0, value=float(base_config["sensor"]["additive_noise_std_ug_m3"]), step=0.1
        )
        multiplicative_noise = st.number_input(
            "Sensor multiplicative noise σ (fraction)", min_value=0.0, value=float(base_config["sensor"]["multiplicative_noise_std_fraction"]), step=0.01
        )
        dropout = st.number_input(
            "Sensor dropout probability", min_value=0.0, max_value=1.0, value=float(base_config["sensor"]["dropout_probability"]), step=0.01
        )
        output_name = st.text_input("Output folder name", value="streamlit-run")

    config_payload = deepcopy(base_config)
    _nested_set(config_payload, "input", "column_mapping", ingestion_mapping)
    _nested_set(config_payload, "wind", "mode", wind_mode)
    _nested_set(config_payload, "wind", "minimum_numeric_coverage", minimum_coverage)
    _nested_set(config_payload["wind"], "synthetic", "base_speed_mps", synthetic_speed)
    _nested_set(config_payload["wind"], "synthetic", "base_direction_from_deg", synthetic_direction)
    _nested_set(config_payload, "source", "stack_height_m", stack_height)
    _nested_set(config_payload, "source", "pm25_emission_g_s", pm25_rate)
    _nested_set(config_payload, "source", "pm10_total_emission_g_s", pm10_rate)
    _nested_set(config_payload, "voxel", "size_x_m", voxel_x)
    _nested_set(config_payload, "voxel", "size_y_m", voxel_y)
    _nested_set(config_payload, "voxel", "size_z_m", voxel_z)
    _nested_set(config_payload, "sensor", "additive_noise_std_ug_m3", additive_noise)
    _nested_set(config_payload, "sensor", "multiplicative_noise_std_fraction", multiplicative_noise)
    _nested_set(config_payload, "sensor", "dropout_probability", dropout)

    coordinate_error = False
    if bool(source_lat_text.strip()) != bool(source_lon_text.strip()):
        st.error("Supply both source latitude and longitude, or leave both blank to use the configured offset.")
        coordinate_error = True
    elif source_lat_text.strip():
        try:
            _nested_set(config_payload, "source", "latitude", float(source_lat_text))
            _nested_set(config_payload, "source", "longitude", float(source_lon_text))
        except ValueError:
            st.error("Source latitude and longitude must be numeric.")
            coordinate_error = True
    else:
        _nested_set(config_payload, "source", "latitude", None)
        _nested_set(config_payload, "source", "longitude", None)

    wind_blocked = wind_mode in {"measured", "auto"} and not measured_available
    run_disabled = csv_path is None or not (csv_path and csv_path.is_file()) or invalid_mass or coordinate_error or wind_blocked
    progress = st.progress(0.0, text="Ready")
    if st.button("Run simulation", type="primary", disabled=run_disabled, use_container_width=True):
        try:
            from voxmaps_sim.config import SimulationConfig
            from voxmaps_sim.pipeline import run_simulation

            config = SimulationConfig.model_validate(config_payload)
            safe_output_name = "".join(
                character for character in output_name.strip() if character.isalnum() or character in "._-"
            )
            if not safe_output_name:
                raise ValueError("Output folder name must contain a letter or number.")
            output_dir = (PROJECT_ROOT / "outputs" / safe_output_name).resolve()
            allowed_output_root = (PROJECT_ROOT / "outputs").resolve()
            if allowed_output_root not in output_dir.parents:
                raise ValueError("Output folder must remain inside the project outputs directory.")

            def update_progress(value: float, message: str) -> None:
                fraction = float(value) / 100.0 if float(value) > 1.0 else float(value)
                progress.progress(max(0.0, min(1.0, fraction)), text=message)

            progress.progress(0.02, text="Validating configuration")
            result = run_simulation(csv_path, config, output_dir, progress_callback=update_progress)
            progress.progress(1.0, text="Complete")
            st.session_state["last_run_dir"] = str(output_dir)
            st.session_state["last_run_result"] = result
        except Exception as exc:
            progress.empty()
            st.exception(exc)

    last_run = st.session_state.get("last_run_dir")
    if last_run:
        _display_run(st, Path(last_run), st.session_state.get("last_run_result"))
    elif not headers:
        st.info("Upload an AirData CSV or enter its local path to begin validation.")


if __name__ == "__main__":
    main()
