export type WindMode = "measured" | "synthetic" | "auto";
export type UiWindMode = "measured" | "synthetic_fallback" | "strict_measured";

export interface ProjectConfig {
  name: string;
  scenario_id: string;
  random_seed: number;
}

export interface EngineConfig {
  project: ProjectConfig;
  input: {
    flight_csv: string | null;
    column_mapping: Record<string, string>;
    invalid_row_policy: "drop_and_report" | "fail";
  };
  coordinates: {
    reference_mode: "first_valid_flight_point" | "configured" | "source_location";
    reference_latitude: number | null;
    reference_longitude: number | null;
    reference_altitude_m: number;
    flat_ground: boolean;
  };
  wind: {
    mode: WindMode;
    minimum_numeric_coverage: number;
    maximum_interpolation_gap_s: number;
    height_adjustment_enabled: boolean;
    reference_height_m: number;
    power_law_exponent: number;
    synthetic: {
      base_speed_mps: number;
      base_direction_from_deg: number;
      speed_variability_mps: number;
      direction_variability_deg: number;
      gust_period_s: number;
    };
  };
  source: {
    name: string;
    latitude: number | null;
    longitude: number | null;
    offset_east_from_flight_centre_m: number;
    offset_north_from_flight_centre_m: number;
    ground_elevation_m: number;
    stack_height_m: number;
    plume_rise_m: number;
    stack_diameter_m: number;
    exit_velocity_mps: number;
    exhaust_temperature_k: number;
    ambient_temperature_k: number;
    pm25_emission_g_s: number;
    pm10_total_emission_g_s: number;
    emission_start_s: number;
    emission_end_s: number | null;
    emission_schedule: Array<{
      time_s: number;
      pm25_emission_g_s: number;
      pm10_total_emission_g_s: number;
    }>;
  };
  simulation: {
    timestep_s: number;
    trajectory_sample_interval_s: number;
    particles_per_timestep: number;
    warmup_s: number;
    preflight_wind_policy: "none" | "hold_first_wind";
    horizontal_padding_m: number;
    vertical_min_m: number;
    vertical_max_m: number;
    maximum_particles: number;
  };
  dispersion: {
    horizontal_diffusivity_m2_s: number;
    vertical_diffusivity_m2_s: number;
    fine_settling_velocity_mps: number;
    coarse_settling_velocity_mps: number;
    fine_deposition_velocity_mps: number;
    coarse_deposition_velocity_mps: number;
    fine_decay_rate_s: number;
    coarse_decay_rate_s: number;
  };
  voxel: {
    size_x_m: number;
    size_y_m: number;
    size_z_m: number;
    smoothing_enabled: boolean;
    smoothing_radius_voxels: number;
  };
  background: {
    pm25_ug_m3: number;
    coarse_pm_ug_m3: number;
  };
  sensor: {
    sample_interval_s: number;
    response_time_pm25_s: number;
    response_time_pm10_s: number;
    transport_delay_s: number;
    additive_noise_std_ug_m3: number;
    multiplicative_noise_std_fraction: number;
    calibration_slope_pm25: number;
    calibration_slope_pm10: number;
    calibration_offset_pm25_ug_m3: number;
    calibration_offset_pm10_ug_m3: number;
    bias_pm25_ug_m3: number;
    bias_pm10_ug_m3: number;
    quantization_ug_m3: number;
    lower_detection_limit_ug_m3: number;
    upper_saturation_limit_ug_m3: number;
    dropout_probability: number;
    missing_value: number | null;
    humidity_interference_enabled: boolean;
    humidity_coefficient_fraction_per_pct: number;
    rotor_wash_dilution_factor: number;
    allow_physically_inconsistent_raw_sensor_noise: boolean;
  };
  output: {
    write_particle_ground_truth: boolean;
    parquet_compression: string;
  };
  batch: null;
}

export interface DesktopConfig {
  engine: EngineConfig;
  emissions: {
    pm25_emission_g_s: number;
    coarse_pm_emission_g_s: number;
  };
  background: {
    pm25_ug_m3: number;
    pm10_ug_m3: number;
  };
  wind_policy: {
    mode: UiWindMode;
    interpolation: "linear_vector" | "nearest";
  };
  sensor_region: {
    size_x_m: number;
    size_y_m: number;
    size_z_m: number;
  };
  playback: {
    frame_interval_s: number;
    visualization_parcel_limit: number;
  };
  sensor_model: {
    pm25_noise_std_ug_m3: number;
    pm10_noise_std_ug_m3: number;
    pm25_bias_ug_m3: number;
    pm10_bias_ug_m3: number;
  };
}

export interface CoordinatePoint {
  latitude_deg: number;
  longitude_deg: number;
  altitude_m?: number;
  elapsed_time_s?: number;
  x_east_m?: number;
  y_north_m?: number;
  z_up_m?: number;
}

