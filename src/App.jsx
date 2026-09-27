import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, ArrowDownToLine, Box, ChevronDown, ChevronLeft, ChevronRight, CircleHelp,
  CloudFog, Crosshair, FileDown, Flame, Layers3, MapPin, Menu, Navigation, Pause,
  Play, RotateCcw, Search, Settings2, Sparkles, Thermometer, Upload, Wind, X,
} from 'lucide-react';
import MapView from './MapView.jsx';
import NativeSimulatorWorkspace from './NativeSimulatorWorkspace.jsx';
import {
  DEMO_START_UTC, POLLUTANTS, SCENARIOS, SOURCES, aqiCategory, forecastTime,
  makeGrid, makeTimeline, scenarioAt,
} from './model.js';
import { loadBundledReplay, parseReplayCsv } from './replay.js';
import { exportCsv, exportGeoJson } from './export.js';
import { scaleFor } from './rendering.js';

const TABS = ['Forecast', 'Physics', 'Sources', 'Data'];
const METRIC_OPTIONS = ['aqi', 'pm25', 'pm10', 'o3', 'nox', 'inversion'];
const blankCorners = () => Array.from({ length: 4 }, () => ({ lat: '', lng: '' }));

function formatMetric(value, unit) {
  return `${Math.round(value).toLocaleString('en-IN')} ${unit}`;
}

