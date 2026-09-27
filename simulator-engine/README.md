# VoxMaps Pollution Source Simulator

VoxMaps is a local Windows application for replaying an AirData drone flight through a simulated PM2.5/PM10 plume. It is a testing and research tool, not CFD, a regulatory dispersion model, or a health/compliance calculator.

## Start here: run a scenario without a command prompt

### 1. Open VoxMaps

Double-click **`Launch VoxMaps Simulator.bat`** in this folder. On first use, the launcher creates a private Python environment and installs the local application, so an internet connection may be needed once. Your browser opens automatically.

Keep the launcher window open while using VoxMaps. The application address begins with `http://127.0.0.1:`; this means it is running only on your computer. Close the launcher window when you are finished.

If you want a desktop icon, double-click **`Create VoxMaps Desktop Shortcut.bat`** once.

> If the launcher reports that the graphical interface files are missing, this source checkout has not yet been prepared as a release. The person preparing the project must complete the frontend build described under [Developer setup](#developer-setup). Normal scenario use does not otherwise require Node.js, PowerShell, Python commands, YAML, or typed file paths.

### 2. Upload the flight data

On **Flight data**, drag in an AirData CSV or a VoxMaps Simplified Format Data (SFD) `.xlsx` workbook, or click the file picker. VoxMaps shows the filename, row count, duration, first and last UTC timestamps, GPS extent, altitude range, detected time/coordinate/wind fields, paired-wind coverage, warnings, and an offline 2D flight-path preview.

Check the retained/excluded-column summary. Excluded telemetry remains in your original input; it is only omitted from the clean virtual-sensor log. An SFD workbook is read from its `Complete Wind Data` sheet and automatically maps its complete wind fields. If automatic recognition is uncertain, use the column-mapping dialog and select the real time, latitude, longitude, altitude, and atmospheric-wind columns.

VoxMaps never edits the selected file. It creates a checksum-protected private local processing copy under `%LOCALAPPDATA%\VoxMapsSimulator\workspace` and verifies that copy again before running. This private copy stays on the computer and is not placed in the final scenario folder.

### 3. Place and configure the source

On **Pollution source**, click the coordinate view, drag the orange marker, or enter exact WGS84 latitude and longitude. The marker and numeric fields stay synchronized.

Set the stack geometry, exhaust/plume assumptions, emission window, PM2.5 rate, coarse-PM rate, background concentrations, and random seed. Every control shows its unit and an explanation. The two mass channels never overlap:

```text
PM2.5  = fine mass
coarse = mass from 2.5 µm through 10 µm
PM10    = PM2.5 + coarse
```

PM2.5 is not counted a second time inside PM10.

### 4. Choose wind, model, sensor, and playback settings

On **Atmosphere & model**, choose one wind policy:

| Choice | What it does |
|---|---|
| **Logged wind timeline** | Uses every valid paired atmospheric wind-speed and wind-direction observation. Missing internal times are filled in vector space, and the nearest measured vector is held before the first and after the last valid pair. It never uses synthetic wind. |
| **Strict measured** | Requires measured coverage and also rejects bracketed gaps longer than the configured interpolation limit. |
| **Synthetic fallback** | Uses the entered deterministic fallback wind for the complete simulated timeline. It does not silently mix measured and synthetic wind. Uploaded raw wind is still retained separately for audit. |

VoxMaps never treats drone ground speed, x/y/z velocity, compass heading, flight direction, pitch, or roll as atmospheric wind. Invalid placeholders are not parsed as wind.

The same page exposes turbulence, fine/coarse settling and deposition, solver interval, parcel resolution, voxel size, sensor sampling-box size, sensor response/noise/bias, playback-frame interval, and visualization parcel limit. The visualization limit changes stored/rendered plume detail, not the full numerical parcel set used for concentrations and mass accounting.

### 5. Review and run

On **Review & run**:

1. Give the scenario a new name.
2. Click **Choose a folder** and select the parent output folder in the Windows picker.
3. Review warnings and the estimated solver steps, parcel count, frames, memory, and runtime.
4. Click **Run Simulation**.

The interface remains responsive while a background worker reports eight real stages: flight-file validation, coordinate preparation, wind preparation, parcel transport, sensor sampling, playback generation, output validation, and output writing. **Cancel** requests a clean stop at a safe boundary. A cancelled or failed job does not publish an apparently complete scenario folder.

VoxMaps will not overwrite an existing scenario folder. Choose another scenario name when rerunning.

### 6. Replay and inspect the result

Open **3D playback** after the run. The scene contains the recorded drone path and drone position, stack, fine and coarse parcels, deposited parcels, wind indicator, sensor sampling box, and optional voxel grid. Use play, pause, restart, forward/backward step, timeline scrubber, playback speed, follow-drone/overview cameras, reset camera, layer switches, and display density.

The drone, particles, wind, sensor region, UTC readout, and PM/wind/altitude plots share one playback time. The interface reports full numerical versus rendered parcel counts so visual decimation is never confused with the calculation.

To revisit a completed result without rerunning the solver, use **Load simulation trace** on the playback page and choose its `_simulation_trace.h5` file in the Windows picker.

### 7. Find the two output files

If the selected parent folder is `D:\VoxMaps Runs` and the scenario name is `test-flight`, the final folder is:

```text
D:\VoxMaps Runs\test-flight\
```

It contains exactly two primary files:

```text
test-flight_simulation_trace.h5
test-flight_simulated_drone_sensor_log.csv
```

Temporary/private upload data is outside this final folder. No configuration sidecar, image, HTML report, or hidden partial file is published there.

## Important setting for the supplied AirData flight

`Apr-14th-2026-09-28AM-Flight-Airdata.csv` contains:

- 12,115 rows and 54 columns;
- 1,211.4 seconds of flight data at approximately 10 Hz;
- complete relative-time, GPS, and height-above-takeoff coverage;
- 6,190 rows with paired numeric `wind_speed(mph)` and `wind_direction(degrees)` values;
- 5,925 rows without a valid pair, for **51.0937% paired coverage**;
- a longest consecutive missing-wind interval of 99.0 seconds, with 99.1 seconds between bracketing observations;
- no nonblank placeholder strings in these particular wind columns.

Normal **Logged wind timeline** mode accepts this flight because it has more than two valid pairs. It uses all 6,190 exact observations and vector-interpolates the other 5,925 flight rows, including the long gaps; the nearest measured vector is held at the flight boundaries. **Strict measured** still rejects the file at the default 80% coverage and 30-second maximum-gap settings. **Synthetic fallback** remains available as an explicit alternative. HDF5 and CSV preserve the uploaded raw wind separately from the wind actually used.

This is a data-availability decision; it does not assert that the 6,190 available pairs are physically invalid.

### Simplified Format Data (SFD)

`VOXMAPS_Wind_Gaps_Interpolated.xlsx` is the supported SFD workbook. The application reads the `Complete Wind Data` sheet, maps `elapsed_time_ms`, WGS84 position, `altitude(feet)`, `wind_speed_complete(mph)`, and `wind_direction_complete_from(degrees)`, and keeps the workbook unchanged. Its complete wind timeline has 12,115 paired rows: 6,190 labelled `original_airdata_estimate` and 5,925 explicitly labelled as interpolated or simulated. The UI shows both counts, and the HDF5/CSV outputs retain the row-level `wind_data_source` labels.

## Desktop output contract

### HDF5 simulation trace

`<scenario>_simulation_trace.h5` is the reproducibility, playback, and audit file. Non-empty array datasets are chunked and compressed with gzip. It can be loaded by the application without running the solver again.

| HDF5 location | Contents |
|---|---|
| root attributes | Schema/simulation/scenario/software versions, seed, creation time, source filename and SHA-256 checksum, and scientific-scope warning |
| `/configuration` | Complete resolved scenario configuration as JSON |
| `/ingestion` | Data-quality report, resolved column mapping, retained columns, excluded columns, and exclusion reasons |
| `/coordinates` | WGS84/ECEF/local-ENU reference, origin, altitude datum, voxel-domain bounds and size |
| `/source` | Source identity, WGS84/ENU location, stack/release geometry, and fine/coarse/total emission rates |
| `/wind/raw` | Source wind cell text as loaded, parsed numeric values, source-row indices and paired-valid flags |
| `/wind/used` | Time-aligned wind speed, direction, east/north components, provenance, interpolation method, and quality flags actually used by the solver |
| `/trajectory` | Source-row linkage, UTC/uptime/elapsed time, WGS84 position, altitude/height, and local ENU trajectory |
| `/parcels/static` | Stable parcel ID, fine/coarse class, represented mass, emission time, source ID, initial/final position, final mass and final state |
| `/playback` | Compressed frame times, offsets, deterministic rendering snapshots, per-frame full numerical counts, parcel state, and explicit visual-decimation metadata |
| `/deposition` | Deposited parcel IDs, positions, masses, and classes |
| `/contributors` | Exact per-sample contributor offsets, parcel IDs, masses and classes, plus sampling volume and backgrounds |
| `/sensor` | The complete clean sensor-log table embedded alongside the contributor records |
| `/mass_balance` | Emitted, airborne, deposited, exited/removed, and numerical-residual ledger in grams |
| `/warnings` | Run warnings and scientific/quality context |

Units are stored as dataset/group attributes where applicable:

- time: seconds (`s`) or uptime milliseconds (`ms`);
- WGS84 position: decimal degrees north/east;
- wind direction: meteorological degrees **from** north, clockwise;
- speed: metres per second (`m/s`);
- local coordinates and dimensions: metres (`m`) in East-North-Up order;
- diffusivity: square metres per second (`m²/s`) in the configuration;
- parcel/emission mass: grams (`g`) and grams per second (`g/s`);
- concentrations: micrograms per cubic metre (`µg/m³`, stored as `ug/m3`).

Playback snapshots are intentionally bounded for interactive rendering and do not duplicate every parcel at every solver microstep. Numerical concentration uses the full parcel representation; exact contributor records, the embedded sensor table, static parcel identity, mass ledger, and full numerical counts remain available for reconstruction and audit.

### Clean virtual-sensor CSV

`<scenario>_simulated_drone_sensor_log.csv` has one row for every valid uploaded flight sample by default, including duplicate or irregular source timestamps, with exact source-row linkage. The solver and playback frame cadences remain independently configurable, so preserving a 10 Hz flight log does not force 10 Hz parcel emission or 10 Hz 3D frames. A legacy fixed-interval trace mode remains available through configuration for developer experiments. The clean log only retains flight fields useful for space/time alignment, wind interpretation, flight quality, and sensor interpretation. Its column order is:

```text
simulation_id
sample_id
source_row_index
utc_time
uptime_ms
elapsed_time_s
latitude_deg
longitude_deg
altitude_msl_m
height_above_takeoff_m
x_east_m
y_north_m
z_up_m
ground_speed_mps
vertical_speed_mps
heading_deg
pitch_deg
roll_deg
satellite_count
gps_quality
flight_state
wind_speed_raw_mps
wind_direction_raw_deg
wind_speed_used_mps
wind_direction_used_deg
wind_source
sampling_region_id
voxel_id
pm25_true_ug_m3
coarse_pm_true_ug_m3
pm10_true_ug_m3
pm25_sensor_ug_m3
pm10_sensor_ug_m3
fine_contributing_parcels
coarse_contributing_parcels
quality_flags
```

`pm25_true_ug_m3`, `coarse_pm_true_ug_m3`, and `pm10_true_ug_m3` are calculated concentrations before virtual-sensor response. `pm25_sensor_ug_m3` and `pm10_sensor_ug_m3` include the selected lag, bias, noise, limits, and other enabled sensor assumptions. The true values remain separate.

## Architecture

The upgrade keeps the established Python numerical engine and replaces only the primary presentation/run boundary:

```text
Windows launcher
  → one local Uvicorn/FastAPI process
      → immutable upload inspection and configuration validation
      → one cancellable background simulation worker
      → existing ingestion, ENU, wind, parcel, dispersion and sensor modules
      → atomic HDF5 + clean CSV publication
  → React/TypeScript application served by the same local process
      → offline SVG coordinate view
      → React Three Fiber / Three.js GPU playback
      → synchronized controls and SVG plots
```

FastAPI owns durable upload, job, cancellation, folder/configuration-dialog, result, and paged HDF5 playback state. React owns high-frequency playback, camera, layer, scrubber, and plot-cursor state; changing those controls cannot restart the solver. Particle frames are fetched in bounded pages rather than loading the entire trace into browser memory.

This architecture was selected because Streamlit's script-rerun model does not cleanly provide a durable cancellable job, native dialogs, stable high-frequency GPU animation, and independent timeline/camera state together. The legacy Streamlit view remains available for developers, but it is not the normal desktop workflow.

Important Python modules:

| Module | Responsibility |
|---|---|
| `ingestion.py` | AirData schema recognition, explicit mapping, unit normalization, missing/placeholder checks |
| `coordinates.py` | WGS84 ↔ local East-North-Up conversion |
| `wind.py` | Measured/synthetic policy, interpolation, direction-vector conversion and provenance |
| `source.py`, `particles.py`, `dispersion.py` | Emission, stable parcels, time-varying transport, turbulence, settling, deposition and mass accounting |
| `sensor.py`, `voxels.py` | Virtual-sensor response, sampling context and voxel identity |
| `trace.py` | Desktop trace run, rectangular sensor-region contributors, two-file atomic contract and HDF5 reload/reconstruction |
| `desktop_jobs.py`, `desktop_models.py` | Immutable uploads, validation, estimates, background jobs and cancellation |
| `desktop_api.py` | Local HTTP API, native pickers and paged playback service |
| `desktop_launcher.py` | Local port selection, plain-language startup errors and automatic browser launch |
| `pipeline.py`, `exports.py`, `visualization.py` | Preserved legacy CLI research pipeline and its separate multi-artifact contract |

## Model behavior and invariants

At each solver step, VoxMaps emits the configured fine and coarse mass, advances every active parcel from its current position using the current time-aligned wind, adds seeded Gaussian turbulent displacement, applies class-specific settling/deposition/removal, records mass accounting, and samples the time-aligned drone position.

When wind direction changes, existing parcels continue from their current positions under the new wind vector; the finished plume is not rotated as one object. Repeating the same input, configuration, and seed produces deterministic numerical output.

The virtual sensor sums the mass of every active contributor inside the configured axis-aligned sampling box and divides by its volume, using `1 g = 1,000,000 µg`. A parcel does not have to collide with a graphical drone model. The graphical sampling box visualizes the same numerical region.

The implementation and tests enforce these rules:

- `true PM10 = true PM2.5 + true coarse PM` within numerical tolerance;
- PM2.5 and coarse PM are non-overlapping mass channels;
- raw uploaded wind and wind actually used remain distinguishable;
- invalid/placeholder wind is not converted to a number;
- drone motion and orientation are not atmospheric wind;
- drone, wind, plume, sensor, plots and playback share one timebase;
- stable contributor IDs reconstruct selected true concentration rows from HDF5;
- emitted mass is accounted as airborne, deposited, exited/removed, or numerical residual;
- cancellation never publishes a partial scenario as complete.

## Performance guidance

Runtime and trace size depend on flight duration, solver interval, parcels per step, emission duration, playback-frame interval, and visualization limit. The supplied 20-minute, approximately 10 Hz flight is much larger than the small automated fixtures.

- Increase the **playback frame interval** or lower the **visualization parcel limit** to reduce HDF5/rendering work without changing numerical sensor concentrations.
- Lower **parcels per step** only when a coarser numerical representation is scientifically acceptable; it changes numerical resolution.
- Larger sensor boxes and voxels reduce spatial detail but may reduce sparse sampling.
- The browser loads frame chunks lazily and can apply additional display density reduction. Rendered count may be lower than numerical count by design.
- The final HDF5 can be substantially larger than the CSV. Allow enough disk space and avoid very long scenario names on deeply nested Windows paths.

## Scientific limitations

- Wind varies with time but is spatially uniform across the domain; there is no resolved weather field.
- Terrain, buildings, obstacles, wakes, stack downwash, chemistry, hygroscopic growth, coagulation, and full atmospheric-stability physics are not solved.
- Ground is simplified and local ENU uses the configured altitude assumptions.
- Plume rise is prescribed/configured rather than computed by CFD or a regulatory plume-rise method.
- Turbulence is a seeded Gaussian random walk with configured horizontal/vertical diffusivity.
- Settling, dry deposition, decay, ground interception, and domain exits are simplified parameterizations.
- A computational parcel represents aggregate mass, not an individual physical particle.
- A rectangular finite sampling volume approximates the virtual sensor; sub-volume gradients are unresolved.
- Sensor lag, bias, noise, quantization, limits, dropout and optional effects are configurable hypotheses, not laboratory calibration.
- Visual snapshots may be decimated and are not the numerical concentration population.
- Mass-balance closure verifies bookkeeping, not atmospheric realism.
- Synthetic wind and labelled emissions are useful for software/research testing but do not prove real-world source detectability.

Do not use simulated concentrations for health, compliance, permitting, enforcement, or emergency-response decisions.

## Developer setup

Normal users should use the `.bat` launcher above. The following commands are for developers and release preparation.

### Prepare Python

Python 3.11 or newer is required.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### Build the production frontend

Node.js with Corepack/pnpm is required only to prepare the compiled `desktop/dist` assets. The one-click launcher deliberately does not build frontend source on every start.

```powershell
corepack enable
pnpm --dir desktop install --frozen-lockfile
pnpm --dir desktop build
```

Verify that `desktop\dist\index.html` now exists, then use `Launch VoxMaps Simulator.bat`.

### Development servers

Run the API and Vite development frontend in separate terminals:

```powershell
.\.venv\Scripts\python.exe -m uvicorn voxmaps_sim.desktop_api:app --host 127.0.0.1 --port 8765
pnpm --dir desktop dev
```

Open `http://127.0.0.1:5173`. The Vite server proxies `/api` to the local FastAPI service.

### Tests

On Windows, use a short workspace-local pytest temporary path to avoid system-temp permissions and long-path issues:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp tdocs -p no:cacheprovider
pnpm --dir desktop build
```

The clean baseline before this desktop upgrade was 50 passing tests. The current Python suite has 81 tests covering the legacy engine plus CSV/SFD schema mapping, SFD worksheet validation and provenance, sparse logged-wind interpolation, exact wind provenance, every-flight-sample output, irregular/duplicate telemetry timing, sensor volume, PM10 identity, deterministic noise/seeds, changing-wind plume bending, settling, mass conservation, HDF5 compression/reload/reconstruction, exact two-file publication, cancellation cleanup, configuration save/load, local API playback, and launcher behavior. The React component/state suite has 9 tests.

## Legacy research CLI and Streamlit interface

The original CLI, batch generator, static Plotly report, YAML configurations, and Streamlit interface are preserved for developers. They use a **different, legacy multi-artifact output contract**. They do not produce the desktop application's exactly-two-file scenario folder.

Example CLI run with the supplied flight and explicit synthetic wind:

```powershell
python -m voxmaps_sim validate `
  --flight "C:\path\to\Apr-14th-2026-09-28AM-Flight-Airdata.csv" `
  --output outputs\validation

python -m voxmaps_sim run `
  --flight "C:\path\to\Apr-14th-2026-09-28AM-Flight-Airdata.csv" `
  --config configs\demo_synthetic_wind.yaml `
  --output outputs\legacy-demo

python -m voxmaps_sim visualize --run outputs\legacy-demo
```

Developer Streamlit view:

```powershell
streamlit run src\voxmaps_sim\app.py
```

The legacy single-run pipeline can write audit/configuration JSON, normalized flight and sensor/voxel CSV files, GeoJSON, Parquet (or an explicitly named CSV fallback), mass balance, a self-contained `visualization.html`, and an artifact manifest. Batch mode additionally writes dataset manifests and whole-scenario train/validation/test splits. These artifacts remain useful for research compatibility but are not user-facing desktop scenario outputs.

Available legacy configurations:

- `configs/demo_synthetic_wind.yaml` — explicit deterministic synthetic wind;
- `configs/measured_wind.yaml` — strict measured wind and expected rejection of the supplied CSV at default coverage;
- `configs/batch_generation.yaml` — legacy multi-scenario generation.

Use `python -m voxmaps_sim --help` for the legacy CLI command list. Production source-inversion/source-detection is outside this simulator's scope.