export interface ColumnDisposition {
  column: string;
  normalized_name?: string;
  category?: string;
  reason: string;
}

export interface Inspection {
  filename: string;
  source_filename?: string;
  checksum_sha256?: string;
  row_count: number;
  usable_row_count?: number;
  duration_s: number;
  first_utc?: string | null;
  last_utc?: string | null;
  gps_extent: {
    min_latitude: number;
    max_latitude: number;
    min_longitude: number;
    max_longitude: number;
  };
  altitude_range_m: { min: number; max: number };
  detected_columns: Record<string, string | null>;
  wind_columns: { speed?: string | null; direction?: string | null };
  valid_paired_wind_rows: number;
  valid_paired_wind_coverage: number;
  sfd_original_wind_rows?: number;
  sfd_generated_wind_rows?: number;
  wind_source_counts?: Record<string, number>;
  input_format?: "airdata_csv" | "sfd_xlsx" | string;
  worksheet?: string | null;
  sampling_frequency_hz?: number | null;
  warnings: string[];
  preview_points: CoordinatePoint[];
  retained_columns: ColumnDisposition[];
  excluded_columns: ColumnDisposition[];
  requires_mapping: boolean;
  available_columns: string[];
  mapping?: Record<string, string>;
}

export interface UploadResponse {
  upload_id: string;
  inspection: Inspection;
}

export interface ValidationResponse {
  valid: boolean;
  config: DesktopConfig;
  warnings: string[];
  errors: string[];
  estimate?: {
    simulation_steps?: number;
    emitted_parcels?: number;
    playback_frames?: number;
    sensor_samples?: number;
    estimated_memory_mb?: number;
    estimated_runtime_s?: number;
    [key: string]: number | string | undefined;
  };
}

export type JobState =
  | "queued"
  | "running"
  | "cancelling"
  | "cancelled"
  | "failed"
  | "completed";

export interface JobStatus {
  job_id: string;
  state: JobState;
  progress: number;
  stage: string;
  stage_index: number;
  stage_count: number;
  message: string;
  elapsed_s: number;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  result?: JobResult | null;
}

export interface JobResult {
  job_id?: string;
  simulation_id: string;
  scenario_name: string;
  output_folder: string;
  output_files: Array<{ name: string; path: string; size_bytes?: number }>;
  trace_path?: string;
  sensor_log_path?: string;
  playback_id?: string;
  mass_balance?: Record<string, number>;
  warnings?: string[];
  summary?: Record<string, number | string>;
}

export type ParticleClass = "fine" | "coarse" | "deposited";

export interface PlaybackParticle {
  id: number;
  particle_class: ParticleClass;
  x: number;
  y: number;
  z: number;
  mass_g?: number;
  state?: "active" | "deposited" | "removed";
}

export interface PlaybackFrame {
  index: number;
  elapsed_time_s: number;
  utc_time?: string | null;
  drone: { x: number; y: number; z: number };
  particles: PlaybackParticle[];
  wind_speed_mps: number;
  wind_direction_deg: number;
  wind_source?: string;
  pm25_true_ug_m3?: number;
  pm10_true_ug_m3?: number;
  pm25_sensor_ug_m3?: number;
  pm10_sensor_ug_m3?: number;
  sensor_region?: { size_x_m: number; size_y_m: number; size_z_m: number };
  numerical_particle_count?: number;
  rendered_particle_count?: number;
}

export interface PlaybackSample {
  sample_id: number | string;
  elapsed_time_s: number;
  utc_time?: string | null;
  x_east_m: number;
  y_north_m: number;
  z_up_m: number;
  latitude_deg?: number;
  longitude_deg?: number;
  altitude_msl_m?: number;
  pm25_true_ug_m3: number;
  pm10_true_ug_m3: number;
  pm25_sensor_ug_m3: number;
  pm10_sensor_ug_m3: number;
  wind_speed_used_mps: number;
  wind_direction_used_deg: number;
  fine_contributing_parcels?: number;
  coarse_contributing_parcels?: number;
  sampling_region_id?: string;
  voxel_id?: string;
  quality_flags?: string;
}

export interface PlaybackMetadata {
  playback_id: string;
  simulation_id: string;
  scenario_name?: string;
  frame_count: number;
  sample_count: number;
  duration_s: number;
  frame_interval_s?: number;
  coordinate_origin?: { latitude_deg: number; longitude_deg: number; altitude_m?: number };
  source?: { x: number; y: number; z: number; stack_height_m: number; latitude?: number; longitude?: number };
  flight_path?: Array<{ x: number; y: number; z: number }>;
  numerical_particle_count?: number;
  warnings?: string[];
  mass_balance?: Record<string, number>;
}

export interface PlaybackBundle {
  metadata: PlaybackMetadata;
  frames: PlaybackFrame[];
  samples: PlaybackSample[];
}

export type AppStep = "upload" | "source" | "model" | "review" | "playback";
