# VoxMaps Interactive Application Implementation Plan

Updated: 2026-08-17

## Completed change — logged time-varying wind timeline (2026-08-19)

Status: **implemented and verified**.

### Requested behavior

The normal desktop **Measured wind** option will use the paired atmospheric wind-speed and wind-direction observations logged by the UAV even when global numeric coverage is below the strict threshold. Exact logged pairs remain unchanged. Missing times are filled in east/north vector space so direction changes cross 0°/360° correctly; internal gaps are interpolated and flight intervals before the first or after the last valid pair hold the nearest measured vector. This mode never substitutes synthetic wind and never derives atmospheric wind from drone velocity, heading, compass, or flight direction.

**Strict measured wind** remains unchanged: it continues to enforce the configured paired-coverage threshold and maximum interpolation gap. Existing CLI/config behavior for `wind.mode: measured` also remains strict for backward compatibility.

### Files to change

- **[MODIFY]** `src/voxmaps_sim/config.py` — add an explicit internal `measured_interpolated` engine mode without changing existing strict `measured` semantics.
- **[MODIFY]** `src/voxmaps_sim/wind.py` — add all-gap vector interpolation plus nearest measured edge-hold for the new mode; retain exact-pair and provenance flags.
- **[MODIFY]** `src/voxmaps_sim/trace.py` — map normal desktop measured policy to `measured_interpolated`, keep strict policy mapped to `measured`, and preserve raw/used timelines separately.
- **[MODIFY]** `src/voxmaps_sim/desktop_jobs.py` — validate normal measured mode from at least two paired observations instead of the global coverage threshold; retain strict validation and warnings.
- **[MODIFY]** `desktop/src/configPolicies.ts`, `desktop/src/components/ModelStep.tsx`, and `desktop/src/components/ReviewStep.tsx` — explain that normal measured mode uses the logged time-varying timeline and fills missing intervals, while strict mode enforces coverage/gap limits.
- **[MODIFY]** `tests/test_wind.py`, `tests/test_desktop_api.py`, and frontend policy tests — cover sparse measured data, exact observation preservation, vector interpolation, boundary hold, provenance, strict rejection, and no synthetic/zero-advection substitution.
- **[MODIFY]** `README.md` and `STATUS.md` — document the two measured policies and final representative-flight verification.

### Acceptance checks

1. The supplied 51.0937%-coverage flight is accepted in normal **Measured wind** mode and still rejected in **Strict measured wind** mode at the 80% threshold.
2. Every valid logged pair is used exactly at its timestamp.
3. Missing internal times use vector interpolation; leading/trailing times hold the nearest measured vector.
4. The used-wind timeline contains only `measured` and `interpolated` provenance for this run—never `synthetic_fallback` or drone-motion-derived wind.
5. Raw CSV wind text/numbers and simulation-used wind remain separately auditable in HDF5 and CSV.
6. Existing strict, synthetic, deterministic, PM10, mass-balance, cancellation, and playback behavior remains passing.
7. The full Python suite, frontend tests, production build, and a supplied-CSV measured-timeline smoke run pass before completion.

## Baseline and current architecture

The repository already contains a deterministic, unit-tested Python pollution-dispersion engine under `src/voxmaps_sim`. Its reusable boundaries are:

- `ingestion.py`: AirData-style schema detection, immutable CSV loading, unit normalization, quality reports, and column mapping;
- `coordinates.py`: WGS84 to local ENU conversion and inverse conversion;
- `wind.py`: explicit measured/synthetic selection, vector interpolation, provenance, and rejection of drone-motion proxies;
- `source.py`, `particles.py`, and `dispersion.py`: source emission, fine/coarse parcel transport, settling, deposition, and mass accounting;
- `voxels.py` and `sensor.py`: concentration aggregation and deterministic virtual-sensor response;
- `pipeline.py`: time-aligned flight replay and legacy artifact export;
- `app.py`: the current Streamlit interface;
- `exports.py` and `visualization.py`: the existing multi-artifact research export and static Plotly report.

Baseline command:

`python -m pytest -q --basetemp work/pytest-baseline-20260817-001 -p no:cacheprovider`

Baseline result: **50 passed in 5.31 seconds**. An initial run using the system temporary directory produced 11 `tmp_path` setup errors because Windows denied access to `%LOCALAPPDATA%/Temp/pytest-of-Harshvardhan`; rerunning against a workspace-local temporary directory proved these were environmental rather than test failures.

