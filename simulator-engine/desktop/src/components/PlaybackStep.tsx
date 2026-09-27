import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { AlertTriangle, ArrowLeft, Box, Camera, CheckCircle2, ChevronDown, CircleGauge, Cloud, Eye, EyeOff, Factory, FastForward, FileArchive, FolderOpen, Gauge, Grid3X3, Layers3, LoaderCircle, LocateFixed, Pause, Play, RefreshCcw, Repeat2, RotateCcw, Route, SkipBack, SkipForward, StepBack, StepForward, Wind, ZoomOut } from "lucide-react";
import { api } from "../api";
import type { JobResult, PlaybackBundle, PlaybackFrame, PlaybackSample } from "../types";
import { Card, EmptyState, SectionHeading, Toggle } from "./Controls";
import { SynchronizedPlots } from "./MetricPlots";
import { PlaybackScene, type LayerState } from "./PlaybackScene";
import { MAX_RENDER_DENSITY, MIN_RENDER_DENSITY, RENDER_DENSITY_STEP, normalizeRenderDensity, renderDensityForKey } from "../playbackPolicies";

const CHUNK_SIZE = 24;

const initialLayers: LayerState = {
  ground: true,
  stack: true,
  flightPath: true,
  fine: true,
  coarse: true,
  deposited: true,
  wind: true,
  sensor: true,
  voxelGrid: false,
};

export function PlaybackStep({
  bundle,
  result,
  loading,
  error,
  visualizationLimit,
  onLoadTrace,
  onBack,
}: {
  bundle: PlaybackBundle | null;
  result: JobResult | null;
  loading: boolean;
  error: string | null;
  visualizationLimit: number;
  onLoadTrace: () => Promise<void>;
  onBack: () => void;
}) {
  if (!bundle) {
    return (
      <div className="page-stack">
        <SectionHeading eyebrow="Step 5 of 5" title="Interactive 3D playback" description="Load a completed simulation trace to replay it without rerunning the solver." />
        <Card>
          <EmptyState
            icon={loading ? <LoaderCircle className="spin" /> : <FileArchive />}
            title={loading ? "Loading simulation trace…" : "No playback loaded"}
            description={error ?? "Choose a VoxMaps _simulation_trace.h5 file. A native Windows file picker will open—no path entry is needed."}
            action={<button type="button" className="button primary large" disabled={loading} onClick={() => void onLoadTrace()}>{loading ? <><LoaderCircle className="spin" size={17} /> Reading HDF5</> : <><FolderOpen size={17} /> Load simulation trace</>}</button>}
          />
        </Card>
        <div className="page-actions"><button type="button" className="button ghost large" onClick={onBack}><ArrowLeft size={17} /> Review & run</button></div>
      </div>
    );
  }
  return <PlaybackWorkspace bundle={bundle} result={result} visualizationLimit={visualizationLimit} onLoadTrace={onLoadTrace} onBack={onBack} />;
}

