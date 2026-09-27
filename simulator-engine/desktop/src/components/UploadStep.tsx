import { useRef, useState, type DragEvent } from "react";
import { AlertTriangle, ArrowRight, CalendarClock, CheckCircle2, FileSearch, FileSpreadsheet, FileUp, Gauge, Map, RefreshCw, Rows3, SlidersHorizontal, Wind, X } from "lucide-react";
import type { Inspection } from "../types";
import { Card, SectionHeading } from "./Controls";
import { FlightMap } from "./FlightMap";

const requiredMappings = [
  { key: "elapsed_time", label: "Elapsed / uptime", required: true },
  { key: "timestamp_utc", label: "UTC timestamp", required: false },
  { key: "latitude", label: "Latitude", required: true },
  { key: "longitude", label: "Longitude", required: true },
  { key: "altitude", label: "Altitude / height", required: true },
  { key: "wind_speed", label: "Atmospheric wind speed", required: false },
  { key: "wind_direction", label: "Atmospheric wind direction", required: false },
];

export function UploadStep({
  inspection,
  busy,
  error,
  onUpload,
  onLoadDemo,
  onOpenDemoTrace,
  onMapColumns,
  onContinue,
}: {
  inspection: Inspection | null;
  busy: boolean;
  error: string | null;
  onUpload: (file: File) => Promise<void>;
  onLoadDemo: () => Promise<void>;
  onOpenDemoTrace: () => Promise<void>;
  onMapColumns: (mapping: Record<string, string>) => Promise<void>;
  onContinue: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [showMapping, setShowMapping] = useState(false);
  const [mappingBusy, setMappingBusy] = useState(false);
  const [detailsTab, setDetailsTab] = useState<"retained" | "excluded">("retained");

  const selectFile = async (file?: File) => {
    if (!file) return;
    if (!/\.(csv|xlsx)$/i.test(file.name)) return;
    await onUpload(file);
  };

  const handleDrop = async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    await selectFile(event.dataTransfer.files[0]);
  };

  return (
    <div className="page-stack">
      <SectionHeading
        eyebrow="Step 1 of 5"
        title="Upload flight data"
        description="Start with an AirData CSV or a VoxMaps Simplified Format Data (SFD) workbook. VoxMaps reads it without changing the source file."
      />

      <div className="bundled-demo-card">
        <div><span className="eyebrow">Supplied SIH demo data · simulated</span><h3>Five-minute Delhi flight and matching trace</h3><p>Load the raw AirData-style flight, configure the source and wind model, then run the original Python solver. The H5 button opens a precomputed run without recomputing it.</p><div className="bundled-demo-facts"><span>3,000 flight rows</span><span>PM2.5 + PM10</span><span>60 H5 frames</span><span>20 × 20 × 10 m voxels</span></div><small>The flight sensor in this five-minute run remains at background concentration; the separate 20-minute dashboard replay contains brief plume encounters.</small></div>
        <div className="bundled-demo-actions"><button type="button" className="button primary" disabled={busy} onClick={() => void onLoadDemo()}>{busy ? "Loading flight..." : "Load demo flight"}</button><button type="button" className="button ghost" disabled={busy} onClick={() => void onOpenDemoTrace()}>Open supplied H5 replay</button></div>
      </div>

      {!inspection ? (
        <Card className="upload-card">
          <div
            className={`drop-zone ${dragging ? "dragging" : ""} ${busy ? "busy" : ""}`}
            onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
          >
            <input ref={inputRef} type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden onChange={(event) => void selectFile(event.target.files?.[0])} />
            <div className="upload-illustration"><FileSpreadsheet size={38} /><span><FileSearch size={20} /></span></div>
            <h3>{busy ? "Inspecting flight data…" : "Drop an AirData CSV or SFD workbook here"}</h3>
            <p>{busy ? "Detecting coordinates, time, altitude, wind, units and missing values." : "or choose a CSV/XLSX file from this computer"}</p>
            <button type="button" className="button primary" disabled={busy} onClick={() => inputRef.current?.click()}>
              {busy ? <><span className="spinner" /> Inspecting file</> : <><FileUp size={17} /> Choose flight file</>}
            </button>
            <div className="drop-notes"><span>AirData-style headers recognized automatically</span><span>Original file stays unchanged</span></div>
          </div>
          {error && <div className="alert error"><AlertTriangle size={17} /><div><strong>Could not inspect this flight file</strong><p>{error}</p></div></div>}
        </Card>
      ) : (
        <>
          <Card className="file-summary-card">
            <div className="file-summary-header">
              <div className="file-icon"><FileSpreadsheet size={26} /></div>
              <div><div className="eyebrow">Flight file ready</div><h3>{inspection.filename}</h3><p>Read-only inspection complete</p></div>
              <div className="verified-pill"><CheckCircle2 size={15} /> Parsed successfully</div>
              <button type="button" className="button ghost" onClick={() => inputRef.current?.click()} disabled={busy}><RefreshCw size={15} /> Replace</button>
              <input ref={inputRef} type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden onChange={(event) => void selectFile(event.target.files?.[0])} />
            </div>
            <div className="stat-grid six">
              <Stat icon={<Rows3 />} label="Rows" value={inspection.row_count.toLocaleString()} helper={inspection.usable_row_count && inspection.usable_row_count !== inspection.row_count ? `${inspection.usable_row_count.toLocaleString()} usable` : "all inspected"} />
              <Stat icon={<CalendarClock />} label="Duration" value={formatDuration(inspection.duration_s)} helper={inspection.sampling_frequency_hz ? `≈ ${inspection.sampling_frequency_hz.toFixed(1)} Hz` : "time aligned"} />
              <Stat icon={<Map />} label="GPS span" value={`${formatDistanceDegrees(inspection.gps_extent.max_latitude - inspection.gps_extent.min_latitude)} × ${formatDistanceDegrees(inspection.gps_extent.max_longitude - inspection.gps_extent.min_longitude)}`} helper="latitude × longitude" />
              <Stat icon={<Gauge />} label="Altitude" value={`${inspection.altitude_range_m.min.toFixed(1)}–${inspection.altitude_range_m.max.toFixed(1)} m`} helper="normalized metres" />
              <Stat icon={<Wind />} label="Paired wind" value={`${(inspection.valid_paired_wind_coverage * 100).toFixed(1)}%`} helper={inspection.input_format === "sfd_xlsx" ? `${(inspection.sfd_original_wind_rows ?? 0).toLocaleString()} original + ${(inspection.sfd_generated_wind_rows ?? 0).toLocaleString()} generated` : `${inspection.valid_paired_wind_rows.toLocaleString()} rows`} status={inspection.valid_paired_wind_coverage >= 0.8 ? "good" : "warn"} />
              <Stat icon={<SlidersHorizontal />} label="Mapping" value={inspection.requires_mapping ? "Needs review" : "Detected"} helper={`${Object.values(inspection.detected_columns).filter(Boolean).length} core fields`} status={inspection.requires_mapping ? "warn" : "good"} />
            </div>
          </Card>

          {(inspection.warnings.length > 0 || inspection.requires_mapping) && (
            <div className="warning-stack">
              {inspection.requires_mapping && <div className="alert warning"><AlertTriangle size={17} /><div><strong>Column mapping needs confirmation</strong><p>One or more essential flight columns could not be identified with enough confidence.</p></div><button className="button small" onClick={() => setShowMapping(true)}>Map columns</button></div>}
              {inspection.warnings.map((warning, index) => <div className="alert warning compact-alert" key={`${warning}-${index}`}><AlertTriangle size={16} /><p>{warning}</p></div>)}
            </div>
          )}

          <div className="two-column layout-map">
            <Card>
              <div className="card-title-row"><div><div className="eyebrow">Recorded route</div><h3>2D flight-path preview</h3></div><span className="read-only-pill">Read only</span></div>
              <FlightMap inspection={inspection} />
            </Card>
            <Card>
              <div className="card-title-row"><div><div className="eyebrow">Schema</div><h3>Detected data contract</h3></div><button type="button" className="button ghost small" onClick={() => setShowMapping(true)}><SlidersHorizontal size={14} /> Review mapping</button></div>
              <dl className="mapping-list">
                {requiredMappings.map((field) => {
                  const detected = inspection.detected_columns[field.key] ?? (field.key === "wind_speed" ? inspection.wind_columns.speed : field.key === "wind_direction" ? inspection.wind_columns.direction : null);
                  return <div key={field.key}><dt>{field.label}</dt><dd className={detected ? "" : "missing"}>{detected ?? "Not detected"}</dd></div>;
                })}
              </dl>
              <div className="time-window">
                <div><span>First UTC sample</span><strong>{formatUtc(inspection.first_utc)}</strong></div>
                <div><span>Last UTC sample</span><strong>{formatUtc(inspection.last_utc)}</strong></div>
              </div>
            </Card>
          </div>

          <Card>
            <div className="card-title-row"><div><div className="eyebrow">Clean sensor-log policy</div><h3>Retained and excluded source columns</h3><p>Nothing is deleted from the uploaded file. This only controls the clean exported sensor log.</p></div></div>
            <div className="tabs">
              <button type="button" className={detailsTab === "retained" ? "active" : ""} onClick={() => setDetailsTab("retained")}>Retained <span>{inspection.retained_columns.length}</span></button>
              <button type="button" className={detailsTab === "excluded" ? "active" : ""} onClick={() => setDetailsTab("excluded")}>Excluded <span>{inspection.excluded_columns.length}</span></button>
            </div>
            <div className="column-table-wrap">
              <table className="data-table">
                <thead><tr><th>Original column</th><th>Category</th><th>Reason</th></tr></thead>
                <tbody>
                  {(detailsTab === "retained" ? inspection.retained_columns : inspection.excluded_columns).slice(0, 50).map((item, index) => {
                    const normalized = typeof item === "string" ? { column: item, category: "", reason: "" } : item;
                    return <tr key={`${normalized.column}-${index}`}><td className="mono">{normalized.column}</td><td><span className="category-chip">{normalized.category || (detailsTab === "retained" ? "flight data" : "unrelated telemetry")}</span></td><td>{normalized.reason || (detailsTab === "retained" ? "Required for time, flight, wind, or sensor interpretation" : "Outside the clean sensor-log contract")}</td></tr>;
                  })}
                  {(detailsTab === "retained" ? inspection.retained_columns : inspection.excluded_columns).length === 0 && <tr><td colSpan={3} className="empty-cell">No column-disposition details were reported.</td></tr>}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="page-actions"><div><strong>Next:</strong> position the source on this flight’s coordinate extent.</div><button type="button" className="button primary large" disabled={inspection.requires_mapping} onClick={onContinue}>Configure source <ArrowRight size={17} /></button></div>
        </>
      )}

      {showMapping && inspection && (
        <MappingDialog
          inspection={inspection}
          busy={mappingBusy}
          onClose={() => setShowMapping(false)}
          onApply={async (mapping) => {
            setMappingBusy(true);
            try { await onMapColumns(mapping); setShowMapping(false); } finally { setMappingBusy(false); }
          }}
        />
      )}
    </div>
  );
}

