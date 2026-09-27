export const MIN_RENDER_DENSITY = 5;
export const MAX_RENDER_DENSITY = 100;
export const RENDER_DENSITY_STEP = 5;

export function normalizeRenderDensity(value: number): number {
  if (!Number.isFinite(value)) return MAX_RENDER_DENSITY;
  const stepped = Math.round(value / RENDER_DENSITY_STEP) * RENDER_DENSITY_STEP;
  return Math.max(MIN_RENDER_DENSITY, Math.min(MAX_RENDER_DENSITY, stepped));
}

export function renderDensityForKey(current: number, key: string): number | null {
  switch (key) {
    case "ArrowLeft":
    case "ArrowDown":
      return normalizeRenderDensity(current - RENDER_DENSITY_STEP);
    case "ArrowRight":
    case "ArrowUp":
      return normalizeRenderDensity(current + RENDER_DENSITY_STEP);
    case "PageDown":
      return normalizeRenderDensity(current - RENDER_DENSITY_STEP * 4);
    case "PageUp":
      return normalizeRenderDensity(current + RENDER_DENSITY_STEP * 4);
    case "Home":
      return MIN_RENDER_DENSITY;
    case "End":
      return MAX_RENDER_DENSITY;
    default:
      return null;
  }
}
