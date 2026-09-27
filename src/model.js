// A deterministic, transparent scenario engine for the SIH demonstration.
// These values are illustrative. They are not WRF-Chem output or a validated forecast.

export const GRID = { west: 76.78, east: 77.55, south: 28.30, north: 29.02, columns: 30, rows: 28 };
export const DEMO_START_UTC = '2026-09-26T12:30:00Z';

export const SCENARIOS = {
  baseline: { label: 'Typical dispersion', short: 'Baseline', detail: 'Daily mixing and moderate north-westerly flow.', plume: 0.45, inversion: 0.35, wind: 1 },
  inversion: { label: 'Strong inversion', short: 'Inversion', detail: 'A shallow boundary layer traps emissions overnight.', plume: 0.55, inversion: 1, wind: 0.72 },
  stubble: { label: 'Stubble plume', short: 'Stubble', detail: 'A regional plume advects into Delhi NCR from the north-west.', plume: 1.45, inversion: 0.5, wind: 1.12 },
  compound: { label: 'Plume + inversion', short: 'Compound', detail: 'Regional inflow arrives while local mixing is suppressed.', plume: 1.5, inversion: 1, wind: 0.8 },
};

export const POLLUTANTS = {
  aqi: { label: 'AQI proxy', unit: 'index', legend: 'PM-based indicative index' },
  pm25: { label: 'PM2.5', unit: 'µg/m³', legend: 'Fine particulate concentration' },
  pm10: { label: 'PM10', unit: 'µg/m³', legend: 'Total particulate concentration' },
  o3: { label: 'O₃', unit: 'µg/m³', legend: 'Ground-level ozone scenario' },
  nox: { label: 'NOₓ', unit: 'µg/m³', legend: 'Nitrogen oxides scenario' },
  inversion: { label: 'Inversion', unit: 'index', legend: 'Mixing suppression index' },
};

export const SOURCES = [
  { id: 'regional', name: 'Regional agricultural fire corridor', type: 'Regional inflow', lng: 76.88, lat: 28.93, confidence: 'Hypothesis', color: '#ff9648' },
  { id: 'urban', name: 'Central Delhi traffic corridor', type: 'Urban emission', lng: 77.215, lat: 28.63, confidence: 'Hypothesis', color: '#ff5f66' },
  { id: 'east', name: 'East NCR industrial cluster', type: 'Industrial area', lng: 77.40, lat: 28.65, confidence: 'Hypothesis', color: '#ffca58' },
];

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const gaussian = (x, y, cx, cy, rx, ry) => Math.exp(-0.5 * (((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2));
const round = (value, digits = 0) => Number(value.toFixed(digits));

// CPCB breakpoint interpolation for PM only. It is deliberately labelled a proxy:
// the official AQI also needs prescribed averaging windows and pollutant coverage.
const PM25_BREAKS = [[0, 30, 0, 50], [30, 60, 50, 100], [60, 90, 100, 200], [90, 120, 200, 300], [120, 250, 300, 400], [250, 500, 400, 500]];
const PM10_BREAKS = [[0, 50, 0, 50], [50, 100, 50, 100], [100, 250, 100, 200], [250, 350, 200, 300], [350, 430, 300, 400], [430, 800, 400, 500]];

function subIndex(value, ranges) {
  const segment = ranges.find((range) => value <= range[1]) || ranges[ranges.length - 1];
  const [low, high, indexLow, indexHigh] = segment;
  return clamp(indexLow + ((value - low) / (high - low)) * (indexHigh - indexLow), 0, 500);
}

export function aqiCategory(value) {
  if (value <= 50) return { name: 'Good', color: '#35b779' };
  if (value <= 100) return { name: 'Satisfactory', color: '#99c45a' };
  if (value <= 200) return { name: 'Moderate', color: '#f3c74c' };
  if (value <= 300) return { name: 'Poor', color: '#f29043' };
  if (value <= 400) return { name: 'Very poor', color: '#e95363' };
  return { name: 'Severe', color: '#a64b8c' };
}

export function scenarioAt(lng, lat, hour, scenarioKey = 'compound', coupled = true) {
  const scenario = SCENARIOS[scenarioKey] || SCENARIOS.compound;
  const dayPhase = ((hour + 18) % 24) / 24;
  const sunshine = Math.max(0, Math.sin(Math.PI * dayPhase));
  const nightFactor = 1 - sunshine;
  const front = clamp((hour - 5) / 18, 0, 1);
  const travel = clamp((hour - 7) / 35, 0, 1);
  const pulse = 0.66 + 0.34 * Math.sin((hour / 72) * Math.PI);
  const windSpeed = (2.5 + 0.8 * Math.sin(hour / 8)) * scenario.wind;
  const plumeX = 76.95 + 0.22 * travel;
  const plumeY = 28.91 - 0.25 * travel;
  const regionalPlume = scenario.plume * front * pulse * gaussian(lng, lat, plumeX, plumeY, 0.20 + 0.10 * travel, 0.12 + 0.08 * travel);
  const city = gaussian(lng, lat, 77.22, 28.62, 0.16, 0.13);
  const east = gaussian(lng, lat, 77.43, 28.66, 0.085, 0.10);
  const south = gaussian(lng, lat, 77.08, 28.47, 0.12, 0.09);
  const urban = 0.86 * city + 0.42 * east + 0.27 * south;
  const inversion = clamp(scenario.inversion * (0.36 + 0.64 * nightFactor) + 0.18 * regionalPlume, 0, 1);
  const basePbl = 310 + 610 * sunshine;
  const uncoupledPbl = basePbl * (1 - 0.48 * inversion);
  const rawPm25 = 28 + 91 * urban + 112 * regionalPlume;
  // A minimal two-way loop: aerosol loading attenuates heating, reducing PBL;
  // the shallower PBL then raises near-surface concentration.
  const aerosolCooling = coupled ? clamp((rawPm25 - 35) / 360, 0, 0.32) : 0;
  const pbl = Math.max(135, uncoupledPbl * (1 - aerosolCooling));
  const mixingRatio = clamp(basePbl / pbl, 1, 2.7);
  const pm25 = clamp(rawPm25 * (0.75 + 0.25 * mixingRatio) / Math.sqrt(scenario.wind), 6, 420);
  const pm10 = clamp(pm25 * (1.43 + 0.13 * east) + 27 * south, 15, 630);
  const nox = clamp(14 + 83 * urban * (1 + 0.28 * inversion), 4, 240);
  const o3 = clamp(29 + 66 * sunshine + 18 * regionalPlume - 26 * urban * nightFactor - 23 * aerosolCooling, 8, 180);
  const aqi = round(Math.max(subIndex(pm25, PM25_BREAKS), subIndex(pm10, PM10_BREAKS)));
  const temperature = 27 + 5 * sunshine - 1.8 * aerosolCooling - 0.7 * inversion;

  return {
    lng, lat, hour, pm25: round(pm25, 1), pm10: round(pm10, 1), o3: round(o3, 1), nox: round(nox, 1),
    aqi, inversion: round(inversion * 100), pbl: round(pbl), wind: round(windSpeed, 1),
    temperature: round(temperature, 1), aerosolCooling: round(aerosolCooling * 100),
    regionalShare: round(100 * (112 * regionalPlume) / Math.max(rawPm25, 1)),
    cityShare: round(100 * (91 * urban) / Math.max(rawPm25, 1)),
  };
}

export function makeGrid(hour, scenarioKey, coupled = true) {
  const cells = [];
  const deltaLng = (GRID.east - GRID.west) / GRID.columns;
  const deltaLat = (GRID.north - GRID.south) / GRID.rows;
  for (let row = 0; row < GRID.rows; row += 1) {
    for (let column = 0; column < GRID.columns; column += 1) {
      const west = GRID.west + column * deltaLng;
      const south = GRID.south + row * deltaLat;
      const sample = scenarioAt(west + deltaLng / 2, south + deltaLat / 2, hour, scenarioKey, coupled);
      cells.push({ ...sample, row, column, west, south, east: west + deltaLng, north: south + deltaLat });
    }
  }
  return cells;
}

export function makeTimeline(scenarioKey, coupled = true) {
  return Array.from({ length: 73 }, (_, hour) => {
    const center = scenarioAt(77.215, 28.63, hour, scenarioKey, coupled);
    const north = scenarioAt(77.08, 28.82, hour, scenarioKey, coupled);
    return { hour, aqi: round((center.aqi + north.aqi) / 2), pm25: round((center.pm25 + north.pm25) / 2), pbl: round((center.pbl + north.pbl) / 2) };
  });
}

export function forecastTime(hour) {
  const date = new Date(new Date(DEMO_START_UTC).getTime() + hour * 3600_000);
  return date.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: true });
}

