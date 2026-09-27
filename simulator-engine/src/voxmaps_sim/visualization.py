"""Interactive Plotly visualisation for a completed VoxMaps simulation run."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

LOGGER = logging.getLogger(__name__)

GROUND_TRUTH_COLOUR = "#0072B2"
SENSOR_COLOUR = "#D55E00"
SOURCE_COLOUR = "#CC0029"
PATH_COLOUR = "#202B3C"
SYNTHETIC_COLOUR = "#8B5CF6"


def _first_column(frame: pd.DataFrame, names: Sequence[str]) -> str | None:
    lookup = {str(column).casefold(): str(column) for column in frame.columns}
    for name in names:
        if name in frame.columns:
            return name
        if name.casefold() in lookup:
            return lookup[name.casefold()]
    return None


def _numeric(frame: pd.DataFrame, names: Sequence[str]) -> pd.Series | None:
    column = _first_column(frame, names)
    return None if column is None else pd.to_numeric(frame[column], errors="coerce")


def _time_axis(frame: pd.DataFrame) -> tuple[pd.Series, str]:
    elapsed = _numeric(frame, ("elapsed_time_s", "time_s", "elapsed_s", "simulation_time_s"))
    if elapsed is not None:
        return elapsed, "Elapsed time (s)"
    column = _first_column(frame, ("timestamp", "datetime_utc", "original_timestamp", "datetime(utc)"))
    if column is not None:
        parsed = pd.to_datetime(frame[column], errors="coerce")
        if parsed.notna().any():
            return parsed, "UTC time"
    return pd.Series(np.arange(len(frame)), index=frame.index), "Sample"


def _empty_figure(title: str, message: str) -> go.Figure:
    figure = go.Figure()
    figure.update_layout(title=title, template="plotly_white", height=400)
    figure.add_annotation(
        text=message,
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        showarrow=False,
        font={"color": "#64748B", "size": 15},
    )
    return figure


def _source_value(source: Mapping[str, Any], names: Sequence[str]) -> float | None:
    nested: list[Mapping[str, Any]] = [source]
    for key in ("source_enu", "enu", "location"):
        item = source.get(key)
        if isinstance(item, Mapping):
            nested.append(item)
    for container in nested:
        for name in names:
            if name in container:
                try:
                    return float(container[name])
                except (TypeError, ValueError):
                    pass
    return None


def _source_xy(source: Mapping[str, Any], *, geographic: bool) -> tuple[float, float] | None:
    if geographic:
        x = _source_value(source, ("source_longitude", "longitude", "longitude_deg", "lon"))
        y = _source_value(source, ("source_latitude", "latitude", "latitude_deg", "lat"))
    else:
        x = _source_value(source, ("source_enu_x_m", "enu_x_m", "x_m", "east_m", "x"))
        y = _source_value(source, ("source_enu_y_m", "enu_y_m", "y_m", "north_m", "y"))
    return None if x is None or y is None else (x, y)


def flight_path_figure(samples: pd.DataFrame, source_label: Mapping[str, Any]) -> go.Figure:
    x = _numeric(samples, ("enu_x_m", "x_m", "east_m"))
    y = _numeric(samples, ("enu_y_m", "y_m", "north_m"))
    geographic = x is None or y is None
    if geographic:
        x = _numeric(samples, ("longitude", "lon", "lng"))
        y = _numeric(samples, ("latitude", "lat"))
    if x is None or y is None:
        return _empty_figure("Recorded flight path", "No ENU or latitude/longitude path columns were exported.")

    altitude = _numeric(samples, ("altitude_m", "enu_z_m", "z_m", "height_m"))
    custom = None if altitude is None else np.asarray(altitude).reshape(-1, 1)
    figure = go.Figure()
    figure.add_trace(
        go.Scattergl(
            x=x,
            y=y,
            mode="lines+markers",
            line={"color": PATH_COLOUR, "width": 2},
            marker={"size": 3, "color": altitude if altitude is not None else PATH_COLOUR, "colorscale": "Viridis"},
            customdata=custom,
            hovertemplate=(
                "%{x:.6f}, %{y:.6f}<br>altitude=%{customdata[0]:.1f} m<extra>Drone</extra>"
                if custom is not None
                else "%{x:.6f}, %{y:.6f}<extra>Drone</extra>"
            ),
            name="Recorded drone path",
        )
    )
    source_xy = _source_xy(source_label, geographic=geographic)
    if source_xy is not None:
        figure.add_trace(
            go.Scatter(
                x=[source_xy[0]],
                y=[source_xy[1]],
                mode="markers",
                marker={"symbol": "star", "size": 18, "color": SOURCE_COLOUR, "line": {"width": 1, "color": "white"}},
                name="Known source (ground truth)",
                hovertemplate="Known source<br>x=%{x}<br>y=%{y}<extra></extra>",
            )
        )
    figure.update_layout(
        title="Recorded flight path and configured source",
        template="plotly_white",
        height=500,
        xaxis_title="Longitude (°)" if geographic else "East (m)",
        yaxis_title="Latitude (°)" if geographic else "North (m)",
        yaxis={"scaleanchor": "x", "scaleratio": 1},
        legend={"orientation": "h", "y": 1.08},
    )
    return figure


def wind_figure(samples: pd.DataFrame) -> go.Figure:
    speed = _numeric(samples, ("wind_speed_mps", "atmospheric_wind_speed_mps", "wind_speed"))
    direction = _numeric(
        samples,
        ("wind_direction_from_deg", "wind_direction_deg", "atmospheric_wind_direction_deg"),
    )
    if speed is None and direction is None:
        return _empty_figure("Atmospheric wind", "No atmospheric-wind series was exported.")
    time, time_label = _time_axis(samples)
    figure = make_subplots(specs=[[{"secondary_y": True}]])
    source_column = _first_column(samples, ("wind_source", "wind_provenance"))
    provenance = ""
    if source_column is not None:
        values = sorted({str(value) for value in samples[source_column].dropna().unique()})
        provenance = f" — {', '.join(values)}" if values else ""
    if speed is not None:
        figure.add_trace(
            go.Scatter(x=time, y=speed, mode="lines", name="Wind speed", line={"color": SYNTHETIC_COLOUR}),
            secondary_y=False,
        )
    if direction is not None:
        figure.add_trace(
            go.Scatter(x=time, y=direction, mode="lines", name="Direction from", line={"color": "#009E73"}),
            secondary_y=True,
        )
    figure.update_layout(
        title=f"Atmospheric wind{provenance}",
        template="plotly_white",
        height=420,
        xaxis_title=time_label,
        legend={"orientation": "h", "y": 1.12},
    )
    figure.update_yaxes(title_text="Speed (m/s)", secondary_y=False)
    figure.update_yaxes(title_text="Meteorological direction from (°)", range=[0, 360], secondary_y=True)
    return figure


def particulate_timeseries_figure(samples: pd.DataFrame, pollutant: str) -> go.Figure:
    pollutant = pollutant.casefold()
    if pollutant not in {"pm25", "pm10"}:
        raise ValueError("pollutant must be 'pm25' or 'pm10'")
    truth = _numeric(
        samples,
        (
            f"{pollutant}_true_ug_m3",
            f"true_{pollutant}_ug_m3",
            f"{pollutant}_ground_truth_ug_m3",
        ),
    )
    sensor = _numeric(
        samples,
        (
            f"{pollutant}_sensor_ug_m3",
            f"{pollutant}_simulated_sensor_ug_m3",
            f"simulated_{pollutant}_ug_m3",
            f"{pollutant}_measured_ug_m3",
        ),
    )
    display_name = "PM2.5" if pollutant == "pm25" else "PM10"
    if truth is None and sensor is None:
        return _empty_figure(
            f"{display_name}: truth versus virtual sensor",
            f"No {display_name} concentration channels were exported.",
        )
    time, time_label = _time_axis(samples)
    figure = go.Figure()
    if truth is not None:
        figure.add_trace(
            go.Scattergl(
                x=time,
                y=truth,
                mode="lines",
                line={"color": GROUND_TRUTH_COLOUR, "width": 2},
                name=f"{display_name} ground truth",
                hovertemplate="%{x}<br>%{y:.3f} µg/m³<extra>Ground truth</extra>",
            )
        )
    if sensor is not None:
        figure.add_trace(
            go.Scattergl(
                x=time,
                y=sensor,
                mode="lines",
                line={"color": SENSOR_COLOUR, "width": 1.5},
                name=f"{display_name} simulated sensor",
                hovertemplate="%{x}<br>%{y:.3f} µg/m³<extra>Virtual sensor</extra>",
            )
        )
    figure.update_layout(
        title=f"{display_name}: ground truth versus simulated sensor",
        template="plotly_white",
        height=420,
        xaxis_title=time_label,
        yaxis_title="Concentration (µg/m³)",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.12},
    )
    return figure


def _voxel_value(frame: pd.DataFrame) -> tuple[pd.Series | None, str]:
    candidates = (
        "pm25_true_ug_m3_mean",
        "pm25_true_mean_ug_m3",
        "mean_pm25_true_ug_m3",
        "pm25_true_ug_m3",
        "true_pm25_ug_m3",
    )
    value = _numeric(frame, candidates)
    return value, "Mean true PM2.5 (µg/m³)"


def voxel_heatmap_figure(
    voxels: pd.DataFrame,
    samples: pd.DataFrame | None = None,
    source_label: Mapping[str, Any] | None = None,
) -> go.Figure:
    x = _numeric(voxels, ("voxel_center_x_m", "center_x_m", "enu_x_m", "x_m"))
    y = _numeric(voxels, ("voxel_center_y_m", "center_y_m", "enu_y_m", "y_m"))
    values, colour_title = _voxel_value(voxels)
    if x is None or y is None or values is None:
        return _empty_figure("2-D voxel concentration heatmap", "Voxel centres or true PM2.5 values are unavailable.")

    projected = pd.DataFrame({"x": x, "y": y, "value": values}).dropna()
    if projected.empty:
        return _empty_figure("2-D voxel concentration heatmap", "No finite observed-voxel values were exported.")
    # Multiple vertical voxels project onto one cell.  Their maximum highlights
    # the plume footprint without summing unlike concentrations.
    projected = projected.groupby(["x", "y"], as_index=False)["value"].max()
    x_unique = np.sort(projected["x"].unique())
    y_unique = np.sort(projected["y"].unique())
    if len(x_unique) * len(y_unique) <= 250_000:
        pivot = projected.pivot(index="y", columns="x", values="value").reindex(
            index=y_unique, columns=x_unique
        )
        figure = go.Figure(
            go.Heatmap(
                x=x_unique,
                y=y_unique,
                z=pivot.to_numpy(),
                colorscale="Turbo",
                colorbar={"title": colour_title},
                hovertemplate="East=%{x:.1f} m<br>North=%{y:.1f} m<br>PM2.5=%{z:.3f} µg/m³<extra></extra>",
                connectgaps=False,
            )
        )
    else:
        figure = go.Figure(
            go.Scattergl(
                x=projected["x"],
                y=projected["y"],
                mode="markers",
                marker={
                    "symbol": "square",
                    "size": 7,
                    "color": projected["value"],
                    "colorscale": "Turbo",
                    "showscale": True,
                    "colorbar": {"title": colour_title},
                },
                hovertemplate="East=%{x:.1f} m<br>North=%{y:.1f} m<extra></extra>",
            )
        )
    if samples is not None:
        path_x = _numeric(samples, ("enu_x_m", "x_m", "east_m"))
        path_y = _numeric(samples, ("enu_y_m", "y_m", "north_m"))
        if path_x is not None and path_y is not None:
            stride = max(1, int(np.ceil(len(samples) / 5000)))
            figure.add_trace(
                go.Scattergl(
                    x=path_x.iloc[::stride],
                    y=path_y.iloc[::stride],
                    mode="lines",
                    line={"color": PATH_COLOUR, "width": 2},
                    name="Recorded drone path",
                    hovertemplate="E=%{x:.1f} m<br>N=%{y:.1f} m<extra>Drone</extra>",
                )
            )
    source_xy = _source_xy(source_label or {}, geographic=False)
    if source_xy is not None:
        figure.add_trace(
            go.Scatter(
                x=[source_xy[0]],
                y=[source_xy[1]],
                mode="markers",
                marker={"symbol": "star", "size": 17, "color": SOURCE_COLOUR, "line": {"color": "white", "width": 1}},
                name="Known source",
                hovertemplate="Known source<br>E=%{x:.1f} m<br>N=%{y:.1f} m<extra></extra>",
            )
        )
    figure.update_layout(
        title="2-D observed-voxel plume footprint, drone path, and known source",
        template="plotly_white",
        height=520,
        xaxis_title="East (m)",
        yaxis_title="North (m)",
        yaxis={"scaleanchor": "x", "scaleratio": 1},
    )
    return figure


def plume_3d_figure(
    samples: pd.DataFrame,
    voxels: pd.DataFrame,
    source_label: Mapping[str, Any],
) -> go.Figure:
    vx = _numeric(voxels, ("voxel_center_x_m", "center_x_m", "enu_x_m", "x_m"))
    vy = _numeric(voxels, ("voxel_center_y_m", "center_y_m", "enu_y_m", "y_m"))
    vz = _numeric(voxels, ("voxel_center_z_m", "center_z_m", "enu_z_m", "z_m", "mean_altitude_m"))
    concentration, colour_title = _voxel_value(voxels)
    if vx is None or vy is None or vz is None or concentration is None:
        return _empty_figure("3-D plume and drone path", "3-D voxel centres or PM2.5 truth are unavailable.")

    figure = go.Figure()
    finite = vx.notna() & vy.notna() & vz.notna() & concentration.notna()
    figure.add_trace(
        go.Scatter3d(
            x=vx[finite],
            y=vy[finite],
            z=vz[finite],
            mode="markers",
            marker={
                "size": 5,
                "opacity": 0.72,
                "color": concentration[finite],
                "colorscale": "Turbo",
                "showscale": True,
                "colorbar": {"title": colour_title},
            },
            text=voxels.loc[finite, _first_column(voxels, ("voxel_id",))]
            if _first_column(voxels, ("voxel_id",))
            else None,
            hovertemplate="voxel=%{text}<br>E=%{x:.1f} m<br>N=%{y:.1f} m<br>U=%{z:.1f} m<extra>Observed voxel</extra>",
            name="Observed voxel truth",
        )
    )

    px = _numeric(samples, ("enu_x_m", "x_m", "east_m"))
    py = _numeric(samples, ("enu_y_m", "y_m", "north_m"))
    pz = _numeric(samples, ("enu_z_m", "altitude_m", "z_m", "height_m"))
    if px is not None and py is not None and pz is not None:
        stride = max(1, int(np.ceil(len(samples) / 3000)))
        figure.add_trace(
            go.Scatter3d(
                x=px.iloc[::stride],
                y=py.iloc[::stride],
                z=pz.iloc[::stride],
                mode="lines",
                line={"color": PATH_COLOUR, "width": 4},
                name="Recorded drone path",
                hovertemplate="E=%{x:.1f} m<br>N=%{y:.1f} m<br>U=%{z:.1f} m<extra>Drone</extra>",
            )
        )

    source_xy = _source_xy(source_label, geographic=False)
    release_height = _source_value(
        source_label,
        (
            "effective_release_enu_z_m",
            "effective_release_height_m",
            "effective_stack_height_m",
            "stack_height_m",
        ),
    )
    if source_xy is not None:
        figure.add_trace(
            go.Scatter3d(
                x=[source_xy[0]],
                y=[source_xy[1]],
                z=[release_height or 0.0],
                mode="markers",
                marker={"size": 8, "symbol": "diamond", "color": SOURCE_COLOUR},
                name="Known source release point",
                hovertemplate="Known source<br>E=%{x:.1f} m<br>N=%{y:.1f} m<br>release U=%{z:.1f} m<extra></extra>",
            )
        )
    figure.update_layout(
        title="3-D observed voxels, recorded drone path, and known source",
        template="plotly_white",
        height=680,
        scene={
            "xaxis_title": "East (m)",
            "yaxis_title": "North (m)",
            "zaxis_title": "Up (m)",
            "aspectmode": "data",
        },
        legend={"orientation": "h", "y": 1.05},
    )
    return figure


def build_figures(
    samples: pd.DataFrame,
    voxel_observations: pd.DataFrame | None = None,
    source_label: Mapping[str, Any] | None = None,
) -> dict[str, go.Figure]:
    """Create all figures used by both the HTML report and Streamlit UI."""

    samples = pd.DataFrame(samples)
    if voxel_observations is None:
        try:
            from .exports import aggregate_voxel_observations

            voxel_observations = aggregate_voxel_observations(samples)
        except ValueError:
            voxel_observations = pd.DataFrame()
    else:
        voxel_observations = pd.DataFrame(voxel_observations)
    label = source_label or {}
    return {
        "flight_path": flight_path_figure(samples, label),
        "wind": wind_figure(samples),
        "pm25_timeseries": particulate_timeseries_figure(samples, "pm25"),
        "pm10_timeseries": particulate_timeseries_figure(samples, "pm10"),
        "voxel_heatmap": voxel_heatmap_figure(voxel_observations, samples, label),
        "plume_3d": plume_3d_figure(samples, voxel_observations, label),
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Could not read JSON metadata from %s", path, exc_info=True)
        return {}


def load_run_data(run_dir: str | Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Read the canonical data used to render an existing run directory."""

    run_path = Path(run_dir)
    samples_path = run_path / "drone_sensor_samples.csv"
    if not samples_path.exists():
        raise FileNotFoundError(f"Run does not contain {samples_path.name}: {run_path}")
    samples = pd.read_csv(samples_path)
    voxels_path = run_path / "voxel_observations.csv"
    voxels = pd.read_csv(voxels_path) if voxels_path.exists() else pd.DataFrame()
    source_label = _read_json(run_path / "source_label.json")
    return samples, voxels, source_label


