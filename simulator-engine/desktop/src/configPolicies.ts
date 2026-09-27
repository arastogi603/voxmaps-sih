import type { DesktopConfig, UiWindMode } from "./types";

export function withEmissionRate(config: DesktopConfig, channel: "pm25" | "coarse", value: number): DesktopConfig {
  const next = structuredClone(config);
  const safe = Math.max(0, value);
  if (channel === "pm25") next.emissions.pm25_emission_g_s = safe;
  else next.emissions.coarse_pm_emission_g_s = safe;
  next.engine.source.pm25_emission_g_s = next.emissions.pm25_emission_g_s;
  next.engine.source.pm10_total_emission_g_s = next.emissions.pm25_emission_g_s + next.emissions.coarse_pm_emission_g_s;
  return next;
}

export function withBackgroundConcentration(config: DesktopConfig, channel: "pm25" | "pm10", value: number): DesktopConfig {
  const next = structuredClone(config);
  const safe = Math.max(0, value);
  if (channel === "pm25") {
    next.background.pm25_ug_m3 = safe;
    next.background.pm10_ug_m3 = Math.max(next.background.pm10_ug_m3, safe);
  } else {
    next.background.pm10_ug_m3 = safe;
    next.background.pm25_ug_m3 = Math.min(next.background.pm25_ug_m3, safe);
  }
  next.engine.background.pm25_ug_m3 = next.background.pm25_ug_m3;
  next.engine.background.coarse_pm_ug_m3 = next.background.pm10_ug_m3 - next.background.pm25_ug_m3;
  return next;
}

export function hasAdequateMeasuredWind(mode: UiWindMode, pairedCoverage: number, minimumCoverage: number): boolean {
  return mode !== "strict_measured" || pairedCoverage >= minimumCoverage;
}

export function sanitizeScenarioName(value: string): string {
  return value.trimStart().replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/-{2,}/g, "-").replace(/^-|-$/g, "").slice(0, 80);
}

export function scenarioFromFilename(filename: string): string {
  return sanitizeScenarioName(filename.replace(/\.(csv|xlsx)$/i, "")) || "scenario";
}