function MappingDialog({ inspection, busy, onClose, onApply }: { inspection: Inspection; busy: boolean; onClose: () => void; onApply: (mapping: Record<string, string>) => Promise<void> }) {
  const initial = Object.fromEntries(requiredMappings.map((field) => [field.key, inspection.mapping?.[field.key] ?? inspection.detected_columns[field.key] ?? (field.key === "wind_speed" ? inspection.wind_columns.speed : field.key === "wind_direction" ? inspection.wind_columns.direction : "") ?? ""]));
  const [mapping, setMapping] = useState<Record<string, string>>(initial);
  const missingRequired = requiredMappings.filter((field) => field.required && !mapping[field.key]);
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="mapping-title">
        <div className="modal-header"><div><div className="eyebrow">AirData schema</div><h2 id="mapping-title">Confirm column mapping</h2><p>Choose only genuine flight and atmospheric-wind fields. Drone velocity, heading, and flight direction are never valid wind substitutes.</p></div><button type="button" className="icon-button" onClick={onClose} disabled={busy} aria-label="Close"><X /></button></div>
        <div className="mapping-dialog-grid">
          {requiredMappings.map((field) => (
            <label className="field" key={field.key}>
              <span className="field-label"><span>{field.label}{field.required && <span className="required"> *</span>}</span></span>
              <select className="select-input" value={mapping[field.key] ?? ""} onChange={(event) => setMapping((current) => ({ ...current, [field.key]: event.target.value }))}>
                <option value="">{field.required ? "Select a column…" : "Not available / do not use"}</option>
                {inspection.available_columns.map((column) => <option key={column} value={column}>{column}</option>)}
              </select>
            </label>
          ))}
        </div>
        <div className="modal-note"><Wind size={16} /><p>Wind speed and direction must form valid numeric pairs. Placeholder strings remain missing; interpolation and fallback are configured explicitly later.</p></div>
        <div className="modal-actions"><button type="button" className="button ghost" onClick={onClose} disabled={busy}>Cancel</button><button type="button" className="button primary" disabled={busy || missingRequired.length > 0} onClick={() => void onApply(mapping)}>{busy ? <><span className="spinner" /> Re-inspecting</> : "Apply and re-inspect"}</button></div>
      </div>
    </div>
  );
}

function Stat({ icon, label, value, helper, status }: { icon: React.ReactNode; label: string; value: string; helper: string; status?: "good" | "warn" }) {
  return <div className={`stat ${status ?? ""}`}><div className="stat-icon">{icon}</div><span>{label}</span><strong>{value}</strong><small>{helper}</small></div>;
}

function formatDuration(seconds: number) {
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  return `${minutes}m ${remainder}s`;
}

function formatUtc(value?: string | null) {
  if (!value) return "Unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium", timeZone: "UTC" }) + " UTC";
}

function formatDistanceDegrees(value: number) {
  return `${Math.abs(value).toFixed(value < 0.001 ? 5 : 4)}°`;
}