The representative AirData file has 12,115 rows and 54 columns, spans 1,211.4 seconds at approximately 10 Hz, and has complete time/GPS/height coverage. It contains 6,190 paired numeric wind rows (51.0937% coverage), below the default 80% strict threshold. There are no nonblank wind placeholders in this particular file. Normal Logged wind timeline mode uses all valid pairs and fills missing times; Strict measured mode rejects it. Leading spaces in several AirData headers are handled by normalized schema matching.

## Architecture decision

The Python engine remains authoritative. The primary presentation layer is a local **FastAPI backend plus React/TypeScript frontend using Three.js through React Three Fiber**. The production frontend is built to static assets and served by the same local Python process. A one-click Windows launcher starts that process and opens the application.

The current Streamlit UI is retained for developer compatibility, but it is not the primary workflow. Streamlit's script-rerun state model is a poor fit for all of the following requirements simultaneously:

- a durable background simulation job with genuine stage progress and cooperative cancellation;
- native output-folder selection without manual path entry;
- a continuously animated, GPU-rendered scene containing thousands of decimated visual parcels;
- play/pause/step/scrub controls whose client state must not rerun the solver;
- synchronized camera, layer, timeline, plot-cursor, and HDF5 reload state;
- responsive controls while the solver is active.

The local web architecture keeps the numerical engine in process, gives the simulation an explicit job lifecycle, and lets the browser own high-frequency playback state. It is intentionally local and single-user; it is not a cloud service or a broad rewrite of the solver.

## Upgrade scope and implementation state

1. **Implemented and covered:** trace-capable runs record stable parcel identity, emission metadata, bounded frame-indexed active/deposited rendering states, final states, and exact sensor contributors.
2. **Implemented and covered:** a rectangular numerical sensor-volume sampler is independent of graphical collision logic.
3. **Implemented and covered:** the desktop exporter atomically publishes one compressed HDF5 playback/audit trace and the exact clean sensor-log CSV schema.
4. **Implemented and covered:** the local service provides validation, native configuration/folder dialogs, a background job, cooperative cancellation, partial-output cleanup, and paged HDF5 reload endpoints.
5. **Implemented in source:** the guided React application covers upload, mapping, source placement, model configuration, review/run, and playback.
6. **Implemented in source:** React Three Fiber playback includes synchronized trajectory/plume/wind/sensor state, timeline controls, plots, layer toggles, cameras, lazy frame paging, and numerical-versus-rendered counts.
7. **Implemented and verified:** compiled frontend assets, the Windows launcher, optional shortcut helper, and user-first documentation are present. The production build and launcher-equivalent local serving smoke test passed.
8. **Implemented and verified end to end:** the supplied representative flight completed through the built GUI with explicit synthetic fallback, published exactly two files, passed HDF5-to-CSV contributor reconstruction, reloaded without a solver rerun, and was visually reviewed across the major workflow screens.

## Milestones

### M1 — Contracts and engine instrumentation — complete

- Extend parcel state with stable identifiers and emission/source metadata without changing established physics.
- Record deposited states and playback snapshots at a configurable interval.
- Sample a configurable rectangular sensor region and retain exact contributing parcel IDs and represented masses.
- Preserve PM2.5, coarse PM, and PM10 as separate, non-overlapping quantities.

### M2 — Two-file persistence — complete

- Write `<scenario>_simulation_trace.h5` using chunked compressed datasets and documented units.
- Write `<scenario>_simulated_drone_sensor_log.csv` with exactly the requested columns.
- Write to temporary names and atomically expose both outputs only after validation.
- Reload playback without rerunning the solver and reconstruct selected concentration rows from contributor records.

### M3 — Local service — complete

- Add upload/inspection, column-mapping, configuration-validation, native-folder, run, progress, cancellation, result, and playback APIs.
- Run solver jobs outside the request thread and expose meaningful stage/status/elapsed data.
- Delete incomplete temporary output on cancellation or failure.

### M4 — React/Three.js application — complete and visually verified

- Implement the upload, source, atmosphere/numerics, review/run, and playback workspace.
- Add an offline-capable geographic SVG preview with click/drag source placement and exact coordinates.
- Add Three.js drone, path, stack, particles, deposited layer, wind indicator, sensor region, optional voxel grid, and two camera modes.
- Add play, pause, restart, step, scrub, speed, camera, layer, and render-density controls with synchronized plots.

### M5 — Windows packaging and verification — complete

