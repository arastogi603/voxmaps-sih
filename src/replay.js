import { colorFor } from './model.js';

function parseLine(line) {
  const fields = [];
  let field = '';
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"') {
      if (quoted && line[index + 1] === '"') { field += '"'; index += 1; }
      else quoted = !quoted;
    } else if (char === ',' && !quoted) {
      fields.push(field);
      field = '';
    } else field += char;
  }
  fields.push(field);
  return fields;
}

export function parseReplayCsv(text) {
  const lines = text.replace(/\r/g, '').split('\n').filter(Boolean);
  if (lines.length < 2) throw new Error('CSV has no data rows.');
  const headers = parseLine(lines[0]);
  const required = ['latitude_deg', 'longitude_deg', 'pm25_sensor_ug_m3', 'pm10_sensor_ug_m3', 'utc_time'];
  const missing = required.filter((header) => !headers.includes(header));
  if (missing.length) throw new Error(`Missing replay columns: ${missing.join(', ')}`);
  const index = Object.fromEntries(headers.map((header, i) => [header, i]));
  const stride = Math.max(1, Math.ceil((lines.length - 1) / 360));
  const samples = [];
  const windSources = {};
  let durationSeconds = 0;
  for (let lineNumber = 1; lineNumber < lines.length; lineNumber += 1) {
    const fields = parseLine(lines[lineNumber]);
    const windSource = fields[index.wind_source] || 'unknown';
    windSources[windSource] = (windSources[windSource] || 0) + 1;
    durationSeconds = Math.max(durationSeconds, Number(fields[index.elapsed_time_s] || 0));
    if (lineNumber % stride !== 1 && lineNumber !== lines.length - 1) continue;
    const lat = Number(fields[index.latitude_deg]);
    const lng = Number(fields[index.longitude_deg]);
    const pm25 = Number(fields[index.pm25_sensor_ug_m3]);
    if (![lat, lng, pm25].every(Number.isFinite)) continue;
    samples.push({
      lat, lng,
      sampleId: fields[index.sample_id],
      utc: fields[index.utc_time],
      altitude: Number(fields[index.height_above_takeoff_m] || 0),
      pm25: Number(pm25.toFixed(1)),
      pm10: Number(Number(fields[index.pm10_sensor_ug_m3] || 0).toFixed(1)),
      referencePm25: Number(Number(fields[index.pm25_true_ug_m3] || 0).toFixed(1)),
      wind: Number(Number(fields[index.wind_speed_used_mps] || 0).toFixed(1)),
      windSource,
      qualityFlags: fields[index.quality_flags] || '',
      voxel: fields[index.voxel_id] || '',
      fineCount: Number(fields[index.fine_contributing_parcels] || 0),
      coarseCount: Number(fields[index.coarse_contributing_parcels] || 0),
      color: colorFor('pm25', pm25),
    });
  }
  return { samples, totalRows: lines.length - 1, windSources, headers, durationSeconds, hotspots: [], encounters: [], playbackFrames: [], voxelMeta: null, sourceFile: 'Uploaded CSV' };
}

export async function loadBundledReplay() {
  const names = ['flight_track', 'plume_encounters', 'wind_provenance', 'voxels', 'playback_frames'];
  const [track, plume, wind, voxels, playback] = await Promise.all(names.map(async (name) => {
    const response = await fetch(`/data/${name}.json`);
    if (!response.ok) throw new Error(`Bundled ${name} data is unavailable.`);
    return response.json();
  }));
  return {
    samples: track.points.map((point) => ({
      ...point,
      sampleId: point.id,
      altitude: point.alt_m,
      referencePm25: point.ref_pm25,
      windSource: point.wind_source,
      qualityFlags: point.flags,
      voxel: point.voxel,
      fineCount: point.fine_n,
      coarseCount: point.coarse_n,
      color: colorFor('pm25', point.pm25),
    })),
    totalRows: track.meta.rows_in,
    windSources: wind.counts,
    durationSeconds: track.points.at(-1)?.elapsed_s || 1211,
    hotspots: plume.hotspots,
    encounters: plume.encounters,
    encounterSummary: plume.summary,
    playbackFrames: playback.frames,
    voxelMeta: voxels,
    sourceFile: track.meta.source_file,
    timeFirst: track.meta.time_first,
    timeLast: track.meta.time_last,
  };
}