function PlaybackWorkspace({ bundle, result, visualizationLimit, onLoadTrace, onBack }: { bundle: PlaybackBundle; result: JobResult | null; visualizationLimit: number; onLoadTrace: () => Promise<void>; onBack: () => void }) {
  const metadata = bundle.metadata;
  const totalFrames = Math.max(metadata.frame_count, bundle.frames.length, 1);
  const [cache, setCache] = useState<Map<number, PlaybackFrame>>(() => new Map(bundle.frames.map((frame) => [frame.index, frame])));
  const cacheRef = useRef(cache);
  const pendingChunks = useRef(new Set<number>());
  const [currentIndex, setCurrentIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [density, setDensity] = useState(100);
  const [layers, setLayers] = useState<LayerState>(initialLayers);
  const [followDrone, setFollowDrone] = useState(false);
  const [resetToken, setResetToken] = useState(0);
  const [renderCount, setRenderCount] = useState(0);
  const [loadingFrame, setLoadingFrame] = useState<number | null>(null);
  const [frameError, setFrameError] = useState<string | null>(null);
  const playbackId = metadata.playback_id;
  const cappedVisualLimit = Math.max(500, Math.min(12000, visualizationLimit || 5000));

  useEffect(() => {
    const next = new Map(bundle.frames.map((frame) => [frame.index, frame]));
    setCache(next);
    cacheRef.current = next;
    pendingChunks.current.clear();
    setCurrentIndex(0);
    setPlaying(false);
    setFrameError(null);
  }, [playbackId, bundle.frames]);

  useEffect(() => { cacheRef.current = cache; }, [cache]);

  const fetchChunk = useCallback(async (index: number) => {
    const chunkStart = Math.floor(Math.max(0, index) / CHUNK_SIZE) * CHUNK_SIZE;
    if (pendingChunks.current.has(chunkStart)) return;
    if (cacheRef.current.has(index)) return;
    pendingChunks.current.add(chunkStart);
    setLoadingFrame(index);
    setFrameError(null);
    try {
      const frames = await api.getPlaybackFrames(playbackId, chunkStart, Math.min(CHUNK_SIZE, totalFrames - chunkStart), cappedVisualLimit);
      setCache((current) => {
        const next = new Map(current);
        frames.forEach((frame) => next.set(frame.index, frame));
        return next;
      });
    } catch (caught) {
      setFrameError(caught instanceof Error ? caught.message : "Could not load the selected playback frame.");
      setPlaying(false);
    } finally {
      pendingChunks.current.delete(chunkStart);
      setLoadingFrame((current) => current === index ? null : current);
    }
  }, [playbackId, totalFrames, cappedVisualLimit]);

  useEffect(() => {
    if (!cache.has(currentIndex)) void fetchChunk(currentIndex);
    if (currentIndex % CHUNK_SIZE >= CHUNK_SIZE - 5 && currentIndex + 1 < totalFrames) void fetchChunk(currentIndex + 5);
  }, [cache, currentIndex, fetchChunk, totalFrames]);

  useEffect(() => {
    if (!playing) return;
    let animationId = 0;
    let last = performance.now();
    let accumulated = 0;
    const intervalMs = Math.max(30, (metadata.frame_interval_s ?? inferInterval(bundle.frames)) * 1000);
    const tick = (now: number) => {
      accumulated += (now - last) * speed;
      last = now;
      if (accumulated >= intervalMs) {
        const advance = Math.max(1, Math.floor(accumulated / intervalMs));
        accumulated %= intervalMs;
        setCurrentIndex((current) => {
          const next = Math.min(totalFrames - 1, current + advance);
          if (next >= totalFrames - 1) setPlaying(false);
          return next;
        });
      }
      animationId = requestAnimationFrame(tick);
    };
    animationId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animationId);
  }, [playing, speed, totalFrames, metadata.frame_interval_s, bundle.frames]);

  const currentFrame = cache.get(currentIndex);
  const displayFrame = currentFrame ?? nearestCachedFrame(cache, currentIndex) ?? bundle.frames[0];
  const targetTime = currentFrame?.elapsed_time_s ?? currentIndex * (metadata.frame_interval_s ?? inferInterval(bundle.frames));
  const currentSample = useMemo(() => nearestSample(bundle.samples, targetTime), [bundle.samples, targetTime]);
  const numericalCount = currentFrame?.numerical_particle_count ?? metadata.numerical_particle_count ?? currentFrame?.particles.length ?? 0;
  const isColdLoading = !currentFrame && loadingFrame != null;

  const setLayer = (key: keyof LayerState, value: boolean) => setLayers((current) => ({ ...current, [key]: value }));
  const restart = () => { setCurrentIndex(0); setPlaying(false); };
  const goOverview = () => { setFollowDrone(false); setResetToken((value) => value + 1); };
  const updateDensityFromInput = (value: number) => setDensity(normalizeRenderDensity(value));
  const handleDensityKey = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    const next = renderDensityForKey(density, event.key);
    if (next == null) return;
    event.preventDefault();
    setDensity(next);
  };

  return (
    <div className="page-stack playback-page">
      <SectionHeading
        eyebrow="Step 5 of 5"
        title="Interactive 3D playback"
        description="Drone, time-varying plume, atmospheric wind, sensor region and plots share the same trace timebase. Scrubbing never reruns the solver."
        action={<div className="header-actions"><button type="button" className="button ghost" onClick={() => void onLoadTrace()}><FileArchive size={15} /> Load another trace</button><button type="button" className="button ghost" onClick={onBack}><ArrowLeft size={15} /> Run summary</button></div>}
      />

      <div className="playback-statusbar">
        <div><span>Simulation</span><strong>{metadata.simulation_id}</strong></div>
        <div><span>Trace frames</span><strong>{totalFrames.toLocaleString()}</strong></div>
        <div><span>Sensor samples</span><strong>{metadata.sample_count.toLocaleString()}</strong></div>
        <div><span>Duration</span><strong>{formatClock(metadata.duration_s)}</strong></div>
        <div><span>Stored cadence</span><strong>{(metadata.frame_interval_s ?? inferInterval(bundle.frames)).toFixed(1)} s</strong></div>
        <div className="trace-ready"><CheckCircle2 size={15} /><span>Loaded from HDF5 · solver not rerun</span></div>
      </div>

      <div className="viewer-shell">
        <div className={`scene-wrap ${isColdLoading ? "cold-loading" : ""}`}>
          {displayFrame ? <PlaybackScene frame={displayFrame} metadata={metadata} layers={layers} density={density} renderLimit={cappedVisualLimit} followDrone={followDrone} resetToken={resetToken} onRenderCount={setRenderCount} /> : <div className="scene-placeholder"><LoaderCircle className="spin" /><span>Loading first playback frame…</span></div>}
          <div className="scene-top-overlay">
            <div className="time-hud"><span>UTC time</span><strong>{formatUtc(displayFrame?.utc_time ?? currentSample?.utc_time)}</strong><small>{formatClock(targetTime)} elapsed · frame {currentIndex + 1}/{totalFrames}</small></div>
            <div className="measurement-hud"><span>Virtual sensor</span><div><strong className="fine-color">{formatConcentration(currentSample?.pm25_sensor_ug_m3)} </strong><small>PM2.5 µg/m³</small></div><div><strong className="coarse-color">{formatConcentration(currentSample?.pm10_sensor_ug_m3)} </strong><small>PM10 µg/m³</small></div></div>
          </div>
          <div className="scene-bottom-overlay">
            <div className="wind-hud"><Wind size={16} /><span><small>Wind used</small><strong>{(displayFrame?.wind_speed_mps ?? currentSample?.wind_speed_used_mps ?? 0).toFixed(1)} m/s from {(displayFrame?.wind_direction_deg ?? currentSample?.wind_direction_used_deg ?? 0).toFixed(0)}°</strong><em>{displayFrame?.wind_source ?? "trace"}</em></span></div>
            <div className="parcel-hud"><Layers3 size={16} /><span><small>Computational / rendered</small><strong>{Number(numericalCount).toLocaleString()} / {renderCount.toLocaleString()}</strong><em>sensor uses full numerical set</em></span></div>
          </div>
          {isColdLoading && <div className="frame-loader"><LoaderCircle className="spin" size={21} /><span>Loading trace frame {currentIndex + 1}…</span><small>Cold scrub: fetching a bounded {CHUNK_SIZE}-frame window</small></div>}
          {frameError && <div className="frame-error"><AlertTriangle size={17} /><span>{frameError}</span></div>}
          <div className="scene-legend">
            <span><i className="fine-dot" /> PM2.5 parcel</span><span><i className="coarse-dot" /> Coarse PM parcel</span><span><i className="deposited-dot" /> Deposited</span><span><i className="drone-dot" /> UAV</span>
            <div className="concentration-scale"><small>lower concentration</small><i /><small>higher</small></div>
          </div>
        </div>

        <aside className="viewer-tools">
          <ToolGroup icon={<Camera />} title="Camera">
            <button type="button" className={followDrone ? "active" : ""} onClick={() => setFollowDrone(!followDrone)}><LocateFixed size={15} /> Follow drone</button>
            <button type="button" className={!followDrone ? "active" : ""} onClick={goOverview}><ZoomOut size={15} /> Overview</button>
            <button type="button" onClick={() => setResetToken((value) => value + 1)}><RotateCcw size={15} /> Reset camera</button>
          </ToolGroup>
          <ToolGroup icon={<Eye />} title="Layers">
            <LayerButton icon={<Factory />} label="Stack" value={layers.stack} onChange={(value) => setLayer("stack", value)} />
            <LayerButton icon={<Route />} label="Flight path" value={layers.flightPath} onChange={(value) => setLayer("flightPath", value)} />
            <LayerButton icon={<Cloud />} label="PM2.5 parcels" value={layers.fine} color="fine" onChange={(value) => setLayer("fine", value)} />
            <LayerButton icon={<Cloud />} label="Coarse parcels" value={layers.coarse} color="coarse" onChange={(value) => setLayer("coarse", value)} />
            <LayerButton icon={<CircleGauge />} label="Deposited" value={layers.deposited} color="deposited" onChange={(value) => setLayer("deposited", value)} />
            <LayerButton icon={<Wind />} label="Wind indicator" value={layers.wind} onChange={(value) => setLayer("wind", value)} />
            <LayerButton icon={<Box />} label="Sensor region" value={layers.sensor} onChange={(value) => setLayer("sensor", value)} />
            <LayerButton icon={<Grid3X3 />} label="Voxel grid" value={layers.voxelGrid} onChange={(value) => setLayer("voxelGrid", value)} />
          </ToolGroup>
          <div className="tool-group density-control">
            <label className="tool-title" htmlFor="render-density-slider"><Gauge size={15} /><span>Render density</span><strong>{density}%</strong></label>
            <div className="density-input-row">
              <button type="button" aria-label="Decrease render density" onClick={() => setDensity((value) => normalizeRenderDensity(value - RENDER_DENSITY_STEP))}>−</button>
              <input
                id="render-density-slider"
                type="range"
                min={MIN_RENDER_DENSITY}
                max={MAX_RENDER_DENSITY}
                step={RENDER_DENSITY_STEP}
                value={density}
                aria-label="Render density percentage"
                aria-valuetext={`${density}% of the configured visual parcel limit`}
                onInput={(event) => updateDensityFromInput(event.currentTarget.valueAsNumber)}
                onChange={(event) => updateDensityFromInput(event.currentTarget.valueAsNumber)}
                onKeyDown={handleDensityKey}
              />
              <button type="button" aria-label="Increase render density" onClick={() => setDensity((value) => normalizeRenderDensity(value + RENDER_DENSITY_STEP))}>+</button>
            </div>
            <small>Visual decimation only. Numerical sensor and mass values do not change.</small>
          </div>
        </aside>
      </div>

      <div className="transport-bar">
        <div className="transport-buttons">
          <button type="button" title="Restart" aria-label="Restart playback" onClick={restart}><SkipBack size={18} /></button>
          <button type="button" title="Step backward" aria-label="Step one frame backward" onClick={() => { setPlaying(false); setCurrentIndex((value) => Math.max(0, value - 1)); }}><StepBack size={18} /></button>
          <button type="button" className="play-button" title={playing ? "Pause" : "Play"} aria-label={playing ? "Pause playback" : "Play playback"} onClick={() => setPlaying(!playing)}>{playing ? <Pause size={20} /> : <Play size={20} />}</button>
          <button type="button" title="Step forward" aria-label="Step one frame forward" onClick={() => { setPlaying(false); setCurrentIndex((value) => Math.min(totalFrames - 1, value + 1)); }}><StepForward size={18} /></button>
          <button type="button" title="Go to end" aria-label="Go to final frame" onClick={() => { setPlaying(false); setCurrentIndex(totalFrames - 1); }}><SkipForward size={18} /></button>
        </div>
        <div className="timeline-control"><div><span>{formatClock(targetTime)}</span><strong>{formatUtc(displayFrame?.utc_time ?? currentSample?.utc_time)}</strong><span>{formatClock(metadata.duration_s)}</span></div><input type="range" min={0} max={totalFrames - 1} step={1} value={currentIndex} aria-label="Playback timeline" onChange={(event) => { setPlaying(false); setCurrentIndex(event.target.valueAsNumber); }} onInput={(event) => { setPlaying(false); setCurrentIndex(event.currentTarget.valueAsNumber); }} onClick={(event) => { const bounds = event.currentTarget.getBoundingClientRect(); const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / Math.max(1, bounds.width))); setPlaying(false); setCurrentIndex(Math.round(ratio * (totalFrames - 1))); }} /></div>
        <label className="speed-control"><FastForward size={15} /><select value={speed} aria-label="Playback speed" onChange={(event) => setSpeed(Number(event.target.value))}>{[0.25, 0.5, 1, 2, 4, 8].map((value) => <option value={value} key={value}>{value}×</option>)}</select></label>
      </div>

      <div className="playback-data-layout">
        <Card>
          <div className="card-title-row"><div><div className="eyebrow">One shared timebase</div><h3>Synchronized measurements</h3><p>The amber cursor follows the exact 3D playback time through every plot.</p></div><div className="cursor-readout"><span>Cursor</span><strong>{formatClock(targetTime)}</strong></div></div>
          <SynchronizedPlots samples={bundle.samples} currentTime={targetTime} />
        </Card>
        <Card className="sample-inspector">
          <div className="card-title-row"><div><div className="eyebrow">Sensor audit</div><h3>Selected sample</h3></div><span className="sample-id">#{currentSample?.sample_id ?? "—"}</span></div>
          <div className="measurement-pair"><div className="fine"><span>True PM2.5</span><strong>{formatConcentration(currentSample?.pm25_true_ug_m3)}</strong><small>µg/m³</small></div><div className="fine sensor"><span>Sensor PM2.5</span><strong>{formatConcentration(currentSample?.pm25_sensor_ug_m3)}</strong><small>µg/m³</small></div></div>
          <div className="measurement-pair"><div className="coarse"><span>True PM10</span><strong>{formatConcentration(currentSample?.pm10_true_ug_m3)}</strong><small>µg/m³</small></div><div className="coarse sensor"><span>Sensor PM10</span><strong>{formatConcentration(currentSample?.pm10_sensor_ug_m3)}</strong><small>µg/m³</small></div></div>
          <dl className="sample-details">
            <div><dt>Fine contributors</dt><dd>{currentSample?.fine_contributing_parcels?.toLocaleString() ?? "—"}</dd></div>
            <div><dt>Coarse contributors</dt><dd>{currentSample?.coarse_contributing_parcels?.toLocaleString() ?? "—"}</dd></div>
            <div><dt>Sampling region</dt><dd>{currentSample?.sampling_region_id ?? "trace region"}</dd></div>
            <div><dt>Voxel ID</dt><dd>{currentSample?.voxel_id ?? "—"}</dd></div>
            <div><dt>Local position</dt><dd>{currentSample ? `${currentSample.x_east_m.toFixed(1)}, ${currentSample.y_north_m.toFixed(1)}, ${currentSample.z_up_m.toFixed(1)} m` : "—"}</dd></div>
            <div><dt>Quality flags</dt><dd>{currentSample?.quality_flags || "none"}</dd></div>
          </dl>
          <div className="sensor-definition"><Box size={16} /><p><strong>Detected = contributed inside the numerical volume.</strong> A graphical collision with the drone is neither required nor used.</p></div>
        </Card>
      </div>

      {metadata.mass_balance && <Card><div className="card-title-row"><div><div className="eyebrow">Audit ledger</div><h3>Mass-balance summary</h3></div></div><div className="mass-grid">{Object.entries(metadata.mass_balance).map(([key, value]) => <div key={key}><span>{humanize(key)}</span><strong>{Number(value).toPrecision(5)} g</strong></div>)}</div></Card>}

      <div className="trace-footer"><FileArchive size={16} /><span><strong>Playback source:</strong> {result?.trace_path ?? result?.output_files.find((file) => file.name.endsWith(".h5"))?.path ?? `${metadata.simulation_id} HDF5 trace`}</span><span>Frames are loaded in bounded {CHUNK_SIZE}-frame windows; the solver is never rerun.</span></div>
    </div>
  );
}

