import { ArrowLeft, ArrowRight, CircleEqual, Factory, Flame, LocateFixed, RotateCcw, Sparkles } from "lucide-react";
import type { DesktopConfig, Inspection } from "../types";
import { withBackgroundConcentration, withEmissionRate } from "../configPolicies";
import { Card, NumberField, RangeNumberField, SectionHeading } from "./Controls";
import { FlightMap } from "./FlightMap";

export function SourceStep({ inspection, config, onConfig, onBack, onContinue }: {
  inspection: Inspection;
  config: DesktopConfig;
  onConfig: (config: DesktopConfig) => void;
  onBack: () => void;
  onContinue: () => void;
}) {
  const source = config.engine.source;
  const background = config.background;
  const fineEmission = config.emissions.pm25_emission_g_s;
  const coarseEmission = config.emissions.coarse_pm_emission_g_s;
  const backgroundPm10 = background.pm10_ug_m3;
  const latitude = source.latitude ?? (inspection.gps_extent.min_latitude + inspection.gps_extent.max_latitude) / 2;
  const longitude = source.longitude ?? (inspection.gps_extent.min_longitude + inspection.gps_extent.max_longitude) / 2;
  const emissionEnd = source.emission_end_s ?? inspection.duration_s;
  const errors = [
    source.stack_height_m <= 0 ? "Stack height must be greater than zero." : null,
    source.stack_diameter_m <= 0 ? "Stack diameter must be greater than zero." : null,
    emissionEnd <= source.emission_start_s ? "Emission stop time must be later than its start time." : null,
    emissionEnd > inspection.duration_s ? "Emission stop time extends beyond this flight. It will continue to be simulated but not sampled after flight end." : null,
  ].filter(Boolean) as string[];

  const updateSource = <K extends keyof typeof source>(key: K, value: typeof source[K]) => {
    const next = structuredClone(config);
    next.engine.source[key] = value;
    onConfig(next);
  };

  const updateBackground = (pm25: number, pm10: number) => {
    const next = withBackgroundConcentration(withBackgroundConcentration(config, "pm25", pm25), "pm10", pm10);
    onConfig(next);
  };

  const placeAtFlightCentre = () => {
    const next = structuredClone(config);
    next.engine.source.latitude = Number(((inspection.gps_extent.min_latitude + inspection.gps_extent.max_latitude) / 2).toFixed(7));
    next.engine.source.longitude = Number(((inspection.gps_extent.min_longitude + inspection.gps_extent.max_longitude) / 2).toFixed(7));
    onConfig(next);
  };

  return (
    <div className="page-stack">
      <SectionHeading
        eyebrow="Step 2 of 5"
        title="Position and configure the source"
        description="Place the smokestack relative to the recorded flight, then define its physical and non-overlapping particulate emissions."
        action={<button type="button" className="button ghost" onClick={placeAtFlightCentre}><LocateFixed size={15} /> Centre on flight</button>}
      />

      <Card className="map-source-card">
        <div className="card-title-row">
          <div><div className="eyebrow">Geographic placement</div><h3>Smokestack location</h3><p>Click anywhere on the coordinate view or drag the orange marker. Exact fields stay synchronized.</p></div>
          <div className="source-coordinate-readout"><span>WGS84</span><strong>{latitude.toFixed(6)}, {longitude.toFixed(6)}</strong></div>
        </div>
        <FlightMap
          inspection={inspection}
          interactive
          source={{ latitude, longitude }}
          onSourceChange={(nextLatitude, nextLongitude) => {
            const next = structuredClone(config);
            next.engine.source.latitude = nextLatitude;
            next.engine.source.longitude = nextLongitude;
            onConfig(next);
          }}
        />
        <div className="coordinate-fields">
          <NumberField label="Stack latitude" value={latitude} onChange={(value) => updateSource("latitude", value)} unit="°N" min={-90} max={90} step={0.000001} tooltip="WGS84 latitude of the stack base. Changes update the map marker immediately." />
          <NumberField label="Stack longitude" value={longitude} onChange={(value) => updateSource("longitude", value)} unit="°E" min={-180} max={180} step={0.000001} tooltip="WGS84 longitude of the stack base. Changes update the map marker immediately." />
          <div className="coordinate-note"><LocateFixed size={16} /><span>The local numerical origin is derived from the flight. Original latitude and longitude remain available in the output.</span></div>
        </div>
      </Card>

      <div className="two-column equal">
        <Card>
          <div className="card-title-row"><div><div className="eyebrow">Stack geometry</div><h3><Factory size={19} /> Physical source</h3></div></div>
          <div className="control-grid two">
            <RangeNumberField label="Stack height" value={source.stack_height_m} onChange={(value) => updateSource("stack_height_m", value)} unit="m" min={1} max={200} step={1} tooltip="Physical height from ground to the stack outlet." />
            <RangeNumberField label="Stack diameter" value={source.stack_diameter_m} onChange={(value) => updateSource("stack_diameter_m", value)} unit="m" min={0.1} max={15} step={0.1} tooltip="Internal outlet diameter used to describe the source." />
            <RangeNumberField label="Exhaust velocity" value={source.exit_velocity_mps} onChange={(value) => updateSource("exit_velocity_mps", value)} unit="m/s" min={0} max={50} step={0.5} tooltip="Upward exhaust speed at the stack outlet." />
            <RangeNumberField label="Exhaust temperature" value={source.exhaust_temperature_k - 273.15} onChange={(value) => updateSource("exhaust_temperature_k", value + 273.15)} unit="°C" min={0} max={500} step={1} tooltip="Exhaust temperature retained in the trace. This simplified model uses a configured effective plume-rise term rather than a full buoyant-plume calculation." />
            <RangeNumberField label="Effective plume rise" value={source.plume_rise_m} onChange={(value) => updateSource("plume_rise_m", value)} unit="m" min={0} max={100} step={1} tooltip="Simplified additional release height above the physical stack. This is an assumption, not a CFD plume-rise solution." />
            <RangeNumberField label="Ground elevation" value={source.ground_elevation_m} onChange={(value) => updateSource("ground_elevation_m", value)} unit="m" min={-100} max={3000} step={1} tooltip="Source-ground elevation in the local vertical reference." />
          </div>
        </Card>

        <Card>
          <div className="card-title-row"><div><div className="eyebrow">Emission schedule</div><h3><Flame size={19} /> Timing and mass rate</h3></div></div>
          <div className="control-grid two">
            <RangeNumberField label="Emission start" value={source.emission_start_s} onChange={(value) => updateSource("emission_start_s", value)} unit="s" min={0} max={Math.max(1, Math.ceil(inspection.duration_s))} step={1} tooltip="Elapsed flight time when parcel emission begins." />
            <RangeNumberField label="Emission stop" value={emissionEnd} onChange={(value) => updateSource("emission_end_s", value)} unit="s" min={1} max={Math.max(1, Math.ceil(inspection.duration_s))} step={1} tooltip="Elapsed flight time when new parcel emission stops." />
            <RangeNumberField
              label="PM2.5 emission rate"
              value={fineEmission}
              onChange={(value) => {
                onConfig(withEmissionRate(config, "pm25", value));
              }}
              unit="g/s"
              min={0}
              max={10}
              step={0.01}
              tooltip="Fine-particle mass at aerodynamic diameter ≤2.5 μm. It is one non-overlapping mass channel."
            />
            <RangeNumberField
              label="Coarse PM emission rate"
              value={coarseEmission}
              onChange={(value) => {
                onConfig(withEmissionRate(config, "coarse", value));
              }}
              unit="g/s"
              min={0}
              max={20}
              step={0.01}
              tooltip="Only the 2.5–10 μm mass fraction. PM2.5 is not included here."
            />
          </div>
          <div className="identity-banner"><CircleEqual size={21} /><div><strong>PM10 emission = PM2.5 + coarse PM</strong><span>{fineEmission.toFixed(3)} + {coarseEmission.toFixed(3)} = {(fineEmission + coarseEmission).toFixed(3)} g/s</span></div><span className="no-double-count">No double-counting</span></div>
        </Card>
      </div>

      <Card>
        <div className="card-title-row"><div><div className="eyebrow">Scenario context</div><h3><Sparkles size={19} /> Background and reproducibility</h3><p>Background mass is added separately to the parcel-derived concentration. The random seed makes equivalent inputs repeatable.</p></div></div>
        <div className="control-grid four">
          <RangeNumberField label="Background PM2.5" value={background.pm25_ug_m3} onChange={(value) => updateBackground(value, Math.max(value, backgroundPm10))} unit="µg/m³" min={0} max={250} step={0.5} tooltip="Ambient fine-particle concentration before the simulated source contribution." />
          <RangeNumberField label="Background PM10" value={backgroundPm10} onChange={(value) => updateBackground(Math.min(background.pm25_ug_m3, value), value)} unit="µg/m³" min={0} max={400} step={0.5} tooltip="Total ambient PM10. It must be at least background PM2.5; internal coarse background is stored as the difference." />
          <NumberField label="Random seed" value={config.engine.project.random_seed} onChange={(value) => { const next = structuredClone(config); next.engine.project.random_seed = Math.max(0, Math.round(value)); onConfig(next); }} min={0} max={2147483647} step={1} tooltip="Same flight, settings, and seed produce deterministic numerical results." />
          <div className="identity-mini"><CircleEqual size={17} /><span>Background coarse PM</span><strong>{(background.pm10_ug_m3 - background.pm25_ug_m3).toFixed(1)} µg/m³</strong><small>PM10 − PM2.5</small></div>
        </div>
      </Card>

      {errors.length > 0 && <div className="validation-strip"><strong>Review source settings</strong>{errors.map((error) => <span key={error}>{error}</span>)}</div>}
      <div className="page-actions"><button type="button" className="button ghost large" onClick={onBack}><ArrowLeft size={17} /> Flight data</button><div className="actions-spacer" /><button type="button" className="button ghost" onClick={placeAtFlightCentre}><RotateCcw size={15} /> Re-centre source</button><button type="button" className="button primary large" disabled={errors.some((error) => !error.includes("extends beyond"))} onClick={onContinue}>Model settings <ArrowRight size={17} /></button></div>
    </div>
  );
}
