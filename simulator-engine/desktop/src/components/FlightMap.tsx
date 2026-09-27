import { useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Crosshair, Move, Navigation } from "lucide-react";
import type { CoordinatePoint, Inspection } from "../types";

const WIDTH = 900;
const HEIGHT = 440;
const PAD = 38;

interface FlightMapProps {
  inspection: Inspection;
  source?: { latitude: number; longitude: number } | null;
  onSourceChange?: (latitude: number, longitude: number) => void;
  interactive?: boolean;
}

export function FlightMap({ inspection, source = null, onSourceChange, interactive = false }: FlightMapProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [dragging, setDragging] = useState(false);
  const extent = inspection.gps_extent;
  const lonRange = Math.max(1e-7, extent.max_longitude - extent.min_longitude);
  const latRange = Math.max(1e-7, extent.max_latitude - extent.min_latitude);
  const minLon = extent.min_longitude - lonRange * 0.08;
  const maxLon = extent.max_longitude + lonRange * 0.08;
  const minLat = extent.min_latitude - latRange * 0.08;
  const maxLat = extent.max_latitude + latRange * 0.08;

  const toSvg = (point: { latitude_deg: number; longitude_deg: number }) => ({
    x: PAD + ((point.longitude_deg - minLon) / Math.max(1e-12, maxLon - minLon)) * (WIDTH - PAD * 2),
    y: HEIGHT - PAD - ((point.latitude_deg - minLat) / Math.max(1e-12, maxLat - minLat)) * (HEIGHT - PAD * 2),
  });

  const path = useMemo(() => inspection.preview_points
    .filter((point) => Number.isFinite(point.latitude_deg) && Number.isFinite(point.longitude_deg))
    .map(toSvg)
    .map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(2)},${point.y.toFixed(2)}`)
    .join(" "), [inspection.preview_points, minLon, maxLon, minLat, maxLat]);

  const sourcePoint = source ? toSvg({ latitude_deg: source.latitude, longitude_deg: source.longitude }) : null;
  const start = inspection.preview_points.length ? toSvg(inspection.preview_points[0]) : null;
  const end = inspection.preview_points.length ? toSvg(inspection.preview_points.at(-1)!) : null;

  const updateSource = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!interactive || !onSourceChange || !svgRef.current) return;
    const bounds = svgRef.current.getBoundingClientRect();
    const svgX = ((event.clientX - bounds.left) / bounds.width) * WIDTH;
    const svgY = ((event.clientY - bounds.top) / bounds.height) * HEIGHT;
    const clampedX = Math.max(PAD, Math.min(WIDTH - PAD, svgX));
    const clampedY = Math.max(PAD, Math.min(HEIGHT - PAD, svgY));
    const longitude = minLon + ((clampedX - PAD) / (WIDTH - PAD * 2)) * (maxLon - minLon);
    const latitude = minLat + ((HEIGHT - PAD - clampedY) / (HEIGHT - PAD * 2)) * (maxLat - minLat);
    onSourceChange(Number(latitude.toFixed(7)), Number(longitude.toFixed(7)));
  };

  const onPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!interactive) return;
    setDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
    updateSource(event);
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (dragging) updateSource(event);
  };

  const onPointerUp = (event: ReactPointerEvent<SVGSVGElement>) => {
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const lonTicks = Array.from({ length: 5 }, (_, index) => minLon + (index / 4) * (maxLon - minLon));
  const latTicks = Array.from({ length: 5 }, (_, index) => minLat + (index / 4) * (maxLat - minLat));

  return (
    <div className={`flight-map ${interactive ? "interactive" : ""}`}>
      <div className="map-toolbar">
        <span><Navigation size={14} /> Geographic preview</span>
        {interactive && <span className="map-hint"><Move size={14} /> Click or drag to place the stack</span>}
      </div>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={interactive ? "Flight path map. Click or drag to place the pollution source." : "Preview of uploaded flight path"}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => setDragging(false)}
      >
        <defs>
          <linearGradient id="map-background" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#102a31" />
            <stop offset="1" stopColor="#0a1b25" />
          </linearGradient>
          <filter id="map-glow"><feGaussianBlur stdDeviation="4" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>
        <rect width={WIDTH} height={HEIGHT} fill="url(#map-background)" rx="12" />
        {lonTicks.map((lon, index) => {
          const x = PAD + index * (WIDTH - PAD * 2) / 4;
          return <g key={`lon-${lon}`}><line x1={x} y1={PAD} x2={x} y2={HEIGHT - PAD} className="map-gridline" /><text x={x} y={HEIGHT - 12} textAnchor="middle" className="map-tick">{lon.toFixed(5)}°</text></g>;
        })}
        {latTicks.map((lat, index) => {
          const y = HEIGHT - PAD - index * (HEIGHT - PAD * 2) / 4;
          return <g key={`lat-${lat}`}><line x1={PAD} y1={y} x2={WIDTH - PAD} y2={y} className="map-gridline" /><text x={8} y={y + 4} className="map-tick">{lat.toFixed(5)}°</text></g>;
        })}
        <path d={path} className="flight-path-glow" />
        <path d={path} className="flight-path-line" />
        {start && <g transform={`translate(${start.x},${start.y})`}><circle r="7" className="map-start" /><text x="12" y="4" className="map-label">Start</text></g>}
        {end && <g transform={`translate(${end.x},${end.y})`}><rect x="-6" y="-6" width="12" height="12" rx="2" className="map-end" /><text x="12" y="4" className="map-label">End</text></g>}
        {sourcePoint && (
          <g transform={`translate(${sourcePoint.x},${sourcePoint.y})`} className="source-marker" filter="url(#map-glow)">
            <circle r="19" className="source-marker-ring" />
            <circle r="8" className="source-marker-core" />
            <line x1="0" y1="-29" x2="0" y2="-9" />
            <line x1="0" y1="9" x2="0" y2="29" />
            <line x1="-29" y1="0" x2="-9" y2="0" />
            <line x1="9" y1="0" x2="29" y2="0" />
          </g>
        )}
        <g transform={`translate(${WIDTH - 44},42)`} className="north-arrow"><path d="M0 -20 L8 7 L0 3 L-8 7 Z" /><text y="22" textAnchor="middle">N</text></g>
      </svg>
      <div className="map-footer">
        <span><i className="legend-line flight" /> Recorded UAV trajectory ({inspection.preview_points.length.toLocaleString()} preview points)</span>
        {source && <span><Crosshair size={13} /> Stack {source.latitude.toFixed(6)}, {source.longitude.toFixed(6)}</span>}
        <span>Offline coordinate view · WGS84</span>
      </div>
    </div>
  );
}
