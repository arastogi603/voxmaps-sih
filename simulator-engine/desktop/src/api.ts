import { mockApi } from "./mockData";
import type {
  DesktopConfig,
  Inspection,
  JobResult,
  JobStatus,
  PlaybackBundle,
  PlaybackFrame,
  PlaybackMetadata,
  PlaybackSample,
  UploadResponse,
  ValidationResponse,
} from "./types";

const DEMO_MODE = import.meta.env.DEV && typeof window !== "undefined" && new URLSearchParams(window.location.search).get("demo") === "1";

export class ApiError extends Error {
  readonly status: number;
  readonly detail?: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof payload === "object" && payload && "detail" in payload
      ? String((payload as { detail: unknown }).detail)
      : `Request failed (${response.status})`;
    throw new ApiError(message, response.status, payload);
  }
  return payload as T;
}

function numberValue(value: unknown, fallback = 0): number {
  const converted = Number(value);
  return Number.isFinite(converted) ? converted : fallback;
}

function normalizeInspection(raw: Record<string, unknown>): Inspection {
  const gps = (raw.gps_extent ?? {}) as Record<string, unknown>;
  const altitude = (raw.altitude_range_m ?? raw.altitude_range ?? {}) as Record<string, unknown>;
  const wind = (raw.wind_columns ?? {}) as Record<string, unknown>;
  const previewRaw = (raw.preview_points ?? raw.flight_path_preview ?? raw.path_preview ?? []) as Array<Record<string, unknown>>;
  const detected = (raw.detected_columns ?? raw.mapping ?? {}) as Record<string, string | null>;
  const coverageValue = numberValue(raw.valid_paired_wind_coverage ?? raw.wind_coverage_fraction ?? raw.wind_coverage, 0);
  return {
    filename: String(raw.filename ?? raw.file_name ?? raw.source_filename ?? "uploaded-flight.csv"),
    source_filename: typeof raw.source_filename === "string" ? raw.source_filename : undefined,
    checksum_sha256: typeof raw.checksum_sha256 === "string" ? raw.checksum_sha256 : undefined,
    row_count: numberValue(raw.row_count),
    usable_row_count: raw.usable_row_count == null ? undefined : numberValue(raw.usable_row_count),
    duration_s: numberValue(raw.duration_s ?? raw.flight_duration_s),
    first_utc: (raw.first_utc ?? raw.first_timestamp_utc ?? null) as string | null,
    last_utc: (raw.last_utc ?? raw.last_timestamp_utc ?? null) as string | null,
    gps_extent: {
      min_latitude: numberValue(gps.min_latitude ?? gps.min_lat ?? gps.latitude_min),
      max_latitude: numberValue(gps.max_latitude ?? gps.max_lat ?? gps.latitude_max),
      min_longitude: numberValue(gps.min_longitude ?? gps.min_lon ?? gps.longitude_min),
      max_longitude: numberValue(gps.max_longitude ?? gps.max_lon ?? gps.longitude_max),
    },
    altitude_range_m: { min: numberValue(altitude.min ?? altitude.minimum), max: numberValue(altitude.max ?? altitude.maximum) },
    detected_columns: detected,
    wind_columns: {
      speed: (wind.speed ?? wind.wind_speed ?? detected.wind_speed ?? null) as string | null,
      direction: (wind.direction ?? wind.wind_direction ?? detected.wind_direction ?? null) as string | null,
    },
    valid_paired_wind_rows: numberValue(raw.valid_paired_wind_rows ?? raw.wind_valid_rows),
    valid_paired_wind_coverage: coverageValue > 1 ? coverageValue / 100 : coverageValue,
    sfd_original_wind_rows: raw.sfd_original_wind_rows == null ? undefined : numberValue(raw.sfd_original_wind_rows),
    sfd_generated_wind_rows: raw.sfd_generated_wind_rows == null ? undefined : numberValue(raw.sfd_generated_wind_rows),
    wind_source_counts: raw.wind_source_counts && typeof raw.wind_source_counts === "object" ? raw.wind_source_counts as Record<string, number> : undefined,
    input_format: typeof raw.input_format === "string" ? raw.input_format : undefined,
    worksheet: typeof raw.worksheet === "string" ? raw.worksheet : null,
    sampling_frequency_hz: raw.sampling_frequency_hz == null ? undefined : numberValue(raw.sampling_frequency_hz),
    warnings: Array.isArray(raw.warnings) ? raw.warnings.map(String) : [],
    preview_points: previewRaw.map((point) => ({
      latitude_deg: numberValue(point.latitude_deg ?? point.latitude ?? point.lat),
      longitude_deg: numberValue(point.longitude_deg ?? point.longitude ?? point.lon),
      altitude_m: point.altitude_m == null ? undefined : numberValue(point.altitude_m),
      elapsed_time_s: point.elapsed_time_s == null ? undefined : numberValue(point.elapsed_time_s),
      x_east_m: point.x_east_m == null ? undefined : numberValue(point.x_east_m),
      y_north_m: point.y_north_m == null ? undefined : numberValue(point.y_north_m),
      z_up_m: point.z_up_m == null ? undefined : numberValue(point.z_up_m),
    })),
    retained_columns: Array.isArray(raw.retained_columns) ? raw.retained_columns as Inspection["retained_columns"] : [],
    excluded_columns: Array.isArray(raw.excluded_columns) ? raw.excluded_columns as Inspection["excluded_columns"] : [],
    requires_mapping: Boolean(raw.requires_mapping ?? raw.mapping_required),
    available_columns: Array.isArray(raw.available_columns) ? raw.available_columns.map(String) : [],
    mapping: raw.mapping && typeof raw.mapping === "object" ? raw.mapping as Record<string, string> : undefined,
  };
}

