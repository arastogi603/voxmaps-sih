# VoxMaps Interactive Application Status

Updated: 2026-08-17

## Completed milestones

### Baseline and architecture

- Inspected the repository structure, README, PLAN, configurations, tests, simulation modules, exporters, static visualization, and original Streamlit application.
- Established a clean pre-upgrade baseline: **50 passed in 5.31 seconds** using a workspace-local pytest temporary directory.
- Audited the supplied AirData CSV: 12,115 rows, 54 columns, 1,211.4 seconds, complete trajectory fields, and 6,190 valid paired wind rows (51.0937%).
- Selected the local FastAPI + React/TypeScript + React Three Fiber architecture while retaining the tested Python engine and legacy developer interfaces.

### Trace and two-file contract

- Added stable parcel IDs, emission/source metadata, emitted/deposited/exited state retention, and time-varying transport from each parcel's current position.
- Added configurable rectangular sensor-region sampling with exact contributor IDs, masses, classes, source-row linkage, voxel linkage, and true-versus-sensor concentrations.
- Added separate PM2.5/coarse channels with enforced true `PM10 = PM2.5 + coarse PM`, deterministic PM2.5/PM10 noise, and class-specific settling/deposition.
- Added raw-versus-used wind timelines and `measured`, `interpolated`, `synthetic_fallback`, or missing-measured provenance without using drone-motion fields as wind.
- Added the compressed/chunked HDF5 audit/playback trace, exact clean sensor-log CSV schema, HDF5 frame reload, contributor reconstruction, mass ledger, and atomic exactly-two-file publication.

### Local service

- Added immutable checksum-verified private uploads, AirData inspection, retained/excluded-column explanations, explicit mapping fallback, validation, estimates, and configuration save/load.
- Added one background simulation worker with eight genuine stages, elapsed status, cooperative cancellation, incomplete-output cleanup, output-folder selection, and existing-scenario protection.
- Added paged playback metadata/frame/sample/contributor endpoints and automatic registration of a completed trace without rerunning the solver.
- Added Windows extended-path handling and a noninteractive native-dialog bypass for automated workflow verification.

### React/Three.js application source

- Added the five-step Flight data → Pollution source → Atmosphere & model → Review & run → 3D playback workflow.
- Added drag/drop and picker upload, inspection/column mapping, offline 2D flight preview, click/drag/numeric stack placement, synchronized unit-labelled controls, explicit wind choices, preflight warnings, size estimates, progress, cancellation, reset, and configuration dialogs.
- Added synchronized 3D drone/path/stack/fine/coarse/deposited/wind/sensor/voxel layers, play/pause/restart/step/scrub/speed controls, follow/overview/reset cameras, layer and density controls, lazy bounded frame loading, plots with a moving cursor, and numerical-versus-rendered counts.
- Compiled production assets, including `desktop/dist/index.html`, passed the production build and were served successfully by the local application.

### Windows entry points and documentation

- Added `Launch VoxMaps Simulator.bat`, automatic local-port selection/browser opening, and plain-language startup errors.
- Added `Create VoxMaps Desktop Shortcut.bat` and its PowerShell shortcut helper.
- Reworked README from the first line around the no-terminal user workflow, supplied-flight wind decision, two-file/HDF5 contract, desktop architecture, limitations, and clearly separated legacy multi-artifact CLI.

## Current automated evidence

- Pre-upgrade baseline command:

  `python -m pytest -q --basetemp work/pytest-baseline-20260817-001 -p no:cacheprovider`

  Result: **50 passed in 5.31 seconds**.

- Current full Python command:

  `.venv/Scripts/python.exe -m pytest -q --basetemp <workspace-local-directory> -p no:cacheprovider`

  Result: **81 passed** on 2026-08-19.

- Frontend component/state command:

  `pnpm --dir desktop test`

  Result: **9 passed** on 2026-08-17.

- Production frontend command:

  `pnpm --dir desktop build`

  Result: **passed**; the compiled `desktop/dist/index.html` was served by FastAPI in the launcher-equivalent local smoke test.

