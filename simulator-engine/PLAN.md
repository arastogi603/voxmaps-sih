# VoxMaps Pollution Source Simulation System — Implementation Plan

## Architecture

The project is a deterministic, configuration-driven pipeline with six separable layers:

1. **Flight ingestion and quality** — discover/map AirData columns, preserve the normalized original trajectory, quantify invalid rows and atmospheric-wind coverage, and reject subscription placeholders.
2. **Coordinates and wind** — establish a local azimuthal-equidistant East/North/Up frame with PyProj; validate measured wind or build an explicitly selected synthetic, spatially uniform time series.
3. **Source and dispersion** — emit vectorized computational parcels in non-overlapping fine and coarse mass channels, then apply advection, seeded turbulent diffusion, settling, deposition/decay, ground interaction, and domain exits.
4. **Drone replay and sensor** — resample the recorded trajectory, find the current 3-D voxel at each sample, convert voxel mass to concentration, add background, and pass truth through a configurable lag/delay/noise sensor model.
5. **VoxMaps exports and visualisation** — write row-level samples, aggregated voxel observations, GeoJSON, Parquet ground truth, source/mass/quality metadata, and a self-contained Plotly report through an isolated export adapter.
6. **Batch generation and interfaces** — generate labelled scenarios and scenario-level splits; expose the same pipeline through Typer and Streamlit.

## Implementation sequence

1. Inspect the attached AirData headers and numeric coverage without modifying the source file.
2. Create packaging, YAML configurations, and validated Pydantic configuration models.
3. Implement ingestion, quality reporting, ENU conversion, and strict measured/synthetic/auto wind handling.
4. Implement source validation, particle state, dispersion physics, voxel grid, and virtual sensor.
5. Integrate the end-to-end run pipeline and mass-balance accounting.
6. Implement VoxMaps exports, GeoJSON/Parquet products, and local Plotly visualisation.
7. Implement scenario batch generation with complete-scenario train/validation/test splits.
8. Implement CLI and Streamlit interfaces.
9. Add focused unit tests plus pipeline and batch smoke tests.
10. Install dependencies in an isolated environment, run the full suite, execute one demonstration against the supplied real trajectory using explicit synthetic wind if measured wind is unusable, inspect generated files/charts, and correct defects.

## Scientific guardrails

- PM2.5 and coarse PM are tracked as disjoint masses; total PM10 is always their sum before optional raw sensor inconsistency.
- Drone velocity and compass heading are never treated as atmospheric wind.
- `auto` wind mode never silently substitutes synthetic wind.
- Parcels represent aggregate mass, not individual particles.
- Outputs identify demonstration assumptions and do not claim regulatory, calibrated, CFD, or terrain/building-wake fidelity.

## Verification gates

- Configuration and physical validation reject invalid mass and wind settings with non-zero CLI exits.
- Determinism, wind direction, coordinate round trips, dispersion, voxel math, sensor behaviour, mass conservation, pipeline completeness, and split leakage are tested.
- The demonstration must produce every required artifact and the HTML must be self-contained.