function normalizeFrame(raw: Record<string, unknown>, index: number): PlaybackFrame {
  const drone = (raw.drone ?? raw.drone_position ?? {}) as Record<string, unknown>;
  const packed = raw.particles as Array<Record<string, unknown>> | undefined;
  const particles = packed?.map((p, particleIndex) => ({
    id: numberValue(p.id ?? p.parcel_id, particleIndex),
    particle_class: (p.particle_class ?? p.class ?? p.channel ?? (p.state === "deposited" ? "deposited" : "fine")) as PlaybackFrame["particles"][number]["particle_class"],
    x: numberValue(p.x ?? p.x_east_m),
    y: numberValue(p.y ?? p.y_north_m),
    z: numberValue(p.z ?? p.z_up_m),
    mass_g: p.mass_g == null ? undefined : numberValue(p.mass_g),
    state: p.state as PlaybackFrame["particles"][number]["state"],
  })) ?? [];
  return {
    index: numberValue(raw.index ?? raw.frame_index, index),
    elapsed_time_s: numberValue(raw.elapsed_time_s ?? raw.time_s),
    utc_time: (raw.utc_time ?? null) as string | null,
    drone: {
      x: numberValue(drone.x ?? drone.x_east_m ?? raw.drone_x_m),
      y: numberValue(drone.y ?? drone.y_north_m ?? raw.drone_y_m),
      z: numberValue(drone.z ?? drone.z_up_m ?? raw.drone_z_m),
    },
    particles,
    wind_speed_mps: numberValue(raw.wind_speed_mps ?? raw.wind_speed_used_mps),
    wind_direction_deg: numberValue(raw.wind_direction_deg ?? raw.wind_direction_used_deg),
    wind_source: typeof raw.wind_source === "string" ? raw.wind_source : undefined,
    pm25_true_ug_m3: raw.pm25_true_ug_m3 == null ? undefined : numberValue(raw.pm25_true_ug_m3),
    pm10_true_ug_m3: raw.pm10_true_ug_m3 == null ? undefined : numberValue(raw.pm10_true_ug_m3),
    pm25_sensor_ug_m3: raw.pm25_sensor_ug_m3 == null ? undefined : numberValue(raw.pm25_sensor_ug_m3),
    pm10_sensor_ug_m3: raw.pm10_sensor_ug_m3 == null ? undefined : numberValue(raw.pm10_sensor_ug_m3),
    sensor_region: raw.sensor_region as PlaybackFrame["sensor_region"],
    numerical_particle_count: raw.numerical_particle_count == null ? particles.length : numberValue(raw.numerical_particle_count),
    rendered_particle_count: raw.rendered_particle_count == null ? particles.length : numberValue(raw.rendered_particle_count),
  };
}

