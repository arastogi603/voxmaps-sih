# Bundled simulation demo data

These files came from the supplied VoxMaps simulator bundle. They are **simulated research data**, not field measurements or a trained ML model.

| File | Role | Contents |
| --- | --- | --- |
| `voxmaps-five-minute-input.csv` | Raw input for a new solver run | AirData-style drone flight, 3,000 rows over about five minutes, with GPS, altitude and partially observed wind |
| `voxmaps-five-minute-demo-20260926_simulation_trace.h5` | Precomputed, matching trace | 60 playback frames, parcel and virtual-sensor data, wind provenance and mass-balance audit |

In the dashboard's **Simulation workspace**, choose **Load demo flight** to configure and run the original Python solver. Choose **Open supplied H5 replay** to inspect the precomputed trace immediately; opening it does not rerun the solver. A new run creates its own H5 and sensor CSV in the local simulator workspace rather than overwriting these files.

The five-minute drone sensor stays at background PM levels because its flight path does not intersect the modeled plume. The separate 20-minute dashboard replay is a different simulated run that *does* contain brief plume encounters; do not combine its readings with this H5 trace. Neither file contains the multi-day meteorology and chemistry needed to validate the illustrative 72-hour Delhi NCR outlook.
