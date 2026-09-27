import { useMemo } from "react";
import type { PlaybackSample } from "../types";

interface Series {
  label: string;
  color: string;
  value: (sample: PlaybackSample) => number;
  dashed?: boolean;
}

export function SynchronizedPlots({ samples, currentTime }: { samples: PlaybackSample[]; currentTime: number }) {
  return (
    <div className="plots-grid">
      <MetricPlot title="PM2.5 vs time" unit="µg/m³" samples={samples} currentTime={currentTime} series={[
        { label: "True", color: "#41d9f5", value: (s) => s.pm25_true_ug_m3 },
        { label: "Sensor", color: "#f4d35e", value: (s) => s.pm25_sensor_ug_m3, dashed: true },
      ]} />
      <MetricPlot title="PM10 vs time" unit="µg/m³" samples={samples} currentTime={currentTime} series={[
        { label: "True", color: "#ff9d57", value: (s) => s.pm10_true_ug_m3 },
        { label: "Sensor", color: "#e8e2d6", value: (s) => s.pm10_sensor_ug_m3, dashed: true },
      ]} />
      <MetricPlot title="Wind speed vs time" unit="m/s" samples={samples} currentTime={currentTime} series={[
        { label: "Used", color: "#5de3ad", value: (s) => s.wind_speed_used_mps },
      ]} />
      <MetricPlot title="Wind direction vs time" unit="° from" fixedRange={[0, 360]} samples={samples} currentTime={currentTime} series={[
        { label: "Used", color: "#b895ff", value: (s) => s.wind_direction_used_deg },
      ]} />
      <MetricPlot title="Drone altitude vs time" unit="m" samples={samples} currentTime={currentTime} series={[
        { label: "Local z", color: "#7fb7ff", value: (s) => s.z_up_m },
      ]} />
    </div>
  );
}

function MetricPlot({ title, unit, samples, currentTime, series, fixedRange }: { title: string; unit: string; samples: PlaybackSample[]; currentTime: number; series: Series[]; fixedRange?: [number, number] }) {
  const width = 430;
  const height = 154;
  const pad = { left: 40, right: 13, top: 26, bottom: 26 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const start = samples[0]?.elapsed_time_s ?? 0;
  const end = samples.at(-1)?.elapsed_time_s ?? Math.max(1, currentTime);
  const duration = Math.max(1e-9, end - start);
  const downsampled = useMemo(() => {
    if (samples.length <= 600) return samples;
    const stride = Math.ceil(samples.length / 600);
    return samples.filter((_, index) => index % stride === 0 || index === samples.length - 1);
  }, [samples]);
  const values = downsampled.flatMap((sample) => series.map((item) => item.value(sample))).filter(Number.isFinite);
  let minValue = fixedRange?.[0] ?? Math.min(...values, 0);
  let maxValue = fixedRange?.[1] ?? Math.max(...values, 1);
  if (!fixedRange) {
    const margin = Math.max(0.1, (maxValue - minValue) * 0.12);
    minValue = Math.max(0, minValue - margin);
    maxValue += margin;
  }
  const valueRange = Math.max(1e-9, maxValue - minValue);
  const x = (time: number) => pad.left + ((time - start) / duration) * plotWidth;
  const y = (value: number) => pad.top + (1 - (value - minValue) / valueRange) * plotHeight;
  const cursorX = Math.max(pad.left, Math.min(width - pad.right, x(currentTime)));

  return (
    <div className="metric-plot">
      <div className="metric-plot-title"><strong>{title}</strong><div>{series.map((item) => <span key={item.label}><i style={{ background: item.color }} />{item.label}</span>)}</div></div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}, synchronized cursor at ${currentTime.toFixed(1)} seconds`}>
        <rect x={pad.left} y={pad.top} width={plotWidth} height={plotHeight} className="plot-bg" />
        {[0, 0.5, 1].map((fraction) => {
          const lineY = pad.top + fraction * plotHeight;
          const label = maxValue - fraction * valueRange;
          return <g key={fraction}><line x1={pad.left} x2={width - pad.right} y1={lineY} y2={lineY} className="plot-grid" /><text x={pad.left - 6} y={lineY + 4} textAnchor="end" className="plot-label">{formatAxis(label)}</text></g>;
        })}
        {series.map((item) => {
          const path = downsampled.map((sample, index) => `${index ? "L" : "M"}${x(sample.elapsed_time_s).toFixed(2)},${y(item.value(sample)).toFixed(2)}`).join(" ");
          return <path key={item.label} d={path} fill="none" stroke={item.color} strokeWidth="2" strokeDasharray={item.dashed ? "5 4" : undefined} vectorEffect="non-scaling-stroke" />;
        })}
        <line x1={cursorX} x2={cursorX} y1={pad.top - 3} y2={height - pad.bottom + 3} className="plot-cursor" />
        <circle cx={cursorX} cy={pad.top - 4} r="3" className="plot-cursor-dot" />
        <text x={pad.left} y={height - 7} className="plot-label">0:00</text>
        <text x={width - pad.right} y={height - 7} textAnchor="end" className="plot-label">{formatClock(duration)}</text>
        <text x={width - pad.right} y={15} textAnchor="end" className="plot-unit">{unit}</text>
      </svg>
    </div>
  );
}

function formatAxis(value: number) {
  if (Math.abs(value) >= 100) return value.toFixed(0);
  if (Math.abs(value) >= 10) return value.toFixed(1);
  return value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

function formatClock(seconds: number) {
  return `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
}
