import { afterEach, describe, expect, it, vi } from "vitest";
import { api, normalizeFrame, normalizeInspection, normalizeSample } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("API payload normalization", () => {
  it("normalizes AirData inspection aliases while preserving timestamp_utc mapping and paired-wind coverage", () => {
    const inspection = normalizeInspection({
      file_name: "flight.csv",
      row_count: 12115,
      flight_duration_s: 1211.4,
      first_timestamp_utc: "2026-04-14T03:58:17Z",
      last_timestamp_utc: "2026-04-14T04:18:28Z",
      gps_extent: { min_lat: 28.1, max_lat: 28.2, min_lon: 77.1, max_lon: 77.2 },
      altitude_range: { minimum: -0.3, maximum: 83.2 },
      mapping: { timestamp_utc: "datetime(utc)", latitude: "latitude", longitude: "longitude" },
      wind_columns: { wind_speed: "wind_speed(mph)", wind_direction: "wind_direction(degrees)" },
      wind_valid_rows: 6190,
      wind_coverage: 51.0937,
      sfd_original_wind_rows: 6190,
      sfd_generated_wind_rows: 5925,
      input_format: "sfd_xlsx",
      worksheet: "Complete Wind Data",
      path_preview: [{ lat: 28.1, lon: 77.1 }],
      warnings: ["paired wind is inadequate"],
    });
    expect(inspection.filename).toBe("flight.csv");
    expect(inspection.detected_columns.timestamp_utc).toBe("datetime(utc)");
    expect(inspection.valid_paired_wind_coverage).toBeCloseTo(0.510937);
    expect(inspection.wind_columns.speed).toBe("wind_speed(mph)");
    expect(inspection.sfd_original_wind_rows).toBe(6190);
    expect(inspection.sfd_generated_wind_rows).toBe(5925);
    expect(inspection.input_format).toBe("sfd_xlsx");
    expect(inspection.preview_points[0]).toMatchObject({ latitude_deg: 28.1, longitude_deg: 77.1 });
  });

  it("normalizes flattened playback frames and samples", () => {
    const frame = normalizeFrame({ frame_index: 7, time_s: 14, drone_x_m: 1, drone_y_m: 2, drone_z_m: 3, wind_speed_used_mps: 4, wind_direction_used_deg: 270, numerical_particle_count: 99, particles: [{ parcel_id: 5, class: "coarse", x_east_m: 8, y_north_m: 9, z_up_m: 10 }] }, 0);
    expect(frame.index).toBe(7);
    expect(frame.drone).toEqual({ x: 1, y: 2, z: 3 });
    expect(frame.particles[0]).toMatchObject({ id: 5, particle_class: "coarse", x: 8, y: 9, z: 10 });
    expect(frame.numerical_particle_count).toBe(99);
    const sample = normalizeSample({ sample_id: "s7", elapsed_time_s: 14, x_east_m: 1, y_north_m: 2, z_up_m: 3, pm25_true_ug_m3: 10, pm10_true_ug_m3: 20, pm25_sensor_ug_m3: 11, pm10_sensor_ug_m3: 21, wind_speed_used_mps: 4, wind_direction_used_deg: 270, sampling_region_id: "r7", voxel_id: "1:2:3" }, 0);
    expect(sample.sampling_region_id).toBe("r7");
    expect(sample.voxel_id).toBe("1:2:3");
  });

  it("omits a path to trigger the native HDF5 picker and bounds frame-window requests", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ playback_id: "p1" }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ frames: [] }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await api.loadPlayback();
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({});
    await api.getPlaybackFrames("p1", 48, 24, 5000);
    const requested = new URL(fetchMock.mock.calls[1][0], "http://localhost");
    expect(requested.searchParams.get("start")).toBe("48");
    expect(requested.searchParams.get("limit")).toBe("24");
    expect(requested.searchParams.get("particle_limit")).toBe("5000");
  });
});
