import { AlertCircle, ArrowLeft, ArrowRight, Check, CheckCircle2, Circle, CircleDashed, Clock3, CopyCheck, FileArchive, FileDown, FileJson, FileSpreadsheet, Folder, FolderOpen, LoaderCircle, OctagonX, PauseCircle, Play, RotateCcw, Save, ShieldCheck, Square, TimerReset, TriangleAlert, Wind, XCircle } from "lucide-react";
import type { DesktopConfig, Inspection, JobResult, JobStatus, ValidationResponse } from "../types";
import { Card, SectionHeading, TextField } from "./Controls";

const stages = [
  { index: 1, label: "Validating flight file", detail: "Schema, units and quality" },
  { index: 2, label: "Preparing coordinates", detail: "WGS84 to local Cartesian" },
  { index: 3, label: "Preparing wind timeline", detail: "Raw, used and provenance" },
  { index: 4, label: "Transporting parcels", detail: "Emission, turbulence and deposition" },
  { index: 5, label: "Sampling drone sensor", detail: "Exact region contributors" },
  { index: 6, label: "Generating playback", detail: "Synchronized trace frames" },
  { index: 7, label: "Validating outputs", detail: "Invariants and reconstruction" },
  { index: 8, label: "Writing output files", detail: "Atomic two-file publish" },
];

