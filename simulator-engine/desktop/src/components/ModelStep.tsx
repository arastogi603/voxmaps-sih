import { ArrowLeft, ArrowRight, Box, ChevronRight, CloudSun, Database, Gauge, Layers3, RadioTower, ShieldAlert, Sparkles, Timer, Waves, Wind } from "lucide-react";
import type { DesktopConfig, Inspection, UiWindMode } from "../types";
import { hasAdequateMeasuredWind } from "../configPolicies";
import { Card, RangeNumberField, SectionHeading, Segmented, SelectField, Toggle } from "./Controls";

export function ModelStep({ inspection, config, onConfig, onBack, onContinue }: {
  inspection: Inspection;
  config: DesktopConfig;
  onConfig: (config: DesktopConfig) => void;
  onBack: () => void;
  onContinue: () => void;
}) {
  const wind = config.engine.wind;
  const dispersion = config.engine.dispersion;
  const simulation = config.engine.simulation;
  const voxel = config.engine.voxel;
  const sensor = config.engine.sensor;
  const loggedWindAvailable = inspection.valid_paired_wind_rows >= 2;
  const strictMeasuredAdequate = hasAdequateMeasuredWind("strict_measured", inspection.valid_paired_wind_coverage, wind.minimum_numeric_coverage);

  const mutate = (change: (draft: DesktopConfig) => void) => {
    const next = structuredClone(config);
    change(next);
    onConfig(next);
  };

  const setWindMode = (mode: UiWindMode) => mutate((next) => {
    next.wind_policy.mode = mode;
    next.engine.wind.mode = mode === "synthetic_fallback" ? "synthetic" : "measured";
  });

  return (
    <div className="page-stack">
      <SectionHeading
        eyebrow="Step 3 of 5"
        title="Atmosphere, physics and sensor"
        description="Control the time-varying wind timeline, particle transport, numerical resolution and virtual sensor. Every value has an explicit unit."
      />

      <Card className="wind-mode-card">
        <div className="card-title-row"><div><div className="eyebrow">Atmospheric forcing</div><h3><Wind size={19} /> Wind behavior</h3><p>Atmospheric wind is never inferred from drone speed, velocity, heading or the recorded flight direction.</p></div><div className={`coverage-badge ${loggedWindAvailable ? "good" : "warn"}`}><span>Logged wind pairs</span><strong>{inspection.valid_paired_wind_rows.toLocaleString()} · {(inspection.valid_paired_wind_coverage * 100).toFixed(1)}%</strong></div></div>
        <div className="wind-mode-grid">
          <WindModeCard
            active={config.wind_policy.mode === "measured"}
            icon={<Waves />}
            title="Logged wind timeline"
            description="Use every valid logged wind pair. Vector-interpolate all internal missing intervals and hold the nearest measured wind at the flight boundaries."
            onClick={() => setWindMode("measured")}
            badge={loggedWindAvailable ? `${inspection.valid_paired_wind_rows.toLocaleString()} valid pairs` : "Needs two valid pairs"}
            warning={!loggedWindAvailable}
          />
          <WindModeCard
            active={config.wind_policy.mode === "synthetic_fallback"}
            icon={<Sparkles />}
            title="Synthetic fallback"
            description="Use the explicit synthetic timeline for this run. Raw input wind remains separately preserved for audit."
            onClick={() => setWindMode("synthetic_fallback")}
            badge={!loggedWindAvailable ? "Available fallback" : "Explicit alternative"}
          />
          <WindModeCard
            active={config.wind_policy.mode === "strict_measured"}
            icon={<ShieldAlert />}
            title="Strict measured wind"
            description="Require the paired-coverage threshold and reject bracketed gaps longer than the configured maximum; never substitutes synthetic values."
            onClick={() => setWindMode("strict_measured")}
            badge={strictMeasuredAdequate ? "Available" : "Will reject"}
            warning={!strictMeasuredAdequate}
          />
        </div>
        {config.wind_policy.mode === "strict_measured" && !strictMeasuredAdequate && <div className="alert error inline-alert"><ShieldAlert size={17} /><p>Strict mode requires {(wind.minimum_numeric_coverage * 100).toFixed(0)}% paired coverage, but this flight provides {(inspection.valid_paired_wind_coverage * 100).toFixed(1)}%. Review will reject this configuration unless the explicit threshold is changed.</p></div>}
        <div className="control-grid four separated-top">
          <RangeNumberField label="Fallback wind speed" value={wind.synthetic.base_speed_mps} onChange={(value) => mutate((next) => { next.engine.wind.synthetic.base_speed_mps = value; })} unit="m/s" min={0} max={25} step={0.1} tooltip="Explicit synthetic atmospheric wind speed. This is not derived from UAV motion." />
          <RangeNumberField label="Fallback direction (from)" value={wind.synthetic.base_direction_from_deg} onChange={(value) => mutate((next) => { next.engine.wind.synthetic.base_direction_from_deg = value; })} unit="°" min={0} max={360} step={1} tooltip="Meteorological direction the wind comes from: 0° north, 90° east, 180° south, 270° west." />
          <SelectField label="Wind interpolation" value={config.wind_policy.interpolation} onChange={(value) => mutate((next) => { next.wind_policy.interpolation = value; })} options={[{ value: "linear_vector", label: "Linear vector", description: "Interpolates east/north vectors to avoid 0°/360° direction artifacts." }, { value: "nearest", label: "Nearest valid pair", description: "Holds the nearest valid paired wind sample." }]} tooltip="Method used between paired measured wind samples." />
          <RangeNumberField label="Strict maximum gap" value={wind.maximum_interpolation_gap_s} onChange={(value) => mutate((next) => { next.engine.wind.maximum_interpolation_gap_s = value; })} unit="s" min={1} max={300} step={1} tooltip="Used only by Strict measured wind. Normal logged-wind mode fills all internal gaps." />
          <RangeNumberField label="Strict minimum coverage" value={wind.minimum_numeric_coverage * 100} onChange={(value) => mutate((next) => { next.engine.wind.minimum_numeric_coverage = value / 100; })} unit="%" min={0} max={100} step={1} tooltip="Used only by Strict measured wind. Both atmospheric speed and direction must be valid on the same row to count." />
          <RangeNumberField label="Speed variability" value={wind.synthetic.speed_variability_mps} onChange={(value) => mutate((next) => { next.engine.wind.synthetic.speed_variability_mps = value; })} unit="m/s" min={0} max={10} step={0.1} tooltip="Deterministic synthetic speed variation around the fallback mean." />
          <RangeNumberField label="Direction variability" value={wind.synthetic.direction_variability_deg} onChange={(value) => mutate((next) => { next.engine.wind.synthetic.direction_variability_deg = value; })} unit="°" min={0} max={90} step={1} tooltip="Deterministic synthetic directional variation around the fallback direction." />
          <RangeNumberField label="Synthetic gust period" value={wind.synthetic.gust_period_s} onChange={(value) => mutate((next) => { next.engine.wind.synthetic.gust_period_s = value; })} unit="s" min={5} max={600} step={5} tooltip="Period of the smooth deterministic fallback-wind variation." />
        </div>
      </Card>

      <div className="two-column equal">
        <Card>
          <div className="card-title-row"><div><div className="eyebrow">Parcel transport</div><h3><CloudSun size={19} /> Turbulence and removal</h3><p>Fine and coarse parcels remain separate mass channels.</p></div></div>
          <div className="subsection-label fine"><i /> PM2.5 (fine)</div>
          <div className="control-grid two compact-grid">
            <RangeNumberField label="Fine settling velocity" value={dispersion.fine_settling_velocity_mps} onChange={(value) => mutate((next) => { next.engine.dispersion.fine_settling_velocity_mps = value; })} unit="m/s" min={0} max={0.2} step={0.001} tooltip="Downward gravitational settling speed for fine parcels." />
            <RangeNumberField label="Fine deposition velocity" value={dispersion.fine_deposition_velocity_mps} onChange={(value) => mutate((next) => { next.engine.dispersion.fine_deposition_velocity_mps = value; })} unit="m/s" min={0} max={0.2} step={0.001} tooltip="Simplified dry-deposition removal speed for fine parcels." />
          </div>
          <div className="subsection-label coarse"><i /> Coarse PM (2.5–10 µm)</div>
          <div className="control-grid two compact-grid">
            <RangeNumberField label="Coarse settling velocity" value={dispersion.coarse_settling_velocity_mps} onChange={(value) => mutate((next) => { next.engine.dispersion.coarse_settling_velocity_mps = value; })} unit="m/s" min={0} max={0.5} step={0.005} tooltip="Downward gravitational settling speed for coarse parcels; normally greater than fine settling." />
            <RangeNumberField label="Coarse deposition velocity" value={dispersion.coarse_deposition_velocity_mps} onChange={(value) => mutate((next) => { next.engine.dispersion.coarse_deposition_velocity_mps = value; })} unit="m/s" min={0} max={0.5} step={0.005} tooltip="Simplified dry-deposition removal speed for coarse parcels." />
          </div>
          <div className="subsection-label neutral"><i /> Turbulent dispersion</div>
          <div className="control-grid two compact-grid">
            <RangeNumberField label="Horizontal diffusivity" value={dispersion.horizontal_diffusivity_m2_s} onChange={(value) => mutate((next) => { next.engine.dispersion.horizontal_diffusivity_m2_s = value; })} unit="m²/s" min={0} max={50} step={0.5} tooltip="Random-walk spreading strength in east and north directions." />
            <RangeNumberField label="Vertical diffusivity" value={dispersion.vertical_diffusivity_m2_s} onChange={(value) => mutate((next) => { next.engine.dispersion.vertical_diffusivity_m2_s = value; })} unit="m²/s" min={0} max={20} step={0.25} tooltip="Random-walk spreading strength in the vertical direction." />
          </div>
        </Card>

        <Card>
          <div className="card-title-row"><div><div className="eyebrow">Numerical resolution</div><h3><Database size={19} /> Solver and voxels</h3><p>Higher resolution increases numerical parcel count, memory and runtime.</p></div></div>
          <div className="control-grid two">
            <RangeNumberField label="Simulation time step" value={simulation.timestep_s} onChange={(value) => mutate((next) => { next.engine.simulation.timestep_s = value; })} unit="s" min={0.1} max={10} step={0.1} tooltip="Transport and emission interval. Smaller values resolve wind changes more often." />
            <RangeNumberField label="Parcels per time step" value={simulation.particles_per_timestep} onChange={(value) => mutate((next) => { next.engine.simulation.particles_per_timestep = Math.round(value); })} unit="parcels" min={2} max={200} step={2} tooltip="Computational parcel emission resolution across the two mass classes." />
            <RangeNumberField label="Voxel east–west size" value={voxel.size_x_m} onChange={(value) => mutate((next) => { next.engine.voxel.size_x_m = value; })} unit="m" min={1} max={100} step={1} tooltip="Numerical voxel dimension along local east." />
            <RangeNumberField label="Voxel north–south size" value={voxel.size_y_m} onChange={(value) => mutate((next) => { next.engine.voxel.size_y_m = value; })} unit="m" min={1} max={100} step={1} tooltip="Numerical voxel dimension along local north." />
            <RangeNumberField label="Voxel vertical size" value={voxel.size_z_m} onChange={(value) => mutate((next) => { next.engine.voxel.size_z_m = value; })} unit="m" min={1} max={50} step={1} tooltip="Numerical voxel dimension along local vertical." />
            <RangeNumberField label="Maximum active parcels" value={simulation.maximum_particles} onChange={(value) => mutate((next) => { next.engine.simulation.maximum_particles = Math.round(value); })} unit="parcels" min={1000} max={1000000} step={1000} tooltip="Safety ceiling for active computational parcels. Reaching it is flagged." />
          </div>
          <Toggle label="Voxel smoothing" checked={voxel.smoothing_enabled} onChange={(checked) => mutate((next) => { next.engine.voxel.smoothing_enabled = checked; })} description="Optional neighbor smoothing for the legacy voxel field; exact sensor contributors remain recorded." />
        </Card>
      </div>

      <Card>
        <div className="card-title-row"><div><div className="eyebrow">Virtual instrument</div><h3><RadioTower size={19} /> Sampling region and sensor response</h3><p>A parcel is detected when it contributes mass inside this numerical region—not when a graphic collides with the drone.</p></div><div className="volume-readout"><Box size={16} /><span>Sampling volume</span><strong>{(config.sensor_region.size_x_m * config.sensor_region.size_y_m * config.sensor_region.size_z_m).toLocaleString()} m³</strong></div></div>
        <div className="settings-columns three">
          <div className="settings-group">
            <h4><Box size={16} /> Sampling region</h4>
            <RangeNumberField label="East–west dimension" value={config.sensor_region.size_x_m} onChange={(value) => mutate((next) => { next.sensor_region.size_x_m = value; })} unit="m" min={1} max={100} step={1} tooltip="Full width of the numerical sensor region along local east." />
            <RangeNumberField label="North–south dimension" value={config.sensor_region.size_y_m} onChange={(value) => mutate((next) => { next.sensor_region.size_y_m = value; })} unit="m" min={1} max={100} step={1} tooltip="Full width of the numerical sensor region along local north." />
            <RangeNumberField label="Vertical dimension" value={config.sensor_region.size_z_m} onChange={(value) => mutate((next) => { next.sensor_region.size_z_m = value; })} unit="m" min={1} max={50} step={1} tooltip="Full height of the numerical sensor region around the drone." />
          </div>
          <div className="settings-group">
            <h4><Timer size={16} /> Response and limits</h4>
            <RangeNumberField label="PM2.5 response time" value={sensor.response_time_pm25_s} onChange={(value) => mutate((next) => { next.engine.sensor.response_time_pm25_s = value; })} unit="s" min={0} max={60} step={0.5} tooltip="First-order response lag applied only to reported PM2.5, never to true concentration." />
            <RangeNumberField label="PM10 response time" value={sensor.response_time_pm10_s} onChange={(value) => mutate((next) => { next.engine.sensor.response_time_pm10_s = value; })} unit="s" min={0} max={60} step={0.5} tooltip="First-order response lag applied only to reported PM10." />
            <RangeNumberField label="Lower detection limit" value={sensor.lower_detection_limit_ug_m3} onChange={(value) => mutate((next) => { next.engine.sensor.lower_detection_limit_ug_m3 = value; })} unit="µg/m³" min={0} max={100} step={0.1} tooltip="Reported readings below this limit are clipped or flagged; true concentrations stay separate." />
            <RangeNumberField label="Upper saturation limit" value={sensor.upper_saturation_limit_ug_m3} onChange={(value) => mutate((next) => { next.engine.sensor.upper_saturation_limit_ug_m3 = value; })} unit="µg/m³" min={1} max={5000} step={10} tooltip="Maximum simulated reported sensor reading." />
          </div>
          <div className="settings-group">
            <h4><Gauge size={16} /> Noise and bias</h4>
            <RangeNumberField label="PM2.5 additive noise" value={config.sensor_model.pm25_noise_std_ug_m3} onChange={(value) => mutate((next) => { next.sensor_model.pm25_noise_std_ug_m3 = value; })} unit="µg/m³" min={0} max={25} step={0.1} tooltip="Deterministic seeded Gaussian noise applied to sensor-reported PM2.5 only." />
            <RangeNumberField label="PM10 additive noise" value={config.sensor_model.pm10_noise_std_ug_m3} onChange={(value) => mutate((next) => { next.sensor_model.pm10_noise_std_ug_m3 = value; })} unit="µg/m³" min={0} max={25} step={0.1} tooltip="Deterministic seeded Gaussian noise applied to sensor-reported PM10 only." />
            <RangeNumberField label="PM2.5 bias" value={config.sensor_model.pm25_bias_ug_m3} onChange={(value) => mutate((next) => { next.sensor_model.pm25_bias_ug_m3 = value; next.engine.sensor.bias_pm25_ug_m3 = value; })} unit="µg/m³" min={-50} max={50} step={0.1} tooltip="Constant offset applied only to the simulated PM2.5 reading." />
            <RangeNumberField label="PM10 bias" value={config.sensor_model.pm10_bias_ug_m3} onChange={(value) => mutate((next) => { next.sensor_model.pm10_bias_ug_m3 = value; next.engine.sensor.bias_pm10_ug_m3 = value; })} unit="µg/m³" min={-50} max={50} step={0.1} tooltip="Constant offset applied only to the simulated PM10 reading." />
          </div>
        </div>
      </Card>

      <Card>
        <div className="card-title-row"><div><div className="eyebrow">Playback output</div><h3><Layers3 size={19} /> Trace and rendering resolution</h3><p>The numerical solver always uses every parcel. These controls only set stored playback cadence and GPU display decimation.</p></div></div>
        <div className="control-grid three">
          <RangeNumberField label="Playback frame interval" value={config.playback.frame_interval_s} onChange={(value) => mutate((next) => { next.playback.frame_interval_s = value; })} unit="s" min={0.5} max={10} step={0.5} tooltip="Interval between lossless-for-playback HDF5 particle snapshots; not the solver time step." />
          <RangeNumberField label="Visualization parcel limit" value={config.playback.visualization_parcel_limit} onChange={(value) => mutate((next) => { next.playback.visualization_parcel_limit = Math.round(value); })} unit="parcels" min={500} max={50000} step={500} tooltip="Maximum parcels drawn at once. Concentration and mass accounting still use the full numerical set." />
          <div className="render-distinction"><Layers3 size={18} /><div><strong>Numerical ≠ rendered</strong><span>UI decimation changes appearance only. Sensor concentrations never use the rendered subset.</span></div></div>
        </div>
      </Card>

      <div className="page-actions"><button type="button" className="button ghost large" onClick={onBack}><ArrowLeft size={17} /> Pollution source</button><div className="actions-spacer" /><div className="next-summary"><span>Wind source</span><strong>{windModeLabel(config.wind_policy.mode)}</strong><ChevronRight size={14} /><span>Solver step</span><strong>{simulation.timestep_s} s</strong></div><button type="button" className="button primary large" onClick={onContinue}>Review scenario <ArrowRight size={17} /></button></div>
    </div>
  );
}

function WindModeCard({ active, icon, title, description, onClick, badge, warning = false }: { active: boolean; icon: React.ReactNode; title: string; description: string; onClick: () => void; badge: string; warning?: boolean }) {
  return <button type="button" className={`wind-mode-option ${active ? "active" : ""} ${warning ? "warning" : ""}`} onClick={onClick}><span className="wind-mode-icon">{icon}</span><span className="wind-mode-copy"><span className="option-badge">{badge}</span><strong>{title}</strong><small>{description}</small></span><span className="radio-dot"><i /></span></button>;
}

function windModeLabel(mode: UiWindMode) {
  return ({ measured: "Logged wind timeline", synthetic_fallback: "Synthetic fallback", strict_measured: "Strict measured" })[mode];
}