- Built the frontend static assets and added a one-click `.bat` launcher plus optional shortcut script. The production build and launcher-equivalent local serving smoke test passed.
- Ran automated unit/integration tests and the complete legacy suite: **81 Python tests passed** and **9 frontend tests passed**.
- Completed the supplied 12,115-row representative flight through the production GUI, verified exactly two outputs, reloaded HDF5 without rerunning, and passed cross-file contributor reconstruction.
- Inspected the major screens in the in-app browser and captured the upload, inspection, source, wind validation/fallback, review, progress, completion, active playback, reload, and scrub states under `evidence/screenshots`.

## Acceptance-test matrix

| Requirement | Automated evidence | Manual evidence | Current state |
|---|---|---|---|
| AirData detection and fallback mapping | Ingestion/API alias, ambiguity and placeholder tests | Supplied CSV upload summary and mapping UI inspected | Passed |
| Raw/used wind separation | HDF5 and CSV schema/provenance tests | Review warning and playback wind readout inspected | Passed |
| Logged/strict/synthetic wind behavior | Sparse logged timeline, strict rejection, and explicit fallback tests | Supplied CSV logged mode ready at 51.1%; strict still rejects | Passed |
| Coordinate and time synchronization | ENU round-trip and aligned-sample tests | Scrubbed drone/plume/plot state inspected | Passed |
| Curved time-varying plume | Explicit 90-degree wind-change cohort test | Active 3D plume playback inspected | Passed |
| Fine/coarse separation and PM10 identity | Settling, mass and PM10 invariant tests | Legend and synchronized plots inspected | Passed |
| Sensor volume and contributor audit | Analytic-volume and HDF5 reconstruction tests | Representative HDF5 contributor reconstruction matched | Passed |
| Determinism | Repeated-run numerical equivalence tests | Seed/configuration shown in review | Passed |
| Atomic outputs and cancellation | Cancellation/partial cleanup and exact-directory assertions | Representative final directory contained exactly two files | Passed |
| HDF5 compression and reload | Dataset compression/schema/frame/reconstruction tests | Completed trace reloaded in playback without rerunning | Passed |
| Complete non-terminal workflow | Upload/config/job/playback API integration tests | Supplied CSV completed upload-to-export in the production GUI | Passed |
| Playback controls and cameras | Nine frontend tests and paged playback API tests | Play/pause/restart/step/speed, cameras, layers, timeline scrub and render density inspected | Passed |
| Windows launch | Port-selection and launcher error-path tests | Launcher-equivalent production serving/browser smoke passed | Passed |

## Design constraints and defaults

- The uploaded CSV is read-only and is never overwritten or copied into the final scenario directory.
- Simulation and rendering are separated: numerical concentration uses every computational parcel, while rendering uses deterministic decimation controlled by the user.
- HDF5 records deterministic, visualization-bounded playback-frame snapshots rather than every parcel at every solver microstep. Full numerical counts, exact sensor contributors, embedded sensor values, static parcel identity/final state, and mass ledgers remain available for audit and CSV reconstruction.
- Final outputs are published atomically. Temporary working files live outside the final scenario directory or use hidden partial names removed on failure.
- Synthetic wind is never silent. The supplied file runs with logged measured/interpolated wind by default; synthetic wind is used only when explicitly selected.
- This is a testing/research forward simulator, not CFD and not a regulatory dispersion model.

## Final verification evidence

- Python: `.venv/Scripts/python.exe -m pytest -q --basetemp <workspace-local-path> -p no:cacheprovider` — **81 passed**.
- Frontend: `pnpm --dir desktop test` — **9 passed**.
- Production frontend: `pnpm --dir desktop build` — passed; `desktop/dist/index.html` was served by the local FastAPI application.
- Current wind verification scenario: `logged-wind-test-verified-20260819`, using the supplied CSV with 6,190 `measured` and 5,925 `interpolated` samples and no synthetic or missing-zero wind.
- Output directory: `outputs/desktop-e2e/airdata-complete-flight` contains exactly:
  - `airdata-complete-flight_simulation_trace.h5` — 8,074,524 bytes;
  - `airdata-complete-flight_simulated_drone_sensor_log.csv` — 5,157,300 bytes, 12,115 rows and 36 columns.
- HDF5 summary: 12,115 sensor samples, 244 playback frames, 1,211.4 seconds, and a maximum full numerical parcel count of 1,467.
- Audit checks: selected contributor reconstruction matched; maximum CSV PM10 identity residual was `0.0 µg/m³`; total numerical mass-balance error was `-2.4868995751603507e-14 g`.
- Visual evidence: fourteen reviewed screenshots are stored in `evidence/screenshots`, ending with no-rerun HDF5 reload, timeline scrubbing, and active-plume render-density adjustment.