export function ReviewStep({
  inspection,
  config,
  validation,
  validationBusy,
  scenarioName,
  outputFolder,
  job,
  result,
  actionBusy,
  onScenarioName,
  onChooseFolder,
  onSaveConfig,
  onLoadConfig,
  onRun,
  onCancel,
  onReset,
  onBack,
  onPlayback,
}: {
  inspection: Inspection;
  config: DesktopConfig;
  validation: ValidationResponse | null;
  validationBusy: boolean;
  scenarioName: string;
  outputFolder: string;
  job: JobStatus | null;
  result: JobResult | null;
  actionBusy: boolean;
  onScenarioName: (name: string) => void;
  onChooseFolder: () => Promise<void>;
  onSaveConfig: () => Promise<void>;
  onLoadConfig: () => Promise<void>;
  onRun: () => Promise<void>;
  onCancel: () => Promise<void>;
  onReset: () => Promise<void>;
  onBack: () => void;
  onPlayback: () => void;
}) {
  const source = config.engine.source;
  const fine = config.emissions.pm25_emission_g_s;
  const coarse = config.emissions.coarse_pm_emission_g_s;
  const running = job?.state === "queued" || job?.state === "running" || job?.state === "cancelling";
  const completed = job?.state === "completed" && Boolean(result);
  const failed = job?.state === "failed" || job?.state === "cancelled";
  const invalid = validation ? !validation.valid : true;
  const scenarioSafe = sanitizeScenarioName(scenarioName);
  const canRun = !running && !validationBusy && !invalid && Boolean(outputFolder) && Boolean(scenarioSafe);

  return (
    <div className="page-stack">
      <SectionHeading
        eyebrow="Step 4 of 5"
        title="Review and run"
        description="Confirm the scenario, choose where the two primary files will be saved, then run without blocking the interface."
        action={<div className="header-actions"><button type="button" className="button ghost" disabled={running || actionBusy} onClick={() => void onLoadConfig()}><FileDown size={15} /> Load configuration</button><button type="button" className="button ghost" disabled={running || actionBusy} onClick={() => void onSaveConfig()}><Save size={15} /> Save configuration</button></div>}
      />

      <div className="review-layout">
        <div className="review-main">
          <Card>
            <div className="card-title-row"><div><div className="eyebrow">Scenario identity</div><h3>Name and output folder</h3><p>The scenario folder will expose exactly the HDF5 trace and clean sensor-log CSV.</p></div></div>
            <div className="scenario-output-grid">
              <TextField label="Scenario name" value={scenarioName} onChange={(value) => onScenarioName(sanitizeScenarioName(value))} placeholder="representative-flight" tooltip="Used in the two filenames. Spaces and unsupported characters are converted to hyphens." />
              <label className="field folder-field"><span className="field-label"><span>Output folder</span></span><button type="button" className={`folder-picker ${outputFolder ? "selected" : ""}`} disabled={running || actionBusy} onClick={() => void onChooseFolder()}><FolderOpen size={19} /><span><strong>{outputFolder ? folderName(outputFolder) : "Choose a folder…"}</strong><small>{outputFolder || "A native Windows folder picker will open; no path entry needed."}</small></span><ArrowRight size={17} /></button></label>
            </div>
            <div className="output-contract">
              <div className="output-file"><FileArchive size={24} /><span><strong>{scenarioSafe || "scenario"}_simulation_trace.h5</strong><small>Reproducibility · full playback · contributors · mass audit</small></span><span className="format-chip">HDF5</span></div>
              <div className="output-file"><FileSpreadsheet size={24} /><span><strong>{scenarioSafe || "scenario"}_simulated_drone_sensor_log.csv</strong><small>One clean row per valid uploaded flight sample</small></span><span className="format-chip">CSV</span></div>
              <div className="exactly-two"><CopyCheck size={16} /><span><strong>Exactly two primary files</strong> in the final scenario folder; temporary output stays hidden until validation succeeds.</span></div>
            </div>
          </Card>

          <Card>
            <div className="card-title-row"><div><div className="eyebrow">Configuration snapshot</div><h3>Scenario summary</h3></div><span className="seed-chip">Seed {config.engine.project.random_seed}</span></div>
            <div className="summary-grid">
              <SummaryGroup title="Flight" rows={[
                ["File", inspection.filename],
                ["Rows / duration", `${inspection.row_count.toLocaleString()} / ${formatDuration(inspection.duration_s)}`],
                ["UTC", `${shortUtc(inspection.first_utc)} → ${shortUtc(inspection.last_utc)}`],
              ]} />
              <SummaryGroup title="Source" rows={[
                ["Location", `${source.latitude?.toFixed(6)}, ${source.longitude?.toFixed(6)}`],
                ["Release height", `${(source.stack_height_m + source.plume_rise_m).toFixed(1)} m`],
                ["Emission window", `${source.emission_start_s.toFixed(0)}–${(source.emission_end_s ?? inspection.duration_s).toFixed(0)} s`],
              ]} />
              <SummaryGroup title="Particulate mass" rows={[
                ["PM2.5", `${fine.toFixed(3)} g/s`],
                ["Coarse (2.5–10 µm)", `${coarse.toFixed(3)} g/s`],
                ["PM10 total", `${(fine + coarse).toFixed(3)} g/s`],
              ]} />
              <SummaryGroup title="Wind" rows={[
                ["Policy", windLabel(config.wind_policy.mode)],
                [inspection.input_format === "sfd_xlsx" ? "Complete wind coverage" : "Measured coverage", `${(inspection.valid_paired_wind_coverage * 100).toFixed(1)}% paired`],
                ...(inspection.input_format === "sfd_xlsx" ? [["SFD provenance", `${(inspection.sfd_original_wind_rows ?? 0).toLocaleString()} original + ${(inspection.sfd_generated_wind_rows ?? 0).toLocaleString()} generated`]] : []),
                ["Fallback", `${config.engine.wind.synthetic.base_speed_mps.toFixed(1)} m/s from ${config.engine.wind.synthetic.base_direction_from_deg.toFixed(0)}°`],
              ]} />
              <SummaryGroup title="Numerics" rows={[
                ["Solver interval", `${config.engine.simulation.timestep_s} s`],
                ["Emission resolution", `${config.engine.simulation.particles_per_timestep} parcels/step`],
                ["Voxel", `${config.engine.voxel.size_x_m} × ${config.engine.voxel.size_y_m} × ${config.engine.voxel.size_z_m} m`],
              ]} />
              <SummaryGroup title="Sensor / playback" rows={[
                ["Sampling region", `${config.sensor_region.size_x_m} × ${config.sensor_region.size_y_m} × ${config.sensor_region.size_z_m} m`],
                ["Response PM2.5 / PM10", `${config.engine.sensor.response_time_pm25_s} / ${config.engine.sensor.response_time_pm10_s} s`],
                ["Frame / visual limit", `${config.playback.frame_interval_s} s / ${config.playback.visualization_parcel_limit.toLocaleString()}`],
              ]} />
            </div>
            <div className="invariant-row"><ShieldCheck size={18} /><div><strong>Hard identities in this configuration</strong><span>PM10 = PM2.5 + coarse PM · raw wind remains separate from wind used · original flight input remains unchanged · one shared timebase</span></div></div>
          </Card>

          <Card>
            <div className="card-title-row"><div><div className="eyebrow">Preflight checks</div><h3>Validation and estimated size</h3></div>{validationBusy ? <span className="checking-pill"><LoaderCircle className="spin" size={14} /> Checking</span> : validation?.valid ? <span className="verified-pill"><CheckCircle2 size={15} /> Ready</span> : <span className="error-pill"><XCircle size={15} /> Needs attention</span>}</div>
            {validation?.errors.map((error) => <div className="alert error compact-alert" key={error}><OctagonX size={16} /><p>{error}</p></div>)}
            {validation?.warnings.map((warning) => <div className="alert warning compact-alert" key={warning}><TriangleAlert size={16} /><p>{warning}</p></div>)}
            {!outputFolder && <div className="alert neutral compact-alert"><Folder size={16} /><p>Choose an output folder before running.</p></div>}
            <EstimateGrid estimate={validation?.estimate} config={config} inspection={inspection} />
          </Card>
        </div>

        <aside className="run-panel">
          <div className={`run-card ${running ? "running" : ""} ${completed ? "completed" : ""} ${failed ? "failed" : ""}`}>
            <div className="run-card-header"><div><div className="eyebrow">Simulation job</div><h3>{completed ? "Run complete" : job?.state === "failed" ? "Run failed" : job?.state === "cancelled" ? "Run cancelled" : running ? "Simulation in progress" : "Ready to simulate"}</h3></div>{completed ? <CheckCircle2 size={27} /> : running ? <LoaderCircle className="spin" size={27} /> : failed ? <XCircle size={27} /> : <Play size={27} />}</div>
            {job ? (
              <>
                <div className="progress-header"><span>{job.stage || "Preparing"}</span><strong>{Math.round(job.progress * 100)}%</strong></div>
                <div className="progress-track"><span style={{ width: `${Math.max(0, Math.min(100, job.progress * 100))}%` }} /></div>
                <div className="job-message">{job.message || "Waiting for the simulation worker."}</div>
                <div className="elapsed"><Clock3 size={14} /><span>Elapsed</span><strong>{formatElapsed(job.elapsed_s)}</strong></div>
              </>
            ) : <p className="run-intro">The worker will report eight real stages. You can cancel cleanly while the interface remains responsive.</p>}
            <ol className="stage-list">
              {stages.map((stage) => {
                const active = running && job?.stage_index === stage.index;
                const done = completed || (job && job.stage_index > stage.index);
                return <li className={`${active ? "active" : ""} ${done ? "done" : ""}`} key={stage.index}>{done ? <Check size={13} /> : active ? <CircleDashed className="spin" size={13} /> : <Circle size={11} />}<span><strong>{stage.label}</strong><small>{stage.detail}</small></span></li>;
              })}
            </ol>
            {job?.error && <div className="job-error"><AlertCircle size={16} /><span>{job.error}</span></div>}
            <div className="run-buttons">
              {!running && !completed && <button type="button" className="button primary run-button" disabled={!canRun || actionBusy} onClick={() => void onRun()}><Play size={18} /> Run Simulation</button>}
              {running && <button type="button" className="button danger run-button" disabled={job?.state === "cancelling" || actionBusy} onClick={() => void onCancel()}><Square size={16} /> {job?.state === "cancelling" ? "Stopping cleanly…" : "Cancel"}</button>}
              {completed && <button type="button" className="button primary run-button" onClick={onPlayback}>Open 3D playback <ArrowRight size={17} /></button>}
              <button type="button" className="button ghost run-button" disabled={running || actionBusy} onClick={() => void onReset()}><RotateCcw size={16} /> Reset run</button>
            </div>
            {!canRun && !running && !completed && <div className="run-blockers">{invalid && <span>Resolve validation errors</span>}{!outputFolder && <span>Select an output folder</span>}{!scenarioSafe && <span>Name the scenario</span>}</div>}
          </div>

          {completed && result && <div className="result-card"><div className="result-title"><CheckCircle2 size={18} /><div><strong>Two files published</strong><span>{result.output_folder}</span></div></div>{result.output_files.map((file) => <div className="result-file" key={file.path}><span>{file.name}</span><strong>{file.size_bytes ? formatBytes(file.size_bytes) : "Ready"}</strong></div>)}</div>}
          <div className="model-caveat"><PauseCircle size={17} /><p><strong>Research simulator</strong><br />Useful for testing VoxMaps forward workflows. Not CFD-grade or suitable for regulatory decisions.</p></div>
        </aside>
      </div>

      <div className="page-actions"><button type="button" className="button ghost large" disabled={running} onClick={onBack}><ArrowLeft size={17} /> Model settings</button><div className="actions-spacer" />{completed && <button type="button" className="button primary large" onClick={onPlayback}>Inspect playback <ArrowRight size={17} /></button>}</div>
    </div>
  );
}

