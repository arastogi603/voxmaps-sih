import { cloneDefaultConfig } from "./defaultConfig";
import type { Inspection, JobStatus, PlaybackBundle, PlaybackFrame, PlaybackSample } from "./types";

const points = Array.from({ length: 180 }, (_, index) => ({
  latitude_deg: 28.6139 + Math.sin(index / 17) * 0.003 + index * 0.000002,
  longitude_deg: 77.209 + Math.cos(index / 23) * 0.004,
  elapsed_time_s: index,
}));

export const demoInspection: Inspection = {
  filename: "AirData_representative_flight.csv",
  row_count: 12115,
  usable_row_count: 12115,
  duration_s: 1211.4,
  first_utc: "2026-04-14T03:58:17.000Z",
  last_utc: "2026-04-14T04:18:28.400Z",
  gps_extent: {
    min_latitude: 28.6106,
    max_latitude: 28.6172,
    min_longitude: 77.2048,
    max_longitude: 77.2134,
  },
  altitude_range_m: { min: -0.3, max: 83.2 },
  detected_columns: {
    elapsed_time: "time(millisecond)",
    utc_time: "datetime(utc)",
    latitude: "latitude",
    longitude: "longitude",
    altitude: "height_above_takeoff(feet)",
  },
  wind_columns: { speed: "wind_speed(mph)", direction: "wind_direction(degrees)" },
  valid_paired_wind_rows: 6190,
  valid_paired_wind_coverage: 0.510937,
  sampling_frequency_hz: 10,
  warnings: [
    "Valid paired measured wind covers 51.1% of the flight; choose synthetic fallback or reduce the explicit coverage threshold.",
    "Negative height-above-takeoff samples are retained and flagged.",
  ],
  preview_points: points,
  retained_columns: [
    { column: "datetime(utc)", category: "time", reason: "UTC synchronization" },
    { column: "latitude", category: "position", reason: "Geospatial reconstruction" },
    { column: "longitude", category: "position", reason: "Geospatial reconstruction" },
    { column: "wind_speed(mph)", category: "wind", reason: "Raw measured atmospheric wind" },
  ],
  excluded_columns: [
    { column: "battery_percent", category: "battery", reason: "Not needed for flight, wind, or sensor interpretation" },
    { column: "rc_aileron", category: "controller", reason: "RC stick input is outside the clean sensor log contract" },
  ],
  requires_mapping: false,
  available_columns: ["time(millisecond)", "datetime(utc)", "latitude", "longitude", "height_above_takeoff(feet)", "wind_speed(mph)", "wind_direction(degrees)"],
};

function demoParticles(frame: number) {
  const particles = [];
  for (let i = 0; i < 900; i += 1) {
    const age = (i % 120) + frame * 0.45;
    const bend = Math.max(0, age - 45);
    const deposited = i % 19 === 0;
    particles.push({
      id: i,
      particle_class: deposited ? ("deposited" as const) : i % 3 ? ("fine" as const) : ("coarse" as const),
      x: -80 + Math.min(age, 45) * 2.2 + bend * 0.2 + Math.sin(i * 3.1) * 10,
      y: Math.max(0, bend) * 1.7 + Math.cos(i * 1.7) * 9,
      z: deposited ? 0.25 : Math.max(0.5, 65 - age * (i % 3 ? 0.05 : 0.35) + Math.sin(i) * 7),
      state: deposited ? ("deposited" as const) : ("active" as const),
    });
  }
  return particles;
}