- The 81-test Python suite includes the 50 legacy tests plus desktop upload/mapping/validation/jobs/configuration/playback/launcher tests, SFD workbook validation/provenance tests, and trace tests for sparse logged-wind interpolation, exact timestamp provenance, every-flight-sample output, irregular/duplicate telemetry timing, sensor volume, exact schemas, compression, reload, reconstruction, cancellation, deterministic runs, warm-up wind, separate sensor noise, raw/used wind provenance, and a 90-degree plume bend.

## Final verification completed

- Ran the production frontend build and served the compiled application through the launcher-equivalent local FastAPI path.
- Confirmed in the rebuilt browser UI that the supplied CSV is **Ready** in normal Logged wind timeline mode at 51.1% coverage; strict measured mode still rejects it as designed.
- Completed a full supplied-CSV trace run using logged wind: 6,190 exact measured rows, 5,925 interpolated rows, zero synthetic rows, and zero missing-wind/zero-advection rows.
- Selected the automated acceptance output folder, observed live simulation progress, and verified the completed scenario directory exposes exactly two files.
- Verified all 12,115 valid uploaded flight samples are present in the clean CSV and embedded HDF5 sensor table; solver and playback cadence remain independent.
- Reloaded the completed HDF5 without rerunning the solver and verified a selected CSV concentration row from exact HDF5 contributor records.
- Exercised play, pause, restart, stepping, speed, camera and layer controls, timeline scrubbing, and the repaired render-density control; inspected the synchronized plume, drone, wind, sensor region, and plots.
- Captured and inspected the major screens at desktop width for clipping, overlap, labels/units, contrast and workflow coherence.
- Ran the settled Python and frontend suites and the production build successfully.

## Final browser/E2E evidence

- Production frontend assets: `pnpm --dir desktop build` passed; `desktop/dist/index.html` was served successfully.
- Production application/launch smoke: launcher-equivalent FastAPI serving and browser connection passed.
- Current measured-wind verification scenario: `logged-wind-test-verified-20260819`, with `measured` on all 6,190 valid source rows and `interpolated` on the remaining 5,925 rows.
- HDF5: `outputs/desktop-e2e/airdata-complete-flight/airdata-complete-flight_simulation_trace.h5`, 8,074,524 bytes, 12,115 sensor samples, 244 playback frames, and maximum full numerical parcel count 1,467.
- Sensor CSV: `outputs/desktop-e2e/airdata-complete-flight/airdata-complete-flight_simulated_drone_sensor_log.csv`, 5,157,300 bytes, 12,115 rows and 36 columns.
- Exact directory assertion: passed; the scenario folder contains only the HDF5 trace and clean sensor CSV.
- Cross-file reconstruction: passed for the inspected sample; HDF5 contributor-derived concentrations matched the stored sensor row.
- Numerical audit: maximum CSV PM10 identity residual `0.0 µg/m³`; total mass-balance residual `-2.4868995751603507e-14 g`.
- HDF5 no-rerun reload: passed and captured in `evidence/screenshots/11-hdf5-reloaded.png`.
- Visual evidence: `evidence/screenshots/01-upload.png` through `14-density-active-plume.png` cover upload, inspection, source configuration, strict-wind rejection, synthetic selection, review, progress, completion, playback, reload, scrubbing, and render-density adjustment.
- Manual layout finding: the reviewed desktop-width workflow remained readable and coherent, with unit labels and research caveats visible and no blocking clipping or overlap in the captured major states.

## Material decisions and limitations

- The original Streamlit UI remains a legacy developer interface; it is not the primary application.
- The supplied flight runs in normal Logged wind timeline mode. Strict measured mode continues to enforce the default 80% coverage threshold and therefore rejects this file unless its strict settings are changed.
- The solver's full numerical parcel population is separate from deterministic HDF5 rendering snapshots and browser density reduction.
- Final scenario files appear only after both outputs pass validation; cancelled/failed partial work is removed.
- The model remains a spatially uniform time-varying wind, seeded parcel random-walk, simplified settling/deposition, prescribed-plume-rise research simulator—not CFD or a regulatory model.