function ToolGroup({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return <div className="tool-group"><div className="tool-title">{icon}<span>{title}</span><ChevronDown size={13} /></div><div className="tool-buttons">{children}</div></div>;
}

function LayerButton({ icon, label, value, onChange, color }: { icon: React.ReactNode; label: string; value: boolean; onChange: (value: boolean) => void; color?: string }) {
  return <button type="button" className={`${value ? "active" : ""} ${color ?? ""}`} onClick={() => onChange(!value)}>{icon}<span>{label}</span>{value ? <Eye size={13} /> : <EyeOff size={13} />}</button>;
}

function nearestCachedFrame(cache: Map<number, PlaybackFrame>, index: number) {
  let result: PlaybackFrame | undefined;
  let best = Number.POSITIVE_INFINITY;
  cache.forEach((frame, key) => { const distance = Math.abs(key - index); if (distance < best) { best = distance; result = frame; } });
  return result;
}

function nearestSample(samples: PlaybackSample[], time: number) {
  if (!samples.length) return undefined;
  let low = 0;
  let high = samples.length - 1;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (samples[mid].elapsed_time_s < time) low = mid + 1;
    else high = mid;
  }
  if (low > 0 && Math.abs(samples[low - 1].elapsed_time_s - time) < Math.abs(samples[low].elapsed_time_s - time)) return samples[low - 1];
  return samples[low];
}

function inferInterval(frames: PlaybackFrame[]) {
  return frames.length > 1 ? Math.max(0.1, frames[1].elapsed_time_s - frames[0].elapsed_time_s) : 1;
}

function formatClock(seconds: number) {
  const safe = Math.max(0, seconds || 0);
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const remainder = Math.floor(safe % 60);
  return hours ? `${hours}:${minutes.toString().padStart(2, "0")}:${remainder.toString().padStart(2, "0")}` : `${minutes}:${remainder.toString().padStart(2, "0")}`;
}

function formatUtc(value?: string | null) {
  if (!value) return "UTC unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().replace("T", " ").replace(".000Z", "Z");
}

function formatConcentration(value?: number) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(value >= 100 ? 1 : 2);
}

function humanize(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