function SummaryGroup({ title, rows }: { title: string; rows: string[][] }) {
  return <div className="summary-group"><h4>{title}</h4>{rows.map(([label, value]) => <div key={label}><span>{label}</span><strong title={value}>{value}</strong></div>)}</div>;
}

function EstimateGrid({ estimate, config, inspection }: { estimate?: ValidationResponse["estimate"]; config: DesktopConfig; inspection: Inspection }) {
  const steps = Number(estimate?.simulation_steps ?? Math.ceil(inspection.duration_s / config.engine.simulation.timestep_s));
  const parcels = Number(estimate?.emitted_parcels ?? steps * config.engine.simulation.particles_per_timestep);
  const frames = Number(estimate?.playback_frames ?? Math.ceil(inspection.duration_s / config.playback.frame_interval_s) + 1);
  const sensorSamples = Number(estimate?.sensor_samples ?? inspection.usable_row_count ?? inspection.row_count);
  return <div className="estimate-grid"><div><span>Solver steps</span><strong>{steps.toLocaleString()}</strong></div><div><span>Sensor samples</span><strong>{sensorSamples.toLocaleString()}</strong></div><div><span>Parcels emitted (est.)</span><strong>{parcels.toLocaleString()}</strong></div><div><span>Playback frames</span><strong>{frames.toLocaleString()}</strong></div><div><span>Peak memory (est.)</span><strong>{estimate?.estimated_memory_mb ? `≈ ${Number(estimate.estimated_memory_mb).toFixed(0)} MB` : "Calculated at run"}</strong></div><div><span>Runtime (est.)</span><strong>{estimate?.estimated_runtime_s ? `≈ ${formatElapsed(Number(estimate.estimated_runtime_s))}` : "Hardware dependent"}</strong></div></div>;
}

function sanitizeScenarioName(value: string) {
  return value.trimStart().replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/-{2,}/g, "-").slice(0, 80);
}

function folderName(path: string) {
  return path.replace(/[\\/]+$/, "").split(/[\\/]/).at(-1) || path;
}

function formatDuration(seconds: number) {
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}m ${total % 60}s`;
}

function formatElapsed(seconds: number) {
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  return formatDuration(seconds);
}

function shortUtc(value?: string | null) {
  if (!value) return "unknown";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toISOString().replace(".000Z", "Z").slice(11);
}

function windLabel(mode: DesktopConfig["wind_policy"]["mode"]) {
  return mode === "strict_measured" ? "Strict measured" : mode === "synthetic_fallback" ? "Synthetic fallback" : "Logged wind timeline";
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
