import { GRID } from './model.js';

const palette = ['#36bd93', '#a1cf69', '#efd15f', '#f5a15a', '#e86672', '#9f5c9e'];
const thresholds = {
  aqi: [0, 50, 100, 200, 300, 400],
  pm25: [0, 30, 60, 90, 120, 250],
  pm10: [0, 50, 100, 250, 350, 430],
  o3: [0, 40, 70, 100, 130, 160],
  nox: [0, 30, 60, 90, 120, 170],
  inversion: [0, 20, 40, 60, 75, 90],
};

export function scaleFor(metric) {
  return { colors: palette, values: thresholds[metric] || thresholds.aqi };
}

function rgb(hex) {
  return [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16));
}

function colorAt(value, scale) {
  const { colors, values } = scale;
  if (value <= values[0]) return rgb(colors[0]);
  for (let index = 1; index < values.length; index += 1) {
    if (value <= values[index]) {
      const start = rgb(colors[index - 1]);
      const end = rgb(colors[index]);
      const blend = (value - values[index - 1]) / (values[index] - values[index - 1]);
      return start.map((channel, channelIndex) => Math.round(channel + (end[channelIndex] - channel) * blend));
    }
  }
  return rgb(colors.at(-1));
}

// Smooth the display between modeled cell centres. This changes rendering only;
// exported cells and click inspection retain the original 30 x 28 model values.
export function scenarioRaster(cells, metric) {
  const width = 240;
  const height = 220;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  if (!context) return null;
  const image = context.createImageData(width, height);
  const scale = scaleFor(metric);
  const values = cells.map((cell) => cell[metric]);
  const sample = (column, row) => values[row * GRID.columns + column] ?? 0;

  for (let y = 0; y < height; y += 1) {
    const row = (1 - y / (height - 1)) * GRID.rows - 0.5;
    const bottom = Math.max(0, Math.min(GRID.rows - 1, Math.floor(row)));
    const top = Math.max(0, Math.min(GRID.rows - 1, bottom + 1));
    const fy = Math.max(0, Math.min(1, row - bottom));
    for (let x = 0; x < width; x += 1) {
      const column = (x / (width - 1)) * GRID.columns - 0.5;
      const left = Math.max(0, Math.min(GRID.columns - 1, Math.floor(column)));
      const right = Math.max(0, Math.min(GRID.columns - 1, left + 1));
      const fx = Math.max(0, Math.min(1, column - left));
      const south = sample(left, bottom) * (1 - fx) + sample(right, bottom) * fx;
      const north = sample(left, top) * (1 - fx) + sample(right, top) * fx;
      const [red, green, blue] = colorAt(south * (1 - fy) + north * fy, scale);
      const edge = Math.min(x, width - 1 - x, y, height - 1 - y);
      const fade = Math.min(1, edge / 10);
      const offset = (y * width + x) * 4;
      image.data[offset] = red;
      image.data[offset + 1] = green;
      image.data[offset + 2] = blue;
      image.data[offset + 3] = Math.round(245 * fade);
    }
  }
  context.putImageData(image, 0, 0);
  return canvas.toDataURL('image/png');
}