def create_visualization(
    run_dir: str | Path,
    *,
    samples: pd.DataFrame | None = None,
    voxel_observations: pd.DataFrame | None = None,
    source_label: Mapping[str, Any] | None = None,
    output_path: str | Path | None = None,
) -> Path:
    """Write a self-contained local HTML report (Plotly JavaScript included)."""

    run_path = Path(run_dir)
    if samples is None:
        loaded_samples, loaded_voxels, loaded_label = load_run_data(run_path)
        samples = loaded_samples
        if voxel_observations is None:
            voxel_observations = loaded_voxels
        if source_label is None:
            source_label = loaded_label
    figures = build_figures(samples, voxel_observations, source_label)
    output = Path(output_path) if output_path is not None else run_path / "visualization.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    cards: list[str] = []
    for index, (name, figure) in enumerate(figures.items()):
        plot = pio.to_html(
            figure,
            include_plotlyjs=True if index == 0 else False,
            full_html=False,
            config={"responsive": True, "displaylogo": False},
        )
        cards.append(f'<section class="plot-card" id="{escape(name)}">{plot}</section>')

    wind_source_column = _first_column(pd.DataFrame(samples), ("wind_source", "wind_provenance"))
    if wind_source_column is None:
        provenance = "not recorded"
    else:
        provenance = ", ".join(
            sorted({str(value) for value in pd.DataFrame(samples)[wind_source_column].dropna().unique()})
        )
    scenario_id = "unknown"
    scenario_column = _first_column(pd.DataFrame(samples), ("scenario_id",))
    if scenario_column is not None and not pd.DataFrame(samples)[scenario_column].dropna().empty:
        scenario_id = str(pd.DataFrame(samples)[scenario_column].dropna().iloc[0])

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VoxMaps pollution simulation — {escape(scenario_id)}</title>
  <style>
    :root {{ color-scheme: light; --ink:#172033; --muted:#526177; --panel:#ffffff; --edge:#dbe3ee; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:#f4f7fb; color:var(--ink); font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; }}
    header {{ padding:28px max(24px,4vw) 20px; background:linear-gradient(130deg,#0f2942,#154d67); color:white; }}
    h1 {{ margin:0 0 8px; font-size:clamp(24px,4vw,38px); }}
    header p {{ margin:5px 0; color:#d9edf6; }}
    .notice {{ margin:16px max(24px,4vw) 0; padding:14px 16px; border-left:5px solid #d97706; background:#fff8e7; }}
    main {{ display:grid; gap:18px; padding:18px max(18px,3vw) 36px; }}
    .plot-card {{ min-width:0; overflow:hidden; border:1px solid var(--edge); border-radius:12px; background:var(--panel); box-shadow:0 4px 18px rgba(26,42,66,.06); }}
    footer {{ padding:0 max(24px,4vw) 28px; color:var(--muted); }}
  </style>
</head>
<body>
  <header>
    <h1>VoxMaps Pollution Source Simulation</h1>
    <p>Scenario: <strong>{escape(scenario_id)}</strong></p>
    <p>Atmospheric wind provenance: <strong>{escape(provenance or 'not recorded')}</strong></p>
  </header>
  <aside class="notice"><strong>Research output:</strong> Ground truth is simulated. Virtual-sensor traces are synthetic observations. This is not CFD or regulatory-grade dispersion modelling.</aside>
  <main>{''.join(cards)}</main>
  <footer>Self-contained report: Plotly JavaScript and run data are embedded, so no Python server or network connection is required.</footer>
</body>
</html>
"""
    output.write_text(html, encoding="utf-8")
    return output


# Friendly aliases used by CLI implementations and notebooks.
generate_visualization = create_visualization
visualize_run = create_visualization


__all__ = [
    "build_figures",
    "create_visualization",
    "flight_path_figure",
    "generate_visualization",
    "load_run_data",
    "particulate_timeseries_figure",
    "plume_3d_figure",
    "visualize_run",
    "voxel_heatmap_figure",
    "wind_figure",
]