const frames: PlaybackFrame[] = Array.from({ length: 120 }, (_, index) => ({
  index,
  elapsed_time_s: index * 2,
  utc_time: new Date(Date.UTC(2026, 3, 14, 3, 58, 17) + index * 2000).toISOString(),
  drone: { x: Math.sin(index / 18) * 130, y: Math.cos(index / 23) * 110, z: 25 + Math.sin(index / 9) * 12 },
  particles: demoParticles(index),
  wind_speed_mps: 3 + Math.sin(index / 11) * 0.8,
  wind_direction_deg: index < 45 ? 270 : 180,
  wind_source: "synthetic_fallback",
  pm25_true_ug_m3: 20 + Math.max(0, Math.sin((index - 22) / 8)) * 40,
  pm10_true_ug_m3: 35 + Math.max(0, Math.sin((index - 24) / 8)) * 63,
  pm25_sensor_ug_m3: 20 + Math.max(0, Math.sin((index - 25) / 9)) * 36,
  pm10_sensor_ug_m3: 35 + Math.max(0, Math.sin((index - 27) / 9)) * 57,
  sensor_region: { size_x_m: 20, size_y_m: 20, size_z_m: 10 },
  numerical_particle_count: 18243,
}));

const samples: PlaybackSample[] = frames.map((frame) => ({
  sample_id: frame.index,
  elapsed_time_s: frame.elapsed_time_s,
  utc_time: frame.utc_time,
  x_east_m: frame.drone.x,
  y_north_m: frame.drone.y,
  z_up_m: frame.drone.z,
  pm25_true_ug_m3: frame.pm25_true_ug_m3 ?? 0,
  pm10_true_ug_m3: frame.pm10_true_ug_m3 ?? 0,
  pm25_sensor_ug_m3: frame.pm25_sensor_ug_m3 ?? 0,
  pm10_sensor_ug_m3: frame.pm10_sensor_ug_m3 ?? 0,
  wind_speed_used_mps: frame.wind_speed_mps,
  wind_direction_used_deg: frame.wind_direction_deg,
}));

export const demoPlayback: PlaybackBundle = {
  metadata: {
    playback_id: "demo-playback",
    simulation_id: "demo-20260414",
    scenario_name: "representative-flight",
    frame_count: frames.length,
    sample_count: samples.length,
    duration_s: frames.at(-1)?.elapsed_time_s ?? 0,
    frame_interval_s: 2,
    source: { x: -80, y: 0, z: 0, stack_height_m: 60 },
    flight_path: frames.map((frame) => frame.drone),
    numerical_particle_count: 18243,
    warnings: ["Demonstration playback; model is not CFD or regulatory."],
  },
  frames,
  samples,
};

export const mockApi = {
  async defaults() {
    return { config: cloneDefaultConfig() };
  },
  async upload() {
    await new Promise((resolve) => setTimeout(resolve, 600));
    return { upload_id: "demo-upload", inspection: demoInspection };
  },
  async validate(config = cloneDefaultConfig()) {
    return {
      valid: true,
      config,
      warnings: demoInspection.warnings,
      errors: [],
      estimate: { simulation_steps: 1212, emitted_parcels: 24240, playback_frames: 606, estimated_memory_mb: 82, estimated_runtime_s: 18 },
    };
  },
  async job(): Promise<JobStatus> {
    return {
      job_id: "demo-job",
      state: "completed",
      progress: 1,
      stage: "writing output files",
      stage_index: 8,
      stage_count: 8,
      message: "Two scenario files validated and published.",
      elapsed_s: 11.4,
      created_at: new Date().toISOString(),
      result: {
        simulation_id: demoPlayback.metadata.simulation_id,
        scenario_name: "representative-flight",
        output_folder: "C:\\Users\\Demo\\Documents\\VoxMaps Simulations\\representative-flight",
        output_files: [
          { name: "representative-flight_simulation_trace.h5", path: "C:\\Demo\\representative-flight_simulation_trace.h5", size_bytes: 12582912 },
          { name: "representative-flight_simulated_drone_sensor_log.csv", path: "C:\\Demo\\representative-flight_simulated_drone_sensor_log.csv", size_bytes: 1458021 },
        ],
        playback_id: "demo-playback",
      },
    };
  },
  playback: demoPlayback,
};