export function colorFor(metric, value) {
  const limits = metric === 'aqi' ? [50, 100, 200, 300, 400]
    : metric === 'pm25' ? [30, 60, 90, 120, 250]
      : metric === 'pm10' ? [50, 100, 250, 350, 430]
        : metric === 'o3' ? [40, 70, 100, 130, 160]
          : metric === 'nox' ? [30, 60, 90, 120, 170]
            : [20, 40, 60, 75, 90];
  const colors = ['#3ec994', '#9bd36a', '#f4ce55', '#f79351', '#eb5963', '#a657a1'];
  return colors[limits.findIndex((limit) => value <= limit) < 0 ? 5 : limits.findIndex((limit) => value <= limit)];
}

export function gridGeoJson(cells, metric, inset = 0) {
  const visualMax = { aqi: 400, pm25: 250, pm10: 430, o3: 160, nox: 170, inversion: 90 }[metric] || 400;
  return {
    type: 'FeatureCollection',
    features: cells.map((cell) => ({
      type: 'Feature',
      properties: {
        id: `${cell.row}-${cell.column}`,
        value: cell[metric],
        color: colorFor(metric, cell[metric]),
        // Relative display height, deliberately exaggerated to make the
        // modeled pattern legible at NCR scale; this is not physical altitude.
        height: Math.min(2400, 120 + (cell[metric] / visualMax) * 1900),
        ...cell,
      },
      geometry: { type: 'Polygon', coordinates: [[
        [cell.west + (cell.east - cell.west) * inset, cell.south + (cell.north - cell.south) * inset],
        [cell.east - (cell.east - cell.west) * inset, cell.south + (cell.north - cell.south) * inset],
        [cell.east - (cell.east - cell.west) * inset, cell.north - (cell.north - cell.south) * inset],
        [cell.west + (cell.east - cell.west) * inset, cell.north - (cell.north - cell.south) * inset],
        [cell.west + (cell.east - cell.west) * inset, cell.south + (cell.north - cell.south) * inset],
      ]] },
    })),
  };
}

export function buildExportRows(cells) {
  return cells.map(({ lat, lng, hour, pm25, pm10, o3, nox, aqi, inversion, pbl, wind, temperature }) =>
    ({ hour, latitude: lat, longitude: lng, pm25_ug_m3: pm25, pm10_ug_m3: pm10, o3_ug_m3: o3, nox_ug_m3: nox, aqi_pm_proxy: aqi, inversion_index: inversion, pbl_height_m: pbl, wind_m_s: wind, temperature_c: temperature, provenance: 'illustrative_scenario' }));
}