function normalizeSample(raw: Record<string, unknown>, index: number): PlaybackSample {
  return {
    sample_id: (raw.sample_id ?? index) as number | string,
    elapsed_time_s: numberValue(raw.elapsed_time_s ?? raw.time_s),
    utc_time: (raw.utc_time ?? null) as string | null,
    x_east_m: numberValue(raw.x_east_m),
    y_north_m: numberValue(raw.y_north_m),
    z_up_m: numberValue(raw.z_up_m),
    latitude_deg: raw.latitude_deg == null ? undefined : numberValue(raw.latitude_deg),
    longitude_deg: raw.longitude_deg == null ? undefined : numberValue(raw.longitude_deg),
    altitude_msl_m: raw.altitude_msl_m == null ? undefined : numberValue(raw.altitude_msl_m),
    pm25_true_ug_m3: numberValue(raw.pm25_true_ug_m3),
    pm10_true_ug_m3: numberValue(raw.pm10_true_ug_m3),
    pm25_sensor_ug_m3: numberValue(raw.pm25_sensor_ug_m3),
    pm10_sensor_ug_m3: numberValue(raw.pm10_sensor_ug_m3),
    wind_speed_used_mps: numberValue(raw.wind_speed_used_mps),
    wind_direction_used_deg: numberValue(raw.wind_direction_used_deg),
    fine_contributing_parcels: raw.fine_contributing_parcels == null ? undefined : numberValue(raw.fine_contributing_parcels),
    coarse_contributing_parcels: raw.coarse_contributing_parcels == null ? undefined : numberValue(raw.coarse_contributing_parcels),
    sampling_region_id: raw.sampling_region_id == null ? undefined : String(raw.sampling_region_id),
    voxel_id: raw.voxel_id == null ? undefined : String(raw.voxel_id),
    quality_flags: typeof raw.quality_flags === "string" ? raw.quality_flags : undefined,
  };
}

function unwrapArray<T>(raw: unknown, key: string): T[] {
  if (Array.isArray(raw)) return raw as T[];
  if (raw && typeof raw === "object" && Array.isArray((raw as Record<string, unknown>)[key])) {
    return (raw as Record<string, unknown>)[key] as T[];
  }
  return [];
}

