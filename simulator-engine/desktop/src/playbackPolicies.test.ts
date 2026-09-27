import { describe, expect, it } from "vitest";
import { MAX_RENDER_DENSITY, MIN_RENDER_DENSITY, normalizeRenderDensity, renderDensityForKey } from "./playbackPolicies";

describe("playback render-density controls", () => {
  it("clamps pointer/input values and snaps them to the visible five-percent steps", () => {
    expect(normalizeRenderDensity(63)).toBe(65);
    expect(normalizeRenderDensity(-10)).toBe(MIN_RENDER_DENSITY);
    expect(normalizeRenderDensity(999)).toBe(MAX_RENDER_DENSITY);
  });

  it("supports arrows, pages, Home and End without exceeding the allowed range", () => {
    expect(renderDensityForKey(100, "ArrowLeft")).toBe(95);
    expect(renderDensityForKey(5, "ArrowDown")).toBe(5);
    expect(renderDensityForKey(45, "PageUp")).toBe(65);
    expect(renderDensityForKey(45, "PageDown")).toBe(25);
    expect(renderDensityForKey(45, "Home")).toBe(5);
    expect(renderDensityForKey(45, "End")).toBe(100);
    expect(renderDensityForKey(45, "Enter")).toBeNull();
  });
});
