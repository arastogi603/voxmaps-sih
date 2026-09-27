import { describe, expect, it } from "vitest";
import { withBackgroundConcentration, withEmissionRate, hasAdequateMeasuredWind, sanitizeScenarioName, scenarioFromFilename } from "./configPolicies";
import { cloneDefaultConfig } from "./defaultConfig";

describe("desktop configuration policies", () => {
  it("keeps PM2.5 and non-overlapping coarse emission aliases synchronized with engine PM10 total", () => {
    const fineChanged = withEmissionRate(cloneDefaultConfig(), "pm25", 0.42);
    const changed = withEmissionRate(fineChanged, "coarse", 0.73);
    expect(changed.emissions).toEqual({ pm25_emission_g_s: 0.42, coarse_pm_emission_g_s: 0.73 });
    expect(changed.engine.source.pm25_emission_g_s).toBeCloseTo(0.42);
    expect(changed.engine.source.pm10_total_emission_g_s).toBeCloseTo(1.15);
  });

  it("enforces background PM10 >= PM2.5 and stores coarse only as the difference", () => {
    const raisedFine = withBackgroundConcentration(cloneDefaultConfig(), "pm25", 48);
    expect(raisedFine.background.pm10_ug_m3).toBe(48);
    expect(raisedFine.engine.background.coarse_pm_ug_m3).toBe(0);
    const loweredTotal = withBackgroundConcentration(raisedFine, "pm10", 30);
    expect(loweredTotal.background.pm25_ug_m3).toBe(30);
    expect(loweredTotal.background.pm10_ug_m3).toBe(30);
    expect(loweredTotal.engine.background.coarse_pm_ug_m3).toBe(0);
  });

  it("allows logged wind below the strict threshold while strict mode still rejects", () => {
    expect(hasAdequateMeasuredWind("measured", 0.51, 0.8)).toBe(true);
    expect(hasAdequateMeasuredWind("strict_measured", 0.51, 0.8)).toBe(false);
    expect(hasAdequateMeasuredWind("synthetic_fallback", 0.0, 0.8)).toBe(true);
  });

  it("makes safe deterministic scenario names from AirData filenames", () => {
    expect(scenarioFromFilename("Apr 14th (Flight) AirData.csv")).toBe("Apr-14th-Flight-AirData");
    expect(scenarioFromFilename("VOXMAPS Wind Gaps Interpolated.xlsx")).toBe("VOXMAPS-Wind-Gaps-Interpolated");
    expect(sanitizeScenarioName("  bend / test !! ")).toBe("bend-test");
  });
});
