import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, ArrowLeft, FlaskConical, RefreshCw, Wind } from 'lucide-react';

function embedStyles(styles) {
  return styles
    .replace(/^:root\s*\{/, ':host {')
    .replace('html, body, #root {', ':host, .simulator-embedded-root {')
    .replace('body { min-width: 1060px; background: var(--bg); }', ':host { display: block; min-width: 0; background: var(--bg); }')
    + '\n.simulator-embedded-root { min-width: 0; }'
    + '\n.simulator-embedded-root .app-shell { min-height: calc(100vh - 108px); }'
    + '\n@media (max-width: 820px) { .simulator-embedded-root .app-shell { display: block; } .simulator-embedded-root .sidebar { position: relative; height: auto; padding: 12px; } .simulator-embedded-root .step-nav { flex-direction: row; overflow-x: auto; } .simulator-embedded-root .step-nav::before, .simulator-embedded-root .sidebar-spacer, .simulator-embedded-root .session-card, .simulator-embedded-root .sidebar-disclaimer { display: none; } .simulator-embedded-root .step-link { min-width: 146px; } .simulator-embedded-root .workspace { min-width: 0; } .simulator-embedded-root .page-container { padding: 18px 12px; } }';
}

export default function NativeSimulatorWorkspace({ active, onForecast, onReplay }) {
  const [status, setStatus] = useState('checking');
  const [activated, setActivated] = useState(false);
  const [mountError, setMountError] = useState('');
  const [reloadToken, setReloadToken] = useState(0);
  const hostRef = useRef(null);
  const rootRef = useRef(null);

  useEffect(() => { if (active) setActivated(true); }, [active]);
  useEffect(() => {
    if (!active) return undefined;
    let mounted = true;
    const check = async () => {
      try {
        const response = await fetch('/api/health', { cache: 'no-store', signal: AbortSignal.timeout(2000) });
        const payload = await response.json();
        if (mounted) setStatus(response.ok && payload.status === 'ok' ? 'ready' : 'unavailable');
      } catch { if (mounted) setStatus('unavailable'); }
    };
    void check();
    const timer = window.setInterval(check, 5000);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [active]);

  useEffect(() => {
    if (!activated || status !== 'ready' || !hostRef.current || rootRef.current) return undefined;
    let cancelled = false;
    Promise.all([
      import('../simulator-engine/desktop/src/App.tsx'),
      import('../simulator-engine/desktop/src/styles.css?inline'),
    ]).then(([component, stylesheet]) => {
      if (cancelled || !hostRef.current) return;
      const shadow = hostRef.current.shadowRoot || hostRef.current.attachShadow({ mode: 'open' });
      const style = document.createElement('style');
      style.textContent = embedStyles(stylesheet.default);
      const mount = document.createElement('div');
      mount.className = 'simulator-embedded-root';
      shadow.replaceChildren(style, mount);
      rootRef.current = createRoot(mount);
      rootRef.current.render(React.createElement(component.default));
      setMountError('');
    }).catch((error) => { if (!cancelled) setMountError(error.message || 'Could not load the simulator interface.'); });
    return () => { cancelled = true; };
  }, [activated, status, reloadToken]);

  useEffect(() => () => { rootRef.current?.unmount(); rootRef.current = null; }, []);

  function reloadWorkspace() {
    rootRef.current?.unmount();
    rootRef.current = null;
    setMountError('');
    setReloadToken((value) => value + 1);
  }

  return <section className="native-simulator-workspace" style={{ display: active ? 'flex' : 'none' }} aria-label="Supplied VoxMaps simulator">
    <div className="native-simulator-bar">
      <div className="native-simulator-identity"><span className="native-simulator-mark"><FlaskConical size={21} /></span><div><small>VOXMAPS / SIH DEMO</small><strong>Simulation workspace</strong></div></div>
      <div className="native-simulator-nav"><button type="button" onClick={onForecast}><ArrowLeft size={15} /> Forecast map</button><button type="button" onClick={onReplay}><Activity size={15} /> Drone replay</button></div>
      <div className={`native-simulator-status ${status}`}><span />{status === 'ready' ? 'LOCAL ENGINE READY' : status === 'checking' ? 'CHECKING ENGINE' : 'ENGINE OFFLINE'}</div>
    </div>
    <div className="native-simulator-context"><Wind size={16} /><span>Supplied five-minute flight and matching H5 trace are bundled below. Run the original Python solver or open the trace directly. This is a research forward simulator, not a trained ML forecast.</span>{status === 'ready' && <button type="button" onClick={reloadWorkspace} title="Reset simulator workspace" aria-label="Reset simulator workspace"><RefreshCw size={15} /></button>}</div>
    {activated && <div className="native-simulator-native" ref={hostRef} style={status === 'ready' && !mountError ? undefined : { display: 'none' }} />}
    {(status !== 'ready' || mountError) && <div className="native-simulator-offline"><FlaskConical size={36} /><h2>{mountError ? 'Simulator interface could not load' : status === 'checking' ? 'Connecting to the simulator…' : 'Start the local simulator engine'}</h2><p>{mountError || 'The numerical solver runs locally in Python. Start the full demo with the command below; this workspace will connect automatically.'}</p><code>npm run dev:full</code><small>For an existing dashboard session, run <code>npm run simulator</code> in a second terminal.</small></div>}
  </section>;
}
