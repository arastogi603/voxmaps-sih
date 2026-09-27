import type { ReactNode } from "react";
import { Activity, Check, CircleDot, CloudCog, Factory, FileUp, FlaskConical, Gauge, PlaySquare, Radio, Settings2 } from "lucide-react";
import type { AppStep, JobState } from "../types";

const steps: Array<{ id: AppStep; number: string; label: string; helper: string; icon: typeof FileUp }> = [
  { id: "upload", number: "01", label: "Flight data", helper: "Upload & inspect", icon: FileUp },
  { id: "source", number: "02", label: "Pollution source", helper: "Place & configure", icon: Factory },
  { id: "model", number: "03", label: "Model settings", helper: "Wind, physics & sensor", icon: Settings2 },
  { id: "review", number: "04", label: "Review & run", helper: "Validate & simulate", icon: Gauge },
  { id: "playback", number: "05", label: "3D playback", helper: "Inspect & audit", icon: PlaySquare },
];

export function Shell({
  children,
  step,
  onStep,
  completedSteps,
  connected,
  demoMode,
  fileName,
  jobState,
}: {
  children: ReactNode;
  step: AppStep;
  onStep: (step: AppStep) => void;
  completedSteps: Set<AppStep>;
  connected: boolean;
  demoMode: boolean;
  fileName?: string;
  jobState?: JobState | null;
}) {
  const currentIndex = steps.findIndex((candidate) => candidate.id === step);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><CloudCog size={25} /></div>
          <div><strong>VoxMaps</strong><span>Pollution Simulator</span></div>
        </div>
        <div className="research-badge"><FlaskConical size={14} /><span>Testing & research model</span></div>
        <nav className="step-nav" aria-label="Scenario workflow">
          {steps.map((item, index) => {
            const Icon = item.icon;
            const complete = completedSteps.has(item.id);
            const active = item.id === step;
            const enabled = item.id === "playback" || index <= currentIndex || complete || (item.id === "source" && completedSteps.has("upload")) || (item.id === "model" && completedSteps.has("source")) || (item.id === "review" && completedSteps.has("model"));
            return (
              <button
                key={item.id}
                type="button"
                className={`step-link ${active ? "active" : ""} ${complete ? "complete" : ""}`}
                onClick={() => enabled && onStep(item.id)}
                disabled={!enabled}
                aria-current={active ? "step" : undefined}
              >
                <span className="step-state">{complete ? <Check size={14} /> : <span>{item.number}</span>}</span>
                <Icon className="step-icon" size={18} />
                <span className="step-copy"><strong>{item.label}</strong><small>{item.helper}</small></span>
              </button>
            );
          })}
        </nav>
        <div className="sidebar-spacer" />
        <div className="session-card">
          <div className="session-title"><Radio size={14} /> Session</div>
          <div><span>Service</span><strong className={connected ? "good" : "bad"}><CircleDot size={10} /> {demoMode ? "Component demo" : connected ? "Connected" : "Offline"}</strong></div>
          <div><span>Flight</span><strong title={fileName}>{fileName ? truncate(fileName, 22) : "Not loaded"}</strong></div>
          <div><span>Solver</span><strong>{jobState ? jobStateLabel(jobState) : "Idle"}</strong></div>
        </div>
        <p className="sidebar-disclaimer">Forward simulation only. Not CFD and not a regulatory dispersion model.</p>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <div>
            <span className="breadcrumb">Scenario workspace <span>/</span> {steps.find((item) => item.id === step)?.label}</span>
          </div>
          <div className="topbar-status">
            {fileName && <span className="file-chip"><Activity size={13} /> {truncate(fileName, 36)}</span>}
            <span className={`connection-dot ${connected ? "connected" : ""}`} />
            <span>{demoMode ? "Demo data" : connected ? "Local service ready" : "Local service unavailable"}</span>
          </div>
        </header>
        <div className="page-container">{children}</div>
      </main>
    </div>
  );
}

function truncate(value: string, length: number) {
  return value.length <= length ? value : `${value.slice(0, length - 1)}…`;
}

function jobStateLabel(state: JobState) {
  return ({ queued: "Queued", running: "Running", cancelling: "Stopping", cancelled: "Cancelled", failed: "Failed", completed: "Complete" })[state];
}