function formatDuration(seconds) {
  const rounded = Math.round(seconds);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, '0')}`;
}

function areaStats(points) {
  if (points.length !== 4) return null;
  const centerLat = points.reduce((sum, point) => sum + point[1], 0) / points.length;
  const metersPerLng = 111_320 * Math.cos(centerLat * Math.PI / 180);
  const projected = points.map(([lng, lat]) => [lng * metersPerLng, lat * 111_320]);
  let twiceArea = 0;
  let perimeter = 0;
  projected.forEach(([x1, y1], index) => {
    const [x2, y2] = projected[(index + 1) % projected.length];
    twiceArea += x1 * y2 - x2 * y1;
    perimeter += Math.hypot(x2 - x1, y2 - y1);
  });
  return { area: Math.abs(twiceArea) / 2, perimeter };
}

function MiniChart({ points, currentHour, metric = 'aqi' }) {
  const values = points.map((item) => item[metric]);
  const min = Math.min(...values) * 0.84;
  const max = Math.max(...values) * 1.08;
  const coordinates = values.map((value, index) => [8 + index * (344 / 72), 80 - ((value - min) / Math.max(max - min, 1)) * 62]);
  const path = coordinates.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const current = coordinates[currentHour];
  return (
    <svg className="mini-chart" viewBox="0 0 360 94" role="img" aria-label={`72 hour ${metric} trend`}>
      {[25, 50, 75].map((y) => <line key={y} x1="8" y1={y} x2="352" y2={y} className="chart-grid" />)}
      <path d={`${path} L352,86 L8,86 Z`} className="chart-area" />
      <path d={path} className="chart-line" />
      <line x1={current[0]} y1="7" x2={current[0]} y2="86" className="chart-cursor" />
      <circle cx={current[0]} cy={current[1]} r="4.5" className="chart-dot" />
      <text x="8" y="93">START</text><text x="170" y="93">+36 H</text><text x="324" y="93">+72 H</text>
    </svg>
  );
}

function ToolButton({ title, active, disabled, onClick, children, icon: Icon }) {
  return (
    <button className={`tool-button ${active ? 'active' : ''} ${disabled ? 'muted' : ''}`} title={title} aria-label={title} aria-pressed={Boolean(active)} onClick={onClick} type="button">
      {Icon ? <Icon size={19} strokeWidth={1.9} /> : children}
    </button>
  );
}

function MetricLine({ label, value, tone }) {
  return <div className="metric-line"><span>{label}</span><strong style={tone ? { color: tone } : undefined}>{value}</strong></div>;
}

export default function App() {
  const [mode, setMode] = useState('forecast');
  const [surveyView, setSurveyView] = useState('sensors');
  const [dimension, setDimension] = useState('2D');
  const [metric, setMetric] = useState('aqi');
  const [scenario, setScenario] = useState('compound');
  const [hour, setHour] = useState(18);
  const [coupled, setCoupled] = useState(true);
  const [playing, setPlaying] = useState(false);
  const [activeTab, setActiveTab] = useState('Forecast');
  const [panelOpen, setPanelOpen] = useState(true);
  const [selection, setSelection] = useState([]);
  const [selectionDraft, setSelectionDraft] = useState(blankCorners);
  const [selecting, setSelecting] = useState(false);
  const [inspected, setInspected] = useState(null);
  const [replay, setReplay] = useState({ samples: [], totalRows: 0, windSources: {}, durationSeconds: 1211, hotspots: [], encounters: [], playbackFrames: [], voxelMeta: null });
  const [replayError, setReplayError] = useState('');
  const [surveyProgress, setSurveyProgress] = useState(0.86);
  const [menu, setMenu] = useState('');
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [toast, setToast] = useState('');
  const [orbitOn, setOrbitOn] = useState(false);
  const mapInstanceRef = useRef(null);
  const uploadRef = useRef(null);

  const cells = useMemo(() => makeGrid(hour, scenario, coupled), [hour, scenario, coupled]);
  const timeline = useMemo(() => makeTimeline(scenario, coupled), [scenario, coupled]);
  const selectedArea = useMemo(() => areaStats(selection), [selection]);
  const center = useMemo(() => scenarioAt(77.215, 28.63, hour, scenario, coupled), [hour, scenario, coupled]);
  const withoutFeedback = useMemo(() => scenarioAt(77.215, 28.63, hour, scenario, false), [hour, scenario]);
  const category = aqiCategory(center.aqi);
  const currentMetric = POLLUTANTS[metric];
  const metricScale = scaleFor(metric);
  const surveyDuration = surveyView === 'particles' ? 300 : replay.durationSeconds || 1211;
  const surveySeconds = Math.round(surveyProgress * surveyDuration);
  const surveyTime = formatDuration(surveySeconds);
  const currentSample = replay.samples[Math.min(replay.samples.length - 1, Math.max(0, Math.round(surveyProgress * replay.samples.length) - 1))];

  useEffect(() => {
    loadBundledReplay().then(setReplay).catch((error) => setReplayError(error.message));
  }, []);

  useEffect(() => {
    if (!playing) return undefined;
    const timer = window.setInterval(() => {
      if (mode === 'forecast') setHour((value) => value >= 72 ? 0 : value + 1);
      else setSurveyProgress((value) => value >= 1 ? 0 : Math.min(1, value + 0.015));
    }, 800);
    return () => window.clearInterval(timer);
  }, [playing, mode]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(''), 3400);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') {
        setMenu('');
        setMobileMenuOpen(false);
      }
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, []);

  function switchMode(next) {
    setMode(next);
    if (next === 'survey') { setSurveyView('sensors'); setSurveyProgress(0.86); }
    setPlaying(false);
    setInspected(null);
    setSelection([]);
    setSelectionDraft(blankCorners());
    setSelecting(false);
    setMenu('');
    setMobileMenuOpen(false);
    setActiveTab('Forecast');
  }

  function addSelectionPoint(point) {
    const next = selection.length >= 4 ? [point] : [...selection, point];
    setSelection(next);
    setSelectionDraft(blankCorners().map((corner, index) => next[index] ? { lat: next[index][1].toFixed(6), lng: next[index][0].toFixed(6) } : corner));
    if (next.length === 4) setSelecting(false);
  }

  function chooseDimension(next) {
    setDimension(next);
    if (next === '2D') setOrbitOn(false);
  }

  function chooseMetric(next) {
    setMetric(next);
    if (mode !== 'forecast') switchMode('forecast');
  }

  function clearSelection() {
    setSelection([]);
    setSelectionDraft(blankCorners());
    setSelecting(false);
    setInspected(null);
  }

  function beginSelection() {
    setSelecting(true);
    if (window.matchMedia('(max-width: 820px)').matches) setPanelOpen(false);
    setToast('Click four map corners to measure an area.');
  }

  function applyGpsCorners() {
    const corners = selectionDraft.map(({ lat, lng }) => [Number(lng), Number(lat)]);
    if (selectionDraft.some(({ lat, lng }) => !lat.trim() || !lng.trim()) || corners.some(([lng, lat]) => !Number.isFinite(lat) || !Number.isFinite(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180)) {
      setToast('Enter four valid latitude / longitude pairs.');
      return;
    }
    setSelection(corners);
    setSelecting(false);
    setToast('Selected area applied.');
  }

  function activateOrbit() {
    setDimension('3D');
    setOrbitOn((value) => !value);
    const map = mapInstanceRef.current;
    if (map) map.easeTo({ bearing: map.getBearing() + 35, pitch: 60, duration: 900 });
    setToast('Drag the map to orbit and adjust pitch.');
  }

  async function handleUpload(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const parsed = parseReplayCsv(await file.text());
      setReplay((previous) => ({
        ...parsed,
        playbackFrames: previous.playbackFrames,
        voxelMeta: previous.voxelMeta,
      }));
      setReplayError('');
      switchMode('survey');
      setToast(`Loaded ${parsed.totalRows.toLocaleString('en-IN')} simulated replay rows.`);
    } catch (error) {
      setReplayError(error.message);
      setToast(`CSV could not be loaded: ${error.message}`);
    }
    event.target.value = '';
  }

  function runMenuAction(action) {
    setMenu('');
    setMobileMenuOpen(false);
    if (action === 'import') uploadRef.current?.click();
    else if (action === 'csv') { exportCsv(cells, scenario, hour); setToast('Illustrative scenario CSV exported.'); }
    else if (action === 'geojson') { exportGeoJson(cells, metric, scenario, hour); setToast('Illustrative scenario GeoJSON exported.'); }
    else if (action === 'select') beginSelection();
    else if (action === 'clear') clearSelection();
    else if (action === '2d') chooseDimension('2D');
    else if (action === '3d') chooseDimension('3D');
    else if (action === 'forecast') switchMode('forecast');
    else if (action === 'survey') switchMode('survey');
    else if (action === 'simulation') switchMode('simulation');
  }

  const menuItems = {
    File: [['Import simulated CSV', 'import'], ['Export CSV', 'csv'], ['Export GeoJSON', 'geojson']],
    Edit: [['Select four corners', 'select'], ['Clear selection', 'clear']],
    Select: [['Select area', 'select'], ['Clear area', 'clear']],
    View: [['2D heatmap', '2d'], ['3D voxels', '3d']],
    Mode: [['72-hour forecast', 'forecast'], ['Drone and particle replay', 'survey'], ['Original simulator', 'simulation']],
  };

  return (
    <>
    <NativeSimulatorWorkspace active={mode === 'simulation'} onForecast={() => { switchMode('forecast'); setPanelOpen(true); }} onReplay={() => { switchMode('survey'); setPanelOpen(true); }} />
    <div className="app-shell" style={mode === 'simulation' ? { display: 'none' } : undefined} onClick={() => { setMenu(''); setMobileMenuOpen(false); }}>
      <header className="topbar">
        <div className="brand" aria-label="VoxMap Simulator"><span className="brand-mark"><span className="brand-dot" /></span><span className="brand-name">VoxMap<span className="brand-caption">SIMULATOR</span></span></div>
        <nav className={`top-menus ${mobileMenuOpen ? 'mobile-open' : ''}`} aria-label="Main menu" onClick={(event) => event.stopPropagation()}>
          {Object.keys(menuItems).map((name) => (
            <div className="menu-wrap" key={name}>
              <button className={`menu-trigger ${menu === name ? 'open' : ''}`} type="button" aria-expanded={menu === name} aria-controls={`nav-menu-${name}`} onClick={(event) => { event.stopPropagation(); setMenu(menu === name ? '' : name); }}>{name}<ChevronDown size={13} strokeWidth={2} aria-hidden="true" /></button>
              {menu === name && <div className="menu-popover" id={`nav-menu-${name}`} onClick={(event) => event.stopPropagation()}>
                <div className="menu-popover-title">{name} actions</div>
                {menuItems[name].map(([label, action]) => {
                  const selected = (name === 'View' && dimension.toLowerCase() === action) || (name === 'Mode' && mode === action);
                  return <button type="button" key={action + label} className={selected ? 'is-current' : ''} onClick={() => runMenuAction(action)}>{label}{selected && <span className="menu-current-dot" aria-label="Current" />}</button>;
                })}
              </div>}
            </div>
          ))}
        </nav>
        <div className="top-status"><span className="status-pulse" /><span>INTERACTIVE DEMO<span className="status-separator">/</span>ILLUSTRATIVE DATA</span></div>
        <button className="top-help" type="button" title="About this demo" aria-label="About this demo" onClick={() => { setActiveTab('Data'); setPanelOpen(true); }}><CircleHelp size={17} /></button>
        <button className={`mobile-menu-toggle ${mobileMenuOpen ? 'open' : ''}`} type="button" aria-label={mobileMenuOpen ? 'Close main menu' : 'Open main menu'} aria-expanded={mobileMenuOpen} onClick={(event) => { event.stopPropagation(); setMobileMenuOpen((open) => !open); setMenu(''); }}><Menu size={19} /></button>
      </header>

      <main className={`workspace ${panelOpen ? 'panel-open' : ''} ${mode === 'simulation' ? 'simulation-mode' : ''}`}>
        <MapView
          cells={cells} metric={metric} dimension={dimension} mode={mode}
          surveyView={surveyView} surveySamples={replay.samples} surveyProgress={surveyProgress}
          surveyDuration={replay.durationSeconds || 1211} surveyHotspots={replay.hotspots}
          playbackFrames={replay.playbackFrames} voxelMeta={replay.voxelMeta}
          selecting={selecting} selection={selection} onSelectPoint={addSelectionPoint}
          onInspect={(properties, point) => { setInspected({ ...properties, point }); setPanelOpen(true); }}
          onMapReady={(instance) => { mapInstanceRef.current = instance; }}
        />

        <div className="left-rail" aria-label="Map tools">
          <ToolButton title="Toggle 2D or 3D" active={dimension === '3D'} onClick={() => chooseDimension(dimension === '2D' ? '3D' : '2D')}>{dimension}</ToolButton>
          <ToolButton title="Orbit tool" icon={Navigation} active={orbitOn} onClick={activateOrbit} />
          <ToolButton title="Location selector" icon={Crosshair} active={selecting} onClick={() => selecting ? setSelecting(false) : beginSelection()} />
          <ToolButton title={mode === 'forecast' ? '3D scenario columns' : '3D voxel display'} icon={Box} active={dimension === '3D'} onClick={() => chooseDimension('3D')} />
          <div className="rail-break" />
          <ToolButton title="AQI proxy layer" active={mode === 'forecast' && metric === 'aqi'} onClick={() => chooseMetric('aqi')}>AQI</ToolButton>
          <ToolButton title="PM2.5 layer" active={mode === 'forecast' && metric === 'pm25'} onClick={() => chooseMetric('pm25')}>PM<br />2.5</ToolButton>
          <ToolButton title="Open original simulator" icon={Sparkles} active={mode === 'simulation'} onClick={() => { switchMode('simulation'); setPanelOpen(true); }} />
        </div>

        <div className="map-title-pill"><span className="pill-dot" /> {mode === 'forecast' ? `DELHI NCR  /  ${SCENARIOS[scenario].short.toUpperCase()}  /  +${hour}H` : surveyView === 'particles' ? 'VOXSKY H5 PARTICLE TRACE  /  5 MIN' : 'VOXSKY FIELD REPLAY  /  20 MIN'}</div>

        <section className={`selected-area-card ${selecting ? 'editing' : ''} ${selectedArea ? 'has-area' : ''}`} aria-label="Selected area summary">
          <div className="section-overline">SELECTED AREA</div>
          <MetricLine label="Area" value={selectedArea ? `${Math.round(selectedArea.area).toLocaleString('en-IN')} m²` : '—'} />
          <MetricLine label="Hectares / km²" value={selectedArea ? `${(selectedArea.area / 10_000).toFixed(2)} ha · ${(selectedArea.area / 1_000_000).toFixed(2)} km²` : '—'} />
          <MetricLine label="Perimeter" value={selectedArea ? `${Math.round(selectedArea.perimeter).toLocaleString('en-IN')} m` : '—'} />
          {selecting && <div className="gps-editor"><span className="tiny-label">SELECTION CORNERS (GPS)</span><p>Click the map or enter four corners.</p>{selectionDraft.map((corner, index) => <div className="gps-corner" key={index}><span>{index + 1}</span><input aria-label={`Corner ${index + 1} latitude`} inputMode="decimal" placeholder="Latitude" value={corner.lat} onChange={(event) => setSelectionDraft((previous) => previous.map((item, itemIndex) => itemIndex === index ? { ...item, lat: event.target.value } : item))} /><input aria-label={`Corner ${index + 1} longitude`} inputMode="decimal" placeholder="Longitude" value={corner.lng} onChange={(event) => setSelectionDraft((previous) => previous.map((item, itemIndex) => itemIndex === index ? { ...item, lng: event.target.value } : item))} /></div>)}</div>}
          {(selection.length > 0 || selecting) && <div className="selection-actions"><span>{selection.length}/4 corners</span><button type="button" onClick={clearSelection}>Clear</button>{selecting && <button type="button" onClick={applyGpsCorners}>Apply</button>}<button type="button" disabled={!selectedArea} onClick={() => setSelecting(false)}>Done</button></div>}
        </section>

        <button className="panel-toggle" type="button" aria-label={panelOpen ? 'Close forecast panel' : 'Open forecast panel'} onClick={() => setPanelOpen(!panelOpen)}>{panelOpen ? <ChevronRight size={19} /> : <ChevronLeft size={19} />}</button>

        {panelOpen && <aside className="insight-panel" aria-label="Forecast and simulator insights">
          <div className="panel-heading"><div><span className="section-overline">VOXMAP SIMULATOR</span><h1>{mode === 'forecast' ? 'Forecast workspace' : 'Field replay'}</h1></div><button className="icon-button" type="button" aria-label="Close panel" onClick={() => setPanelOpen(false)}><X size={18} /></button></div>
          <div className="mode-switch" role="group" aria-label="Workspace mode"><button className={mode === 'forecast' ? 'selected' : ''} type="button" onClick={() => switchMode('forecast')}>72-hour forecast</button><button className={mode === 'survey' ? 'selected' : ''} type="button" onClick={() => switchMode('survey')}>Drone replay</button><button className={mode === 'simulation' ? 'selected' : ''} type="button" onClick={() => switchMode('simulation')}>Original simulator</button></div>
          {mode === 'survey' && <div className="survey-switch" role="group" aria-label="Survey replay source"><button className={surveyView === 'sensors' ? 'selected' : ''} type="button" onClick={() => { setSurveyView('sensors'); setSurveyProgress(0.86); }}>20-min sensors</button><button className={surveyView === 'particles' ? 'selected' : ''} type="button" onClick={() => { setSurveyView('particles'); setSurveyProgress(0.55); }}>5-min H5 particles</button></div>}

          <>{mode === 'forecast' ? <>
            <div className="scenario-row"><div><span className="tiny-label">ACTIVE SCENARIO</span><strong>{SCENARIOS[scenario].label}</strong></div><CloudFog size={20} /></div>
            <select className="scenario-select" aria-label="Forecast scenario" value={scenario} onChange={(event) => setScenario(event.target.value)}>{Object.entries(SCENARIOS).map(([key, item]) => <option key={key} value={key}>{item.label}</option>)}</select>
            <p className="scenario-detail">{SCENARIOS[scenario].detail}</p>
            <div className="hero-aqi"><div><span>PM-BASED AQI PROXY</span><strong>{center.aqi}</strong><em style={{ color: category.color }}>{category.name}</em></div><div className="hero-aqi-right"><span>+{hour} HOUR</span><strong>{forecastTime(hour)}</strong></div></div>
          </> : <>
            <div className="scenario-row"><div><span className="tiny-label">SIMULATED DATASET</span><strong>{surveyView === 'particles' ? 'Five-minute parcel trace' : 'VoxSky 20-minute flight'}</strong></div><Activity size={20} /></div>
            <p className="scenario-detail">{surveyView === 'particles' ? 'A separate five-minute H5 simulation shows a known stack release and moving parcels. Its sensor track is background-only.' : replay.sourceFile === 'Uploaded CSV' ? 'Uploaded simulated sensor data. Inspect its route and wind provenance; plume encounters require supplied reference values.' : 'The supplied 14 Apr 2026 live-demo CSV includes brief plume encounters and measured versus interpolated wind provenance.'}</p>
            <div className="replay-stat"><strong>{surveyView === 'particles' ? replay.playbackFrames.length : replay.totalRows.toLocaleString('en-IN')}</strong><span>{surveyView === 'particles' ? 'H5 frames' : 'input rows'}</span><strong>{surveyView === 'particles' ? replay.voxelMeta?.parcels?.total || 0 : replay.encounterSummary?.n_encounters || 0}</strong><span>{surveyView === 'particles' ? 'numerical parcels' : 'plume encounters'}</span></div>
          </>}

          <div className="panel-tabs" role="tablist" aria-label="Insight views">{TABS.map((tabName) => <button key={tabName} role="tab" aria-selected={activeTab === tabName} className={activeTab === tabName ? 'active' : ''} type="button" onClick={() => setActiveTab(tabName)}>{tabName}</button>)}</div>
          <div className="panel-content">
            {inspected && <div className="inspect-card"><div className="card-head"><span className="tiny-label">MAP INSPECTION</span><button type="button" aria-label="Close inspection" onClick={() => setInspected(null)}><X size={15} /></button></div>
              <strong>{inspected.source ? inspected.name : inspected.sampleId ? `Survey sample #${inspected.sampleId}` : inspected.voxel ? `Voxel ${inspected.voxel}` : inspected.provenance === 'five_minute_h5_particle_trace' ? 'H5 trace element' : `Grid cell ${inspected.id || ''}`}</strong>
              <span className="inspect-location">{Number(inspected.point?.lat ?? inspected.lat).toFixed(4)}° N, {Number(inspected.point?.lng ?? inspected.lng).toFixed(4)}° E</span>
              {inspected.source ? <p>{inspected.provenance === 'H5 configured source' ? 'Configured stack in the separate five-minute H5 research trace; not inferred from the 20-minute flight.' : 'Potential source region. Field attribution remains a hypothesis until validated with observations.'}</p> : mode === 'forecast' ? <><MetricLine label={`${currentMetric.label} · illustrative`} value={`${inspected.value ?? inspected[metric] ?? '—'} ${currentMetric.unit}`} /><MetricLine label="PM2.5 scenario" value={`${inspected.pm25 ?? '—'} µg/m³`} /><MetricLine label="Inversion" value={`${inspected.inversion ?? '—'} / 100`} /><MetricLine label="Boundary layer" value={`${inspected.pbl ?? '—'} m`} /><p>Click values describe this modeled grid cell, not a sensor observation.</p></> : inspected.provenance === 'five_minute_h5_particle_trace' ? <><MetricLine label="Trace element" value={inspected.particle != null ? `Particle ${inspected.particle}` : 'Voxel cluster'} /><MetricLine label="Height above ground" value={inspected.up != null ? `${inspected.up} m` : `${inspected.base ?? '—'} m`} /><MetricLine label="Source" value="Separate H5 research run" /></> : <><MetricLine label="PM2.5 sensor" value={`${inspected.pm25 ?? '—'} µg/m³`} /><MetricLine label="PM2.5 simulated truth" value={`${inspected.referencePm25 ?? inspected.ref_pm25 ?? '—'} µg/m³`} /><MetricLine label="PM10 sensor" value={`${inspected.pm10 ?? '—'} µg/m³`} /><MetricLine label="Wind source" value={inspected.windSource || inspected.wind_source || 'unknown'} /><MetricLine label="Height above takeoff" value={inspected.altitude != null ? `${Math.round(Number(inspected.altitude))} m` : '—'} />{(inspected.qualityFlags || inspected.flags) && <small className="inspect-flags">Flags: {inspected.qualityFlags || inspected.flags}</small>}</>}
            </div>}

            {activeTab === 'Forecast' && mode === 'forecast' && <>
              <div className="panel-block-title"><span>72-HOUR OUTLOOK</span><span>DELHI CENTRE</span></div>
              <MiniChart points={timeline} currentHour={hour} />
              <div className="three-stats"><div><span>PM2.5</span><strong>{Math.round(center.pm25)}</strong><small>µg/m³</small></div><div><span>PM10</span><strong>{Math.round(center.pm10)}</strong><small>µg/m³</small></div><div><span>O₃</span><strong>{Math.round(center.o3)}</strong><small>µg/m³</small></div></div>
              <div className="panel-block-title"><span>METEOROLOGY</span><span>COUPLED SCENARIO</span></div>
              <MetricLine label={<><Wind size={15} /> Wind speed</>} value={`${center.wind} m/s · NW → SE`} />
              <MetricLine label={<><Thermometer size={15} /> Temperature</>} value={`${center.temperature} °C`} />
              <MetricLine label="Boundary layer height" value={`${center.pbl} m`} />
              <MetricLine label="Inversion strength" value={`${center.inversion} / 100`} tone={center.inversion > 65 ? '#f5a270' : undefined} />
              <div className="readout-note"><CloudFog size={16} /><span>Regional plume contribution at Delhi centre: <strong>{center.regionalShare}%</strong> in this illustrative scenario.</span></div>
            </>}

            {activeTab === 'Physics' && mode === 'forecast' && <>
              <div className="panel-block-title"><span>TWO-WAY FEEDBACK</span><span>EXPLAINABLE</span></div>
              <div className="feedback-flow"><div><Wind size={17} /> Wind + inversion</div><ChevronDown size={15} /><div><CloudFog size={17} /> Transport + mixing</div><ChevronDown size={15} /><div><Activity size={17} /> PM + aerosols</div><ChevronDown size={15} /><div><Thermometer size={17} /> Heating + PBL change</div></div>
              <label className="toggle-row"><span><strong>Two-way feedback</strong><small>Compare coupled and one-way behavior</small></span><input type="checkbox" checked={coupled} onChange={(event) => setCoupled(event.target.checked)} /><i aria-hidden="true" /></label>
              <div className="comparison-card"><span>DELHI CENTRE · +{hour}H</span><MetricLine label="PM2.5 with feedback" value={`${center.pm25} µg/m³`} /><MetricLine label="Without aerosol feedback" value={`${withoutFeedback.pm25} µg/m³`} /><MetricLine label="Aerosol heating reduction" value={`${center.aerosolCooling}%`} /><MetricLine label="Boundary-layer height" value={`${center.pbl} m`} /></div>
              <p className="fine-print">A deterministic scenario equation illustrates the coupling. It is not WRF-Chem or a trained forecast model.</p>
            </>}

            {activeTab === 'Physics' && mode === 'survey' && <><div className="panel-block-title"><span>FIELD MODEL</span><span>RESEARCH TRACE</span></div><div className="feedback-flow"><div><Wind size={17} /> Hover anchors + ground wind</div><ChevronDown size={15} /><div><Activity size={17} /> Reconstructed wind field</div><ChevronDown size={15} /><div><CloudFog size={17} /> Advection + diffusion</div><ChevronDown size={15} /><div><Box size={17} /> 20 × 20 × 10 m voxels</div></div><div className="comparison-card"><MetricLine label="Measured wind rows" value={(replay.windSources.measured || 0).toLocaleString('en-IN')} /><MetricLine label="Interpolated wind rows" value={(replay.windSources.interpolated || 0).toLocaleString('en-IN')} /><MetricLine label="Raw wind missing" value="about 49%" /><MetricLine label="Trace scope" value="Forward simulation" /></div><p className="fine-print">Wind measurements and reconstructed values are kept distinct. This supplied trace does not contain temperature, PBL, ozone or NOx observations.</p></>}

            {activeTab === 'Sources' && mode === 'forecast' && <>
              <div className="panel-block-title"><span>SOURCE HYPOTHESES</span><span>NOT CONFIRMED</span></div>
              {SOURCES.map((source, index) => <button key={source.id} className="source-list-item" type="button" onClick={() => setInspected({ ...source, source: true, point: source })}><span className="source-number" style={{ borderColor: source.color, color: source.color }}>0{index + 1}</span><span><strong>{source.name}</strong><small>{source.type} · inspect region</small></span><ChevronRight size={15} /></button>)}
              <div className="source-brief"><Flame size={19} /><div><strong>Regional plume pathway</strong><p>North-west inflow is transported toward Delhi. The selected scenario controls its strength and the inversion controls near-surface trapping.</p></div></div>
              <div className="source-brief soft"><MapPin size={19} /><div><strong>Inspection priority</strong><p>Compare upwind and downwind measurements near the highest gradient before attributing a source.</p></div></div>
            </>}

            {activeTab === 'Sources' && mode === 'survey' && <><div className="panel-block-title"><span>ATTRIBUTION STATUS</span><span>NOT VALIDATED</span></div>{surveyView === 'particles' ? <><div className="source-brief"><Flame size={19} /><div><strong>Known simulated stack</strong><p>Latitude {replay.voxelMeta?.source?.latitude_deg?.toFixed(5) || '—'}, longitude {replay.voxelMeta?.source?.longitude_deg?.toFixed(5) || '—'}. Configured at {replay.voxelMeta?.source?.stack_height_m || '—'} m with PM2.5 release {replay.voxelMeta?.source?.pm25_emission_g_s || '—'} g/s.</p></div></div><p className="fine-print">This is the input source of the H5 simulation, not an inverse-model result.</p></> : <><div className="source-brief"><Flame size={19} /><div><strong>Supplied plume encounters</strong><p>{replay.encounterSummary?.n_encounters || 0} simulated flight rows intersect a plume. Orange map markers identify high-concentration reference points.</p></div></div><div className="source-brief soft"><MapPin size={19} /><div><strong>Next inspection step</strong><p>Collect upwind and downwind samples around the observed gradient before estimating a source or emission rate.</p></div></div><p className="fine-print">The 20-minute CSV does not supply a validated inverse source estimate. The H5 stack belongs to a different simulation run.</p></>}</>}

            {activeTab === 'Data' && <>
              <div className="panel-block-title"><span>DATA PROVENANCE</span><span>DEMO STATUS</span></div>
               <div className="provenance-card"><span className="provenance-dot blue" /><div><strong>VoxSky replay</strong><small>{replay.sourceFile === 'Uploaded CSV' ? 'Uploaded simulated drone sensor log' : 'Supplied 20-minute simulated drone sensor log'}; {replay.totalRows.toLocaleString('en-IN')} rows, {replay.encounterSummary?.n_encounters || 0} identified plume encounter rows.</small></div></div>
              <div className="provenance-card"><span className="provenance-dot blue" /><div><strong>H5 particle trace</strong><small>Separate five-minute research run; {replay.playbackFrames.length} playback frames, 20 × 20 × 10 m voxels. Sensor observations are background-only.</small></div></div>
              <div className="provenance-card"><span className="provenance-dot orange" /><div><strong>72-hour forecast</strong><small>Illustrative generated scenario. No validated WRF-Chem output supplied.</small></div></div>
              <div className="provenance-card"><span className="provenance-dot green" /><div><strong>Wind provenance</strong><small>{Object.entries(replay.windSources).map(([name, count]) => `${name}: ${count.toLocaleString('en-IN')}`).join(' · ') || 'Loading replay metadata…'}. About 49% of raw wind values are absent and filled in the simulation.</small></div></div>
              {replayError && <p className="data-error">{replayError}</p>}
              <p className="fine-print">The AQI proxy uses PM2.5 and PM10 breakpoint interpolation. Official AQI requires the prescribed averaging periods and sufficient pollutant observations.</p>
              <div className="export-actions"><button type="button" onClick={() => uploadRef.current?.click()}><Upload size={15} /> Upload CSV</button><button type="button" onClick={() => exportCsv(cells, scenario, hour)}><FileDown size={15} /> CSV</button><button type="button" onClick={() => exportGeoJson(cells, metric, scenario, hour)}><ArrowDownToLine size={15} /> GeoJSON</button></div>
            </>}

            {activeTab === 'Forecast' && mode === 'survey' && <><div className="panel-block-title"><span>{surveyView === 'particles' ? 'PARCEL TRACE' : 'FLIGHT REPLAY'}</span><span>{Math.round(surveyProgress * 100)}% COMPLETE</span></div>{surveyView === 'sensors' && currentSample && <div className="three-stats"><div><span>PM2.5 SENSOR</span><strong>{Math.round(currentSample.pm25)}</strong><small>µg/m³</small></div><div><span>SIMULATED TRUTH</span><strong>{Math.round(currentSample.referencePm25)}</strong><small>µg/m³</small></div><div><span>WIND</span><strong>{currentSample.wind}</strong><small>m/s</small></div></div>}<div className="comparison-card"><MetricLine label={surveyView === 'particles' ? 'Playback frame' : 'Mapped sensor points'} value={surveyView === 'particles' ? `${Math.round(surveyProgress * Math.max(replay.playbackFrames.length - 1, 0)) + 1} / ${replay.playbackFrames.length}` : `${Math.round(replay.samples.length * surveyProgress)} / ${replay.samples.length}`} /><MetricLine label="Simulation source" value={surveyView === 'particles' ? 'H5 research trace' : 'live-demo CSV'} /><MetricLine label="Voxel dimensions" value="20 × 20 × 10 m" />{surveyView === 'particles' ? <><MetricLine label="Configured stack height" value={`${replay.voxelMeta?.source?.stack_height_m || '—'} m`} /><MetricLine label="PM2.5 background" value={`${replay.voxelMeta?.background?.background_pm25_ug_m3 || '—'} µg/m³`} /></> : <><MetricLine label="Peak reference PM2.5" value={`${replay.encounterSummary?.max_ref_pm25 || '—'} µg/m³`} /><MetricLine label="Wind at cursor" value={currentSample?.windSource || '—'} /></>}</div><p className="fine-print">{surveyView === 'particles' ? 'The separate five-minute trace visualizes simulated parcels from a configured stack. Its flight sensor values show background only.' : 'Orange rings identify the strongest supplied plume encounters. Click a map point or voxel to inspect sensor concentration, simulated truth, wind provenance and quality flags.'}</p></>}
          </div>
          </>
        </aside>}

        <div className={`bottom-dock ${panelOpen ? 'with-panel' : ''}`}>
          <div className="dock-head"><div><span className="section-overline">{mode === 'forecast' ? 'ILLUSTRATIVE FORECAST TIME' : surveyView === 'particles' ? 'H5 PARCEL TIME' : 'FLIGHT REPLAY'}</span><strong>{mode === 'forecast' ? forecastTime(hour) : `${surveyTime} / ${formatDuration(surveyDuration)}`}</strong></div><div className="lead-chip">{mode === 'forecast' ? `+${hour} HOURS` : `${Math.round(surveyProgress * 100)}%`}</div></div>
          <div className="timeline-controls"><button type="button" aria-label={playing ? 'Pause animation' : 'Play animation'} className="play-button" onClick={() => setPlaying(!playing)}>{playing ? <Pause size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" />}</button><input aria-label={mode === 'forecast' ? 'Forecast lead hour' : 'Survey replay progress'} type="range" min="0" max={mode === 'forecast' ? '72' : '100'} step="1" value={mode === 'forecast' ? hour : Math.round(surveyProgress * 100)} onChange={(event) => { if (mode === 'forecast') setHour(Number(event.target.value)); else setSurveyProgress(Number(event.target.value) / 100); }} /><button type="button" className="reset-button" aria-label="Restart timeline" onClick={() => { setHour(0); setSurveyProgress(0); setPlaying(false); }}><RotateCcw size={15} /></button></div>
          <div className="timeline-ticks"><span>{mode === 'forecast' ? 'DEMO START' : 'START'}</span><span>{mode === 'forecast' ? '+24 H' : formatDuration(surveyDuration / 3)}</span><span>{mode === 'forecast' ? '+48 H' : formatDuration(surveyDuration * 2 / 3)}</span><span>{mode === 'forecast' ? '+72 H' : formatDuration(surveyDuration)}</span></div>
        </div>

        <div className="bottom-left-actions">{mode === 'forecast' ? <><div className="metric-picker"><Layers3 size={16} /><select aria-label="Displayed pollutant" value={metric} onChange={(event) => chooseMetric(event.target.value)}>{METRIC_OPTIONS.map((key) => <option key={key} value={key}>{POLLUTANTS[key].label}</option>)}</select><ChevronDown size={14} /></div><div className="map-scale" role="img" aria-label={`${currentMetric.label} illustrative color scale from ${metricScale.values[0]} to ${metricScale.values.at(-1)} ${currentMetric.unit}`}><div className="scale-title">{currentMetric.legend}</div><div className="scale-gradient" style={{ background: `linear-gradient(90deg, ${metricScale.colors.join(', ')})` }} /><div className="scale-ticks"><span>{metricScale.values[0]}</span><span>{metricScale.values.at(-1)}+ {currentMetric.unit}</span></div></div></> : surveyView === 'particles' ? <div className="survey-legend"><span className="trace-particle-key" /> Displayed parcel sample <span className="survey-legend-detail">Up to 150 per frame · separate H5 run</span></div> : <div className="survey-legend"><span className="survey-line-key" /> Drone path <span className="survey-hotspot-key" /> Plume encounter <span className="survey-legend-detail">Simulated replay</span></div>}</div>
        {toast && <div className="toast" role="status">{toast}</div>}
        <input ref={uploadRef} className="visually-hidden" type="file" accept=".csv,text/csv" onChange={handleUpload} aria-label="Upload simulated drone CSV" />
      </main>
    </div>
    </>
  );
}
