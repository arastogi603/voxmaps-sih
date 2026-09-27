# SIH MoES alignment and scientific boundary

The SIH challenge asks for a Delhi NCR **72-hour, high-resolution, two-way weather–chemistry forecast** with PM2.5, PM10, ground-level O₃, NOₓ, inversion and stubble-plume behaviour. VOXMAPS contributes a useful field-sensing and explainability layer, but the supplied simulator artifacts alone cannot prove that full forecasting capability.

| Challenge capability | Demo now | Evidence needed before claiming implemented |
| --- | --- | --- |
| Delhi NCR 72-hour map | Interactive 0–72 h scenario grid and timeline | WRF-Chem or another coupled model output for actual forecast times and NCR domain. |
| Weather ↔ chemistry feedback | Deterministic aerosol–cooling–PBL–PM loop with on/off comparison | Physical coupling configuration, evaluation against observed temperature, wind, PBL and pollutant time series. |
| Winter inversion | Adjustable scenario with inversion strength and shallow PBL | Observed/simulated inversion diagnostics (profiles and PBL height), calibrated event cases. |
| Stubble-burning plume | Illustrative regional inflow corridor and compound event | Fire detections, emissions, transport/chemical boundary conditions and measured plume validation. |
| PM2.5/PM10/O₃/NOₓ | Four displayed forecast variables | Multi-pollutant observations and valid chemistry output, especially secondary O₃. Supplied drone data is PM-only. |
| Field-to-voxel reconstruction | 20-minute CSV sensor flight, wind provenance and 3D map; separate 5-minute H5 parcel trace | Validated spatial interpolation and uncertainty across more sites and times. |
| Runnable local simulation | The supplied version 0.2.0 Python forward engine now runs from the dashboard with bundled five-minute input; it produces its own H5 trace and clean sensor CSV with 3D playback | Independent flight/event validation, measured emissions and meteorology, validated transport and uncertainty, plus a separate coupled 72-hour forecasting model. This is not trained ML. |
| Probable source + action | Clearly labelled hypothesis markers and inspection guidance | Inverse model, uncertainty intervals, independent ground truth and defensible attribution. |
| Official AQI | PM-based **proxy** with category visualization | CPCB-compliant averaging, completeness and sub-index computation from eligible pollutant data. |

## Product story for judges

`VoxSky measurement → wind provenance → spatial/voxel reconstruction → coupled forecast model → 72-hour risk map → source hypothesis → targeted inspection`.

The field simulation now runs through the supplied Python engine. The 72-hour forecast and action stages remain **illustrative UI and equations**, not scientifically validated predictions. The five-minute simulator input/trace and 20-minute dashboard replay are separate runs; a strong presentation should keep them distinct and avoid reporting scenario values as live Delhi conditions.

## Recommended next technical steps

1. Obtain or run a Delhi NCR coupled meteorology–chemistry baseline, with versioned input emissions and weather boundary conditions.
2. Assemble observed station PM2.5, PM10, O₃, NOₓ and meteorology with quality flags, timestamps and matched grid cells; retain VoxSky runs as high-resolution local validation when available.
3. Backtest rolling 24/48/72-hour forecasts on held-out inversion and fire episodes. Report MAE/RMSE and episode detection for each lead time and pollutant, plus uncertainty calibration and comparison with a one-way baseline.
4. Connect model output via a backend contract containing `run_id`, issue time, valid time, units, model version, forecast value, confidence and provenance. Never mix a historical simulated replay with a live forecast under one run ID.
5. Only then upgrade the source panel from hypotheses to an evaluated inverse estimate with confidence and inspection priority.
