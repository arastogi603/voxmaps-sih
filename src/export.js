import { buildExportRows, gridGeoJson } from './model.js';

function download(filename, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

export function exportCsv(cells, scenario, hour) {
  const rows = buildExportRows(cells);
  const headers = Object.keys(rows[0]);
  const content = [headers.join(','), ...rows.map((row) => headers.map((header) => row[header]).join(','))].join('\n');
  download(`voxmaps-${scenario}-h${hour}-illustrative.csv`, content, 'text/csv;charset=utf-8');
}

export function exportGeoJson(cells, metric, scenario, hour) {
  const output = gridGeoJson(cells, metric);
  output.metadata = { scenario, lead_hour: hour, provenance: 'illustrative_scenario', disclaimer: 'Not an operational forecast or regulatory output' };
  download(`voxmaps-${scenario}-h${hour}-illustrative.geojson`, JSON.stringify(output), 'application/geo+json');
}
