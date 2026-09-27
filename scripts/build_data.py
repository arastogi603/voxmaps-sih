#!/usr/bin/env python3
"""Build compact browser-friendly JSON assets for the VoxMaps SIH demo frontend.

Reads (read-only, never modifies inputs):
  - 20-minute live-demo simulated drone sensor CSV
  - 5-minute simulation trace H5 (voxmaps_interactive_simulation_trace v1.0.0)

Writes (created/overwritten):
  public/data/flight_track.json       downsampled flight points, true-vs-sensor PM
  public/data/plume_encounters.json   nonzero parcel hits + hotspot top-N
  public/data/wind_provenance.json    wind_source counts + raw coverage note
  public/data/voxels.json             H5 voxel grid / source / mass-balance metadata
  public/data/playback_frames.json    decimated H5 playback particle positions per frame
  public/data/manifest.json           provenance, sizes, thresholds, disclaimers

Stdlib only, except h5py which is required ONLY for the H5-derived files
(voxels.json, playback_frames.json). If h5py is missing, CSV outputs are still
written and the H5 outputs are skipped with a warning.

Repeatable usage (run from repo root with the supplied source bundle):
  python scripts/build_data.py --live-csv "<path>" --trace-h5 "<path>" --out-dir public/data
  python scripts/build_data.py --live-csv "<path>" --flight-stride 10 --hotspot-top 25

The source bundle is not committed; provide its local paths explicitly.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT_DIR = os.path.join(REPO_ROOT, "public", "data")

BG_PM25 = 20.0
BG_COARSE = 15.0
BG_PM10 = 35.0

DISCLAIMERS = [
    "Forward testing/research simulation only; not CFD and not a regulatory product.",
    "Sensor columns are synthetic (quantization/noise/lag applied); true columns include fixed background.",
    "20-minute live CSV carries brief plume transects; 5-minute H5 trace sensor log is background-only (zero contributing parcels).",
    "Wind 'used' values are interpolated where raw telemetry is missing (~49% raw gaps); see wind_provenance.json.",
    "10 Hz output rows are heavily hold/interpolated (sensor_interval_hold); treat ~1 Hz as independent samples.",
]


def fnum(v, nd=3):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, nd)


def inum(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def read_live_csv(path):
    """Read-only parse of the live-demo CSV. Returns (rows, fieldnames)."""
    with open(path, "r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return rows, fieldnames


def build_flight_track(rows, stride):
    pts = []
    n = len(rows)
    for i, r in enumerate(rows):
        is_last = i == n - 1
        if (i % stride != 0) and not is_last:
            continue
        lat = fnum(r.get("latitude_deg"), 6)
        lon = fnum(r.get("longitude_deg"), 6)
        if lat is None or lon is None:
            continue
        pts.append(
            {
                "id": inum(r.get("sample_id")),
                "utc": r.get("utc_time"),
                "elapsed_s": fnum(r.get("elapsed_time_s"), 1),
                "lat": lat,
                "lng": lon,
                "alt_m": fnum(r.get("height_above_takeoff_m"), 1),
                "pm25": fnum(r.get("pm25_sensor_ug_m3"), 1),
                "pm10": fnum(r.get("pm10_sensor_ug_m3"), 1),
                "ref_pm25": fnum(r.get("pm25_true_ug_m3"), 1),
                "ref_pm10": fnum(r.get("pm10_true_ug_m3"), 1),
                "ref_coarse": fnum(r.get("coarse_pm_true_ug_m3"), 1),
                "wind": fnum(r.get("wind_speed_used_mps"), 2),
                "wind_dir": fnum(r.get("wind_direction_used_deg"), 1),
                "wind_source": r.get("wind_source") or "unknown",
                "flags": r.get("quality_flags") or "",
                "state": r.get("flight_state") or "",
                "fine_n": inum(r.get("fine_contributing_parcels")),
                "coarse_n": inum(r.get("coarse_contributing_parcels")),
                "voxel": r.get("voxel_id") or "",
            }
        )
    return pts


def build_encounters(rows, hotspot_top):
    enc = []
    for r in rows:
        fine = inum(r.get("fine_contributing_parcels"))
        coarse = inum(r.get("coarse_contributing_parcels"))
        t25 = fnum(r.get("pm25_true_ug_m3"), 2)
        t10 = fnum(r.get("pm10_true_ug_m3"), 2)
        hit = (fine > 0) or (coarse > 0)
        above = ((t25 is not None and t25 > BG_PM25 + 1.0) or (t10 is not None and t10 > BG_PM10 + 1.0))
        if not (hit or above):
            continue
        enc.append(
            {
                "id": inum(r.get("sample_id")),
                "utc": r.get("utc_time"),
                "elapsed_s": fnum(r.get("elapsed_time_s"), 1),
                "lat": fnum(r.get("latitude_deg"), 6),
                "lng": fnum(r.get("longitude_deg"), 6),
                "alt_m": fnum(r.get("height_above_takeoff_m"), 1),
                "ref_pm25": t25,
                "ref_pm10": t10,
                "ref_coarse": fnum(r.get("coarse_pm_true_ug_m3"), 2),
                "pm25": fnum(r.get("pm25_sensor_ug_m3"), 1),
                "pm10": fnum(r.get("pm10_sensor_ug_m3"), 1),
                "fine_n": fine,
                "coarse_n": coarse,
                "wind": fnum(r.get("wind_speed_used_mps"), 2),
                "wind_source": r.get("wind_source") or "unknown",
            }
        )
    hotspots = sorted(enc, key=lambda e: (e["ref_pm25"] or 0), reverse=True)[:hotspot_top]
    summary = {
        "n_encounters": len(enc),
        "n_nonzero_parcels": sum(1 for e in enc if e["fine_n"] > 0 or e["coarse_n"] > 0),
        "max_ref_pm25": max((e["ref_pm25"] for e in enc), default=None),
        "max_ref_pm10": max((e["ref_pm10"] for e in enc), default=None),
        "background": {"pm25": BG_PM25, "coarse": BG_COARSE, "pm10": BG_PM10},
    }
    return enc, hotspots, summary


def build_wind(rows):
    sources = Counter((r.get("wind_source") or "unknown") for r in rows)
    n_raw_missing = sum(1 for r in rows if not (r.get("wind_speed_raw_mps") or "").strip())
    total = len(rows)
    return {
        "counts": dict(sources),
        "total_rows": total,
        "raw_missing_rows": n_raw_missing,
        "raw_missing_frac": round(n_raw_missing / total, 4) if total else None,
        "note": "wind_speed/direction_used_mps/deg fill ~49% raw gaps via linear_vector interpolation (max gap 30 s); H5 reports paired numeric coverage 51.7% < 80% minimum.",
    }


def _h5_str(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    try:
        import numpy as np  # noqa: F401  (only for type check)

        if v.__class__.__name__ == "bytes_":
            return bytes(v).decode("utf-8", errors="replace")
    except ImportError:
        pass
    return str(v)


def build_h5_outputs(h5_path, out_dir, playback_max_per_frame):
    """Read H5 read-only; write voxels.json + playback_frames.json. Returns meta dict."""
    import h5py

    info = {"h5_available": True}
    with h5py.File(h5_path, "r") as f:
        root_attrs = {k: (_h5_str(v) if isinstance(v, bytes) else (float(v) if hasattr(v, "item") else v)) for k, v in f.attrs.items()}

        def arr(name):
            return f[name][:].tolist()

        def scalar(name):
            v = f[name][()]
            return float(v) if hasattr(v, "item") else v

        vmin = [round(float(x), 2) for x in f["coordinates/voxel_minimum_m"][:]]
        vmax = [round(float(x), 2) for x in f["coordinates/voxel_maximum_m"][:]]
        vsize = [round(float(x), 2) for x in f["coordinates/voxel_size_m"][:]]
        dims = [round((mx - mn) / s, 1) for mx, mn, s in zip(vmax, vmin, vsize)]
        coord_attrs = {k: _h5_str(v) if isinstance(v, bytes) else float(v) if hasattr(v, "item") else v for k, v in f["coordinates"].attrs.items()}
        src_attrs = {}
        if "source" in f:
            src_attrs = {k: (float(v) if hasattr(v, "item") else (_h5_str(v) if isinstance(v, bytes) else v)) for k, v in f["source"].attrs.items()}
        contrib_attrs = {}
        if "contributors" in f:
            contrib_attrs = {k: (float(v) if hasattr(v, "item") else v) for k, v in f["contributors"].attrs.items()}
        mb = {k: round(float(f[f"mass_balance/{k}"][()]), 6) for k in [
            "total_emitted_fine_mass_g", "total_emitted_coarse_mass_g",
            "airborne_fine_mass_g", "airborne_coarse_mass_g",
            "deposited_fine_mass_g", "deposited_coarse_mass_g",
            "exited_fine_mass_g", "exited_coarse_mass_g",
            "numerical_mass_balance_error_g",
        ] if f"mass_balance/{k}" in f}
        final_state = list(f["parcels/static/final_state"][:])
        counts = Counter(int(x) for x in final_state)
        warnings = [_h5_str(x) for x in f["warnings"][:]] if "warnings" in f else []

        voxels = {
            "meta": {
                "trace_file": os.path.basename(h5_path),
                "simulation_id": root_attrs.get("simulation_id"),
                "scenario": root_attrs.get("scenario_name"),
                "schema": f"{root_attrs.get('schema_name')} {root_attrs.get('schema_version')}",
                "note": "20x20x10 m voxels; ENU meters relative to takeoff origin (flat-ground assumption).",
            },
            "grid": {"size_m": vsize, "min_m": vmin, "max_m": vmax, "dims_voxels": dims, "attrs": coord_attrs},
            "origin": {
                "lat": float(f["coordinates"].attrs.get("origin_latitude_deg", 0.0)),
                "lon": float(f["coordinates"].attrs.get("origin_longitude_deg", 0.0)),
                "alt_m": float(f["coordinates"].attrs.get("origin_altitude_m", 0.0)),
            },
            "source": src_attrs,
            "background": contrib_attrs,
            "mass_balance_g": mb,
            "parcels": {
                "total": len(final_state),
                "active": counts.get(0, 0),
                "deposited": counts.get(1, 0),
                "exited": counts.get(2, 0),
                "removed": counts.get(3, 0),
            },
            "warnings": warnings,
            "provenance": {"root_attrs": root_attrs},
        }
        with open(os.path.join(out_dir, "voxels.json"), "w", encoding="utf-8") as fh:
            json.dump(voxels, fh, separators=(",", ":"))

        # Playback: decimate each frame to <= max points (even stride).
        frame_times = [round(float(x), 1) for x in f["playback/frame_times_s"][:]]
        full_counts = [int(x) for x in f["playback/full_numerical_particle_count"][:]]
        offsets = [int(x) for x in f["playback/offsets"][:]]
        pos = f["playback/position_m"]
        mass = f["playback/mass_g"][:]
        frames = []
        for fi in range(len(frame_times)):
            s, e = offsets[fi], offsets[fi + 1]
            n_vis = e - s
            stride = max(1, (n_vis + playback_max_per_frame - 1) // playback_max_per_frame) if n_vis else 1
            idx = list(range(s, e, stride))[:playback_max_per_frame]
            pts = [[round(float(x), 1) for x in row] for row in pos[idx].tolist()] if idx else []
            ms = [round(float(mass[i]), 4) for i in idx] if idx else []
            frames.append({"t_s": frame_times[fi], "n_full": full_counts[fi], "n_visual": n_vis, "n_kept": len(idx), "points_en_m": pts, "mass_g": ms})
        playback = {
            "meta": {
                "trace_file": os.path.basename(h5_path),
                "frame_interval_s": 5.0,
                "n_frames": len(frame_times),
                "decimation": f"even stride per frame, cap {playback_max_per_frame} visual points/frame (visual_decimation_only=true; numerics use full set)",
                "frame": "local East-North-Up meters from takeoff origin; pair with voxels.json origin lat/lon",
            },
            "frames": frames,
        }
        with open(os.path.join(out_dir, "playback_frames.json"), "w", encoding="utf-8") as fh:
            json.dump(playback, fh, separators=(",", ":"))
        info.update({"n_frames": len(frame_times), "parcel_total": len(final_state), "root_attrs": root_attrs})
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build VoxMaps demo JSON assets (inputs read-only).")
    ap.add_argument("--live-csv", required=True, help="Path to the supplied 20-minute simulated sensor CSV.")
    ap.add_argument("--trace-h5", help="Path to the separate five-minute H5 trace (requires h5py).")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--flight-stride", type=int, default=10, help="Keep every Nth live row (~10 Hz / N = Hz).")
    ap.add_argument("--playback-max-per-frame", type=int, default=150)
    ap.add_argument("--hotspot-top", type=int, default=25)
    args = ap.parse_args(argv)

    os.makedirs(args.out_dir, exist_ok=True)
    t0 = dt.datetime.now(dt.timezone.utc)

    rows, fields = read_live_csv(args.live_csv)
    sim_ids = sorted({r.get("simulation_id", "") for r in rows})
    times = [r.get("utc_time", "") for r in rows if r.get("utc_time")]
    track = build_flight_track(rows, max(1, args.flight_stride))
    encounters, hotspots, summary = build_encounters(rows, args.hotspot_top)
    wind = build_wind(rows)

    with open(os.path.join(args.out_dir, "flight_track.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": {"source_file": os.path.basename(args.live_csv), "simulation_id": sim_ids, "rows_in": len(rows), "rows_out": len(track), "stride": max(1, args.flight_stride), "time_first": times[0] if times else None, "time_last": times[-1] if times else None, "shape": "points[] matches frontend replay sample fields (lat/lng/pm25/pm10/ref_pm25/wind/wind_source/flags)"}, "points": track}, fh, separators=(",", ":"))
    with open(os.path.join(args.out_dir, "plume_encounters.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": {"criteria": "fine_n>0 OR coarse_n>0 OR ref_pm25>21 OR ref_pm10>36 (background 20/15/35)", "hotspot_top": args.hotspot_top}, "summary": summary, "hotspots": hotspots, "encounters": encounters}, fh, separators=(",", ":"))
    with open(os.path.join(args.out_dir, "wind_provenance.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": {"source_file": os.path.basename(args.live_csv)}, **wind}, fh, separators=(",", ":"), indent=None)

    h5_info = {"h5_available": False}
    try:
        if args.trace_h5:
            h5_info = build_h5_outputs(args.trace_h5, args.out_dir, args.playback_max_per_frame)
    except ImportError as e:
        print(f"WARNING: h5py unavailable ({e}); skipping voxels.json + playback_frames.json", file=sys.stderr)
    except FileNotFoundError as e:
        print(f"WARNING: H5 not found ({e}); skipping voxels.json + playback_frames.json", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - report but keep CSV outputs
        print(f"WARNING: H5 processing failed ({type(e).__name__}: {e}); skipping H5 outputs", file=sys.stderr)

    outputs = []
    for name in ["flight_track.json", "plume_encounters.json", "wind_provenance.json", "voxels.json", "playback_frames.json"]:
        p = os.path.join(args.out_dir, name)
        if os.path.exists(p):
            outputs.append({"file": name, "bytes": os.path.getsize(p)})
    manifest = {
        "generated_at_utc": t0.isoformat(),
        "script": "scripts/build_data.py",
        "inputs": [
            {"file": os.path.basename(args.live_csv), "bytes": os.path.getsize(args.live_csv) if os.path.exists(args.live_csv) else None, "rows": len(rows), "columns": len(fields)},
            {"file": os.path.basename(args.trace_h5), "bytes": os.path.getsize(args.trace_h5) if os.path.exists(args.trace_h5) else None, "h5": h5_info} if args.trace_h5 else {"file": None, "h5": h5_info},
        ],
        "outputs": outputs,
        "params": {"flight_stride": max(1, args.flight_stride), "playback_max_per_frame": args.playback_max_per_frame, "hotspot_top": args.hotspot_top},
        "simulation_ids": sim_ids,
        "disclaimers": DISCLAIMERS,
    }
    with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    outputs.append({"file": "manifest.json", "bytes": os.path.getsize(os.path.join(args.out_dir, "manifest.json"))})

    print(f"rows_in={len(rows)} track_out={len(track)} encounters={len(encounters)} hotspots={len(hotspots)}")
    print(f"wind={wind['counts']} raw_missing_frac={wind['raw_missing_frac']}")
    print(f"h5={h5_info}")
    for o in outputs:
        print(f"wrote {o['file']} ({o['bytes']} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
