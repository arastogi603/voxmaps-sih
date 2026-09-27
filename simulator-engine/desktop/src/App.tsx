import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, LoaderCircle, X } from "lucide-react";
import { api } from "./api";
import { cloneDefaultConfig, defaultConfig } from "./defaultConfig";
import { scenarioFromFilename } from "./configPolicies";
import type { AppStep, DesktopConfig, Inspection, JobResult, JobStatus, PlaybackBundle, UploadResponse, ValidationResponse } from "./types";
import { ModelStep } from "./components/ModelStep";
import { ReviewStep } from "./components/ReviewStep";
import { Shell } from "./components/Shell";
import { SourceStep } from "./components/SourceStep";
import { UploadStep } from "./components/UploadStep";

const PlaybackStep = lazy(() => import("./components/PlaybackStep").then((module) => ({ default: module.PlaybackStep })));

export default function App() {
  const [step, setStep] = useState<AppStep>("upload");
  const [completedSteps, setCompletedSteps] = useState<Set<AppStep>>(new Set());
  const [connected, setConnected] = useState(api.demoMode);
  const [booting, setBooting] = useState(true);
  const [config, setConfig] = useState<DesktopConfig>(cloneDefaultConfig);
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [scenarioName, setScenarioName] = useState("scenario");
  const [outputFolder, setOutputFolder] = useState("");
  const [validation, setValidation] = useState<ValidationResponse | null>(null);
  const [validationBusy, setValidationBusy] = useState(false);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [result, setResult] = useState<JobResult | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [playback, setPlayback] = useState<PlaybackBundle | null>(null);
  const [playbackLoading, setPlaybackLoading] = useState(false);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "success" | "error" | "warning"; text: string } | null>(null);
  const handledJobs = useRef(new Set<string>());

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const [health, defaults] = await Promise.all([
          api.demoMode ? Promise.resolve({ status: "ok" }) : api.health(),
          api.getDefaults(),
        ]);
        if (!active) return;
        setConnected(health.status === "ok" || health.status === "healthy");
        setConfig(mergeConfig(defaultConfig, defaults.config));
      } catch (caught) {
        if (!active) return;
        setConnected(false);
        setNotice({ kind: "error", text: `The local VoxMaps service is not ready: ${messageOf(caught)}` });
      } finally {
        if (active) setBooting(false);
      }
    })();
    return () => { active = false; };
  }, []);

  const markComplete = useCallback((value: AppStep) => setCompletedSteps((current) => new Set(current).add(value)), []);

  const initializeFlightConfig = useCallback((base: DesktopConfig, inspection: Inspection, filename: string, forceLocation = false) => {
    const next = structuredClone(base);
    const latitude = (inspection.gps_extent.min_latitude + inspection.gps_extent.max_latitude) / 2;
    const longitude = (inspection.gps_extent.min_longitude + inspection.gps_extent.max_longitude) / 2;
    if (forceLocation || next.engine.source.latitude == null || next.engine.source.longitude == null) {
      next.engine.source.latitude = Number(latitude.toFixed(7));
      next.engine.source.longitude = Number(longitude.toFixed(7));
    }
    if (forceLocation || next.engine.source.emission_end_s == null) next.engine.source.emission_end_s = Math.max(1, inspection.duration_s);
    const name = scenarioFromFilename(filename);
    next.engine.project.scenario_id = name;
    return { config: next, scenarioName: name };
  }, []);

  const handleUpload = useCallback(async (file: File) => {
    setUploadBusy(true);
    setUploadError(null);
    try {
      const nextUpload = await api.uploadFlight(file);
      const initialized = initializeFlightConfig(config, nextUpload.inspection, file.name, true);
      if (upload) void api.deleteUpload(upload.upload_id).catch(() => undefined);
      setUpload(nextUpload);
      setConfig(initialized.config);
      setScenarioName(initialized.scenarioName);
      setJob(null);
      setResult(null);
      setPlayback(null);
      setValidation(null);
      setCompletedSteps(nextUpload.inspection.requires_mapping ? new Set() : new Set(["upload"]));
      setNotice({ kind: "success", text: `${file.name} inspected without modifying the original file.` });
    } catch (caught) {
      setUploadError(messageOf(caught));
    } finally {
      setUploadBusy(false);
    }
  }, [config, initializeFlightConfig, upload]);

  const handleLoadDemo = useCallback(async () => {
    setUploadBusy(true);
    setUploadError(null);
    try {
      const nextUpload = await api.loadBundledDemoFlight();
      const initialized = initializeFlightConfig(config, nextUpload.inspection, nextUpload.inspection.filename, true);
      if (upload) void api.deleteUpload(upload.upload_id).catch(() => undefined);
      setUpload(nextUpload);
      setConfig(initialized.config);
      setScenarioName(`sih-five-minute-${new Date().toISOString().replace(/\D/g, "").slice(0, 14)}`);
      setOutputFolder(nextUpload.output_folder);
      setJob(null);
      setResult(null);
      setPlayback(null);
      setValidation(null);
      setCompletedSteps(nextUpload.inspection.requires_mapping ? new Set() : new Set(["upload"]));
      setNotice({ kind: "success", text: "Bundled five-minute simulated flight loaded. Configure its source and run the original solver." });
    } catch (caught) {
      setUploadError(messageOf(caught));
    } finally {
      setUploadBusy(false);
    }
  }, [config, initializeFlightConfig, upload]);

  const handleMapping = useCallback(async (mapping: Record<string, string>) => {
    if (!upload) return;
    try {
      const nextUpload = await api.updateMapping(upload.upload_id, mapping, config.engine.wind.minimum_numeric_coverage);
      setUpload(nextUpload);
      const nextConfig = structuredClone(config);
      nextConfig.engine.input.column_mapping = mapping;
      setConfig(nextConfig);
      if (!nextUpload.inspection.requires_mapping) markComplete("upload");
      setNotice({ kind: "success", text: "Column mapping applied and the flight was re-inspected." });
    } catch (caught) {
      setUploadError(messageOf(caught));
      throw caught;
    }
  }, [upload, config, markComplete]);

  useEffect(() => {
    if (step !== "review" || !upload) return;
    let active = true;
    const timer = window.setTimeout(() => {
      setValidationBusy(true);
      void api.validateConfig(upload.upload_id, config)
        .then((response) => { if (active) setValidation(response); })
        .catch((caught) => {
          if (active) setValidation({ valid: false, config, warnings: [], errors: [messageOf(caught)] });
        })
        .finally(() => { if (active) setValidationBusy(false); });
    }, 180);
    return () => { active = false; window.clearTimeout(timer); };
  }, [step, upload?.upload_id, config]);

  const preparePlayback = useCallback(async (nextResult: JobResult) => {
    setPlaybackLoading(true);
    setPlaybackError(null);
    try {
      let playbackId = nextResult.playback_id;
      if (!playbackId) {
        const tracePath = nextResult.trace_path ?? nextResult.output_files.find((file) => file.name.endsWith(".h5"))?.path;
        if (!tracePath) throw new Error("The completed job did not report an HDF5 trace path.");
        const loaded = await api.loadPlayback(tracePath);
        playbackId = loaded.playback_id;
      }
      const bundle = await api.getPlayback(playbackId);
      setPlayback(bundle);
      markComplete("playback");
    } catch (caught) {
      setPlaybackError(messageOf(caught));
    } finally {
      setPlaybackLoading(false);
    }
  }, [markComplete]);

  const finalizeJob = useCallback(async (status: JobStatus) => {
    if (handledJobs.current.has(status.job_id)) return;
    handledJobs.current.add(status.job_id);
    if (status.state === "completed") {
      try {
        const nextResult = status.result ?? await api.getResult(status.job_id);
        setResult(nextResult);
        markComplete("review");
        setNotice({ kind: "success", text: "Simulation complete: exactly two primary scenario files were published." });
        await preparePlayback(nextResult);
      } catch (caught) {
        setNotice({ kind: "error", text: `The solver completed, but its result could not be opened: ${messageOf(caught)}` });
      }
    } else if (status.state === "failed") {
      setNotice({ kind: "error", text: status.error || "The simulation failed; incomplete primary outputs were not published." });
    } else if (status.state === "cancelled") {
      setNotice({ kind: "warning", text: "Simulation cancelled cleanly; incomplete primary outputs were removed." });
    }
  }, [markComplete, preparePlayback]);

  useEffect(() => {
    if (!job || !["queued", "running", "cancelling"].includes(job.state)) return;
    let active = true;
    let timer = 0;
    const poll = async () => {
      try {
        const status = await api.getJob(job.job_id);
        if (!active) return;
        setJob(status);
        if (["queued", "running", "cancelling"].includes(status.state)) {
          timer = window.setTimeout(poll, 450);
        } else {
          await finalizeJob(status);
        }
      } catch (caught) {
        if (!active) return;
        setNotice({ kind: "error", text: `Progress update failed: ${messageOf(caught)}` });
        timer = window.setTimeout(poll, 1500);
      }
    };
    timer = window.setTimeout(poll, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [job?.job_id, job?.state, finalizeJob]);

  const chooseFolder = async () => {
    setActionBusy(true);
    try {
      const selection = await api.chooseFolder();
      if (!selection.cancelled && selection.path) setOutputFolder(selection.path);
    } catch (caught) {
      setNotice({ kind: "error", text: `Folder selection failed: ${messageOf(caught)}` });
    } finally { setActionBusy(false); }
  };

  const saveConfiguration = async () => {
    setActionBusy(true);
    try {
      const saved = await api.saveConfig(config);
      if (!("cancelled" in saved) || !saved.cancelled) setNotice({ kind: "success", text: `Configuration saved${saved.path ? ` to ${saved.path}` : ""}.` });
    } catch (caught) { setNotice({ kind: "error", text: `Could not save configuration: ${messageOf(caught)}` }); }
    finally { setActionBusy(false); }
  };

  const loadConfiguration = async () => {
    setActionBusy(true);
    try {
      const loaded = await api.loadConfig();
      if (loaded.cancelled) return;
      const next = mergeConfig(defaultConfig, loaded.config);
      if (upload && (next.engine.source.latitude == null || next.engine.source.longitude == null)) {
        const initialized = initializeFlightConfig(next, upload.inspection, upload.inspection.filename);
        setConfig(initialized.config);
      } else setConfig(next);
      setNotice({ kind: "success", text: `Configuration loaded${loaded.path ? ` from ${loaded.path}` : ""}.` });
    } catch (caught) { setNotice({ kind: "error", text: `Could not load configuration: ${messageOf(caught)}` }); }
    finally { setActionBusy(false); }
  };

  const runSimulation = async () => {
    if (!upload) return;
    setActionBusy(true);
    setResult(null);
    setPlayback(null);
    setPlaybackError(null);
    handledJobs.current.clear();
    try {
      const status = await api.createJob({ upload_id: upload.upload_id, scenario_name: scenarioName, output_folder: outputFolder, config });
      setJob(status);
    } catch (caught) {
      setNotice({ kind: "error", text: `Could not start the simulation: ${messageOf(caught)}` });
    } finally { setActionBusy(false); }
  };

  const cancelSimulation = async () => {
    if (!job) return;
    setActionBusy(true);
    try {
      await api.cancelJob(job.job_id);
      setJob((current) => current ? { ...current, state: "cancelling", message: "Cancellation requested; stopping at a safe solver checkpoint." } : current);
    } catch (caught) { setNotice({ kind: "error", text: `Could not request cancellation: ${messageOf(caught)}` }); }
    finally { setActionBusy(false); }
  };

  const resetRun = async () => {
    if (job && ["queued", "running", "cancelling"].includes(job.state)) return;
    setActionBusy(true);
    try {
      if (job) await api.resetJob(job.job_id);
      setJob(null);
      setResult(null);
      setPlayback(null);
      setPlaybackError(null);
      setCompletedSteps((current) => { const next = new Set(current); next.delete("review"); next.delete("playback"); return next; });
    } catch (caught) { setNotice({ kind: "error", text: `Could not reset the run: ${messageOf(caught)}` }); }
    finally { setActionBusy(false); }
  };

  const loadTrace = async () => {
    setPlaybackLoading(true);
    setPlaybackError(null);
    try {
      const loaded = await api.loadPlayback("");
      if (loaded.cancelled || !loaded.playback_id) return;
      const bundle = await api.getPlayback(loaded.playback_id);
      setPlayback(bundle);
      markComplete("playback");
      setNotice({ kind: "success", text: "HDF5 playback loaded without rerunning the solver." });
    } catch (caught) { setPlaybackError(messageOf(caught)); }
    finally { setPlaybackLoading(false); }
  };

  const openBundledDemoTrace = useCallback(async () => {
    setPlaybackLoading(true);
    setPlaybackError(null);
    try {
      const loaded = await api.loadBundledDemoTrace();
      const bundle = await api.getPlayback(loaded.playback_id);
      setJob(null);
      setResult(null);
      setPlayback(bundle);
      setStep("playback");
      markComplete("playback");
      setNotice({ kind: "success", text: "Supplied H5 trace opened for replay; this did not rerun the solver." });
    } catch (caught) {
      setUploadError(messageOf(caught));
    } finally { setPlaybackLoading(false); }
  }, [markComplete]);

  const content = useMemo(() => {
    if (step === "upload") return <UploadStep inspection={upload?.inspection ?? null} busy={uploadBusy} error={uploadError} onUpload={handleUpload} onLoadDemo={handleLoadDemo} onOpenDemoTrace={openBundledDemoTrace} onMapColumns={handleMapping} onContinue={() => { if (upload) { const initialized = initializeFlightConfig(config, upload.inspection, upload.inspection.filename); setConfig(initialized.config); markComplete("upload"); setStep("source"); } }} />;
    if (step === "source" && upload) return <SourceStep inspection={upload.inspection} config={config} onConfig={setConfig} onBack={() => setStep("upload")} onContinue={() => { markComplete("source"); setStep("model"); }} />;
    if (step === "model" && upload) return <ModelStep inspection={upload.inspection} config={config} onConfig={setConfig} onBack={() => setStep("source")} onContinue={() => { markComplete("model"); setStep("review"); }} />;
    if (step === "review" && upload) return <ReviewStep inspection={upload.inspection} config={config} validation={validation} validationBusy={validationBusy} scenarioName={scenarioName} outputFolder={outputFolder} job={job} result={result} actionBusy={actionBusy} onScenarioName={setScenarioName} onChooseFolder={chooseFolder} onSaveConfig={saveConfiguration} onLoadConfig={loadConfiguration} onRun={runSimulation} onCancel={cancelSimulation} onReset={resetRun} onBack={() => setStep("model")} onPlayback={() => setStep("playback")} />;
    if (step === "playback") return <Suspense fallback={<div className="playback-module-loader"><LoaderCircle className="spin" /><span>Loading the 3D playback workspace…</span></div>}><PlaybackStep bundle={playback} result={result} loading={playbackLoading} error={playbackError} visualizationLimit={config.playback.visualization_parcel_limit} onLoadTrace={loadTrace} onBack={() => setStep(upload ? "review" : "upload")} /></Suspense>;
    return null;
  }, [step, upload, uploadBusy, uploadError, handleUpload, handleLoadDemo, openBundledDemoTrace, handleMapping, initializeFlightConfig, config, markComplete, validation, validationBusy, scenarioName, outputFolder, job, result, actionBusy, playback, playbackLoading, playbackError]);

  if (booting) return <div className="boot-screen"><div className="brand-mark"><LoaderCircle className="spin" /></div><h1>VoxMaps Pollution Simulator</h1><p>Starting the local workspace…</p></div>;

  return (
    <>
      <Shell step={step} onStep={setStep} completedSteps={completedSteps} connected={connected} demoMode={api.demoMode} fileName={upload?.inspection.filename} jobState={job?.state}>
        {content}
      </Shell>
      {notice && <div className={`toast ${notice.kind}`} role="status">{notice.kind === "success" ? <CheckCircle2 /> : <AlertTriangle />}<span>{notice.text}</span><button type="button" aria-label="Dismiss message" onClick={() => setNotice(null)}><X /></button></div>}
    </>
  );
}

function messageOf(value: unknown) {
  return value instanceof Error ? value.message : String(value);
}

function mergeConfig(base: DesktopConfig, provided: Partial<DesktopConfig>): DesktopConfig {
  const merged = deepMerge(structuredClone(base), provided) as DesktopConfig;
  if (!provided.emissions) {
    merged.emissions.pm25_emission_g_s = merged.engine.source.pm25_emission_g_s;
    merged.emissions.coarse_pm_emission_g_s = Math.max(0, merged.engine.source.pm10_total_emission_g_s - merged.engine.source.pm25_emission_g_s);
  }
  merged.engine.source.pm25_emission_g_s = merged.emissions.pm25_emission_g_s;
  merged.engine.source.pm10_total_emission_g_s = merged.emissions.pm25_emission_g_s + merged.emissions.coarse_pm_emission_g_s;
  if (!provided.background) {
    merged.background.pm25_ug_m3 = merged.engine.background.pm25_ug_m3;
    merged.background.pm10_ug_m3 = merged.engine.background.pm25_ug_m3 + merged.engine.background.coarse_pm_ug_m3;
  }
  merged.engine.background.pm25_ug_m3 = merged.background.pm25_ug_m3;
  merged.engine.background.coarse_pm_ug_m3 = Math.max(0, merged.background.pm10_ug_m3 - merged.background.pm25_ug_m3);
  merged.engine.sensor.bias_pm25_ug_m3 = merged.sensor_model.pm25_bias_ug_m3;
  merged.engine.sensor.bias_pm10_ug_m3 = merged.sensor_model.pm10_bias_ug_m3;
  return merged;
}

function deepMerge(target: unknown, source: unknown): unknown {
  if (!source || typeof source !== "object" || Array.isArray(source)) return source ?? target;
  const result = { ...(target as Record<string, unknown>) };
  for (const [key, value] of Object.entries(source as Record<string, unknown>)) {
    result[key] = value && typeof value === "object" && !Array.isArray(value)
      ? deepMerge(result[key] ?? {}, value)
      : value;
  }
  return result;
}