export const api = {
  demoMode: DEMO_MODE,

  health: () => request<{ status: string; version?: string }>("/api/health"),

  async getDefaults(): Promise<{ config: DesktopConfig }> {
    if (DEMO_MODE) return mockApi.defaults();
    return request("/api/config/defaults");
  },

  async uploadFlight(file: File): Promise<UploadResponse> {
    if (DEMO_MODE) return mockApi.upload();
    const body = new FormData();
    body.append("file", file, file.name);
    const raw = await request<{ upload_id: string; inspection: Record<string, unknown> }>("/api/uploads", { method: "POST", body });
    return { upload_id: raw.upload_id, inspection: normalizeInspection(raw.inspection) };
  },

  async loadBundledDemoFlight(): Promise<UploadResponse & { output_folder: string }> {
    const raw = await request<{ upload_id: string; inspection: Record<string, unknown>; output_folder: string }>("/api/demo/load-flight", { method: "POST" });
    return { upload_id: raw.upload_id, inspection: normalizeInspection(raw.inspection), output_folder: raw.output_folder };
  },

  loadBundledDemoTrace(): Promise<{ playback_id: string }> {
    return request("/api/demo/load-trace", { method: "POST" });
  },

  async updateMapping(uploadId: string, mapping: Record<string, string>, minimumWindCoverage: number): Promise<UploadResponse> {
    const raw = await request<{ upload_id?: string; inspection?: Record<string, unknown> } | Record<string, unknown>>(`/api/uploads/${encodeURIComponent(uploadId)}/mapping`, {
      method: "PUT",
      body: JSON.stringify({ mapping, min_wind_coverage: minimumWindCoverage }),
    });
    const inspectionRaw = "inspection" in raw && raw.inspection ? raw.inspection : raw;
    return { upload_id: ("upload_id" in raw && String(raw.upload_id)) || uploadId, inspection: normalizeInspection(inspectionRaw as Record<string, unknown>) };
  },

  deleteUpload(uploadId: string) {
    if (DEMO_MODE) return Promise.resolve({ ok: true });
    return request(`/api/uploads/${encodeURIComponent(uploadId)}`, { method: "DELETE" });
  },

  async validateConfig(uploadId: string | null, config: DesktopConfig): Promise<ValidationResponse> {
    if (DEMO_MODE) return mockApi.validate(config);
    return request("/api/config/validate", { method: "POST", body: JSON.stringify({ upload_id: uploadId, config }) });
  },

  async chooseFolder(): Promise<{ path: string; cancelled: boolean; source?: string }> {
    if (DEMO_MODE) return { path: "C:\\Users\\Demo\\Documents\\VoxMaps Simulations", cancelled: false, source: "demo" };
    return request("/api/folders/select", { method: "POST", body: JSON.stringify({}) });
  },

  saveConfig(config: DesktopConfig): Promise<{ path?: string; saved?: boolean; cancelled?: boolean }> {
    if (DEMO_MODE) return Promise.resolve({ saved: true });
    return request<{ path?: string; saved?: boolean; cancelled?: boolean }>("/api/config/save", { method: "POST", body: JSON.stringify({ config }) });
  },

  loadConfig(): Promise<{ config: DesktopConfig; path?: string; cancelled?: boolean }> {
    if (DEMO_MODE) return mockApi.defaults();
    return request<{ config: DesktopConfig; path?: string; cancelled?: boolean }>("/api/config/load", { method: "POST", body: JSON.stringify({}) });
  },

  async createJob(payload: { upload_id: string; scenario_name: string; output_folder: string; config: DesktopConfig }): Promise<JobStatus> {
    if (DEMO_MODE) return { ...(await mockApi.job()), state: "queued", progress: 0, stage: "validating CSV", stage_index: 1, message: "Queued demo simulation", result: null };
    return request("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
  },

  async getJob(jobId: string): Promise<JobStatus> {
    if (DEMO_MODE) return mockApi.job();
    return request(`/api/jobs/${encodeURIComponent(jobId)}`);
  },

  cancelJob(jobId: string) {
    if (DEMO_MODE) return Promise.resolve({ job_id: jobId, state: "cancelling" });
    return request(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
  },

  resetJob(jobId: string) {
    if (DEMO_MODE) return Promise.resolve({ job_id: jobId, state: "cancelled" });
    return request(`/api/jobs/${encodeURIComponent(jobId)}/reset`, { method: "POST", body: JSON.stringify({}) });
  },

  async getResult(jobId: string): Promise<JobResult> {
    if (DEMO_MODE) return (await mockApi.job()).result!;
    return request(`/api/jobs/${encodeURIComponent(jobId)}/result`);
  },

  loadPlayback(path = ""): Promise<{ playback_id: string; metadata?: PlaybackMetadata; cancelled?: boolean }> {
    if (DEMO_MODE) return Promise.resolve({ playback_id: mockApi.playback.metadata.playback_id, metadata: mockApi.playback.metadata });
    return request("/api/playback/load", { method: "POST", body: JSON.stringify(path ? { path } : {}) });
  },

  async getPlayback(playbackId: string): Promise<PlaybackBundle> {
    if (DEMO_MODE) return mockApi.playback;
    const [metadataRaw, frames, samplesRaw] = await Promise.all([
      request<Record<string, unknown>>(`/api/playback/${encodeURIComponent(playbackId)}/metadata`),
      this.getPlaybackFrames(playbackId, 0, 24, 12000),
      request<unknown>(`/api/playback/${encodeURIComponent(playbackId)}/samples?start=0`),
    ]);
    const samples = unwrapArray<Record<string, unknown>>(samplesRaw, "samples").map(normalizeSample);
    const metadata = {
      ...metadataRaw,
      playback_id: String(metadataRaw.playback_id ?? playbackId),
      simulation_id: String(metadataRaw.simulation_id ?? "unknown"),
      frame_count: numberValue(metadataRaw.frame_count, frames.length),
      sample_count: numberValue(metadataRaw.sample_count, samples.length),
      duration_s: numberValue(metadataRaw.duration_s, frames.at(-1)?.elapsed_time_s ?? 0),
    } as PlaybackMetadata;
    return { metadata, frames, samples };
  },

  async getPlaybackFrames(playbackId: string, startIndex: number, limit = 24, particleLimit = 12000): Promise<PlaybackFrame[]> {
    if (DEMO_MODE) return mockApi.playback.frames.slice(startIndex, startIndex + limit);
    const params = new URLSearchParams({
      start: String(Math.max(0, Math.floor(startIndex))),
      end: String(Math.max(0, Math.floor(startIndex + limit))),
      limit: String(Math.max(1, Math.floor(limit))),
      particle_limit: String(Math.max(100, Math.floor(particleLimit))),
    });
    const raw = await request<unknown>(`/api/playback/${encodeURIComponent(playbackId)}/frames?${params}`);
    return unwrapArray<Record<string, unknown>>(raw, "frames").map((frame, offset) => normalizeFrame(frame, startIndex + offset));
  },
};

function cloneStructured<T>(value: T): T {
  return structuredClone(value);
}

export { normalizeFrame, normalizeInspection, normalizeSample };
