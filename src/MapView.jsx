import React, { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import { setWorkerUrl } from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import { GRID, SOURCES, colorFor, gridGeoJson } from './model.js';
import { scenarioRaster } from './rendering.js';

setWorkerUrl(workerUrl);

const emptyCollection = { type: 'FeatureCollection', features: [] };
const transparentImage = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4////fwAJ+wP9KobjigAAAABJRU5ErkJggg==';
const rasterCorners = [
  [GRID.west, GRID.north], [GRID.east, GRID.north],
  [GRID.east, GRID.south], [GRID.west, GRID.south],
];
const DELHI_VIEW = { center: [77.17, 28.65], zoom: 9.65, pitch: 50, bearing: -16 };
const SURVEY_VIEW = { center: [77.277, 28.569], zoom: 14.9, pitch: 45, bearing: -16 };
const OSM_STYLE = {
  version: 8,
  sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© OpenStreetMap contributors' } },
  layers: [{ id: 'osm-base', type: 'raster', source: 'osm' }],
};

function lineFeature(points) {
  return points.length > 1 ? { type: 'FeatureCollection', features: [{
    type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: points },
  }] } : emptyCollection;
}

function polygonFeature(points) {
  return points.length > 2 ? { type: 'FeatureCollection', features: [{
    type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[...points, points[0]]] },
  }] } : emptyCollection;
}

function localPolygon(lng, lat, base, height, properties, footprintMeters = 20) {
  const dy = footprintMeters / 2 / 111_320;
  const dx = footprintMeters / 2 / (111_320 * Math.cos(lat * Math.PI / 180));
  return { type: 'Feature', properties: { ...properties, base, height }, geometry: { type: 'Polygon', coordinates: [[
    [lng - dx, lat - dy], [lng + dx, lat - dy], [lng + dx, lat + dy], [lng - dx, lat + dy], [lng - dx, lat - dy],
  ]] } };
}

function surveyVoxelData(samples) {
  const voxels = new Map();
  samples.forEach((sample) => {
    if (!sample.voxel || !Number.isFinite(sample.lat) || !Number.isFinite(sample.lng)) return;
    const previous = voxels.get(sample.voxel);
    if (!previous || (sample.referencePm25 || sample.pm25) > (previous.referencePm25 || previous.pm25)) voxels.set(sample.voxel, sample);
  });
  return { type: 'FeatureCollection', features: [...voxels.values()].map((sample) => {
    const base = Math.max(0, Math.floor((sample.altitude || 0) / 10) * 10);
    return localPolygon(sample.lng, sample.lat, base, base + 10, { ...sample, color: colorFor('pm25', sample.referencePm25 || sample.pm25), provenance: 'simulated_sensor_voxel' });
  }) };
}

function traceData(frame, origin) {
  if (!frame || !origin) return { particles: emptyCollection, voxels: emptyCollection };
  const metersToLng = 1 / (111_320 * Math.cos(origin.lat * Math.PI / 180));
  const metersToLat = 1 / 111_320;
  const grouped = new Map();
  const particles = frame.points_en_m.map(([east, north, up], index) => {
    const lng = origin.lon + east * metersToLng;
    const lat = origin.lat + north * metersToLat;
    const base = Math.max(0, Math.floor(up / 10) * 10);
    const key = `${Math.floor(east / 20)}:${Math.floor(north / 20)}:${base}`;
    if (!grouped.has(key)) grouped.set(key, { lng, lat, base, count: 0 });
    grouped.get(key).count += 1;
    return { type: 'Feature', geometry: { type: 'Point', coordinates: [lng, lat] }, properties: { up: Math.round(up), color: '#f79553', provenance: 'five_minute_h5_particle_trace', particle: index } };
  });
  return { particles: { type: 'FeatureCollection', features: particles }, voxels: { type: 'FeatureCollection', features: [...grouped.values()].map((voxel) =>
    localPolygon(voxel.lng, voxel.lat, voxel.base, voxel.base + 10, { color: colorFor('pm25', 30 + voxel.count * 18), count: voxel.count, provenance: 'five_minute_h5_particle_trace' })) } };
}

export default function MapView({ cells, metric, dimension, mode, surveyView, surveySamples, surveyProgress, surveyDuration, surveyHotspots, playbackFrames, voxelMeta, selecting, selection, onSelectPoint, onInspect, onMapReady }) {
  const elementRef = useRef(null);
  const mapRef = useRef(null);
  const markersRef = useRef([]);
  const lastViewRef = useRef('');
  const selectRef = useRef({ selecting, onSelectPoint });
  const inspectRef = useRef(onInspect);
  const [ready, setReady] = useState(false);
  const [mapError, setMapError] = useState(false);

  selectRef.current = { selecting, onSelectPoint };
  inspectRef.current = onInspect;

  useEffect(() => {
    if (!elementRef.current || mapRef.current) return undefined;
    let map;
    try {
      map = new maplibregl.Map({
        container: elementRef.current,
        style: OSM_STYLE,
        ...DELHI_VIEW,
        maxPitch: 75,
        canvasContextAttributes: { antialias: true },
        attributionControl: false,
      });
      mapRef.current = map;
      map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: true }), 'bottom-left');
      map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');
      map.on('load', () => {
        map.addSource('scenario-grid', { type: 'geojson', data: emptyCollection });
        map.addSource('scenario-columns', { type: 'geojson', data: emptyCollection });
        map.addSource('scenario-raster', { type: 'image', url: transparentImage, coordinates: rasterCorners });
        map.addSource('selected-polygon', { type: 'geojson', data: emptyCollection });
        map.addSource('selected-corners', { type: 'geojson', data: emptyCollection });
        map.addSource('survey-points', { type: 'geojson', data: emptyCollection });
        map.addSource('survey-current', { type: 'geojson', data: emptyCollection });
        map.addSource('survey-line', { type: 'geojson', data: emptyCollection });
        map.addSource('survey-hotspots', { type: 'geojson', data: emptyCollection });
        map.addSource('survey-voxels', { type: 'geojson', data: emptyCollection });
        map.addSource('trace-particles', { type: 'geojson', data: emptyCollection });
        map.addSource('plume-axis', { type: 'geojson', data: lineFeature([[76.87, 28.96], [77.18, 28.69], [77.38, 28.52]]) });

        map.addLayer({ id: 'scenario-surface', type: 'raster', source: 'scenario-raster', paint: { 'raster-opacity': 0.68, 'raster-resampling': 'linear' } });
        map.addLayer({ id: 'grid-2d', type: 'fill', source: 'scenario-grid', paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.01 } });
        map.addLayer({ id: 'voxels-3d', type: 'fill-extrusion', source: 'scenario-columns', paint: { 'fill-extrusion-color': ['get', 'color'], 'fill-extrusion-height': ['get', 'height'], 'fill-extrusion-base': 0, 'fill-extrusion-opacity': 0.83 }, layout: { visibility: 'none' } });
        map.addLayer({ id: 'plume-axis-layer', type: 'line', source: 'plume-axis', paint: { 'line-color': '#f59e56', 'line-width': 3, 'line-opacity': 0.85, 'line-dasharray': [2, 2] } });
        map.addLayer({ id: 'survey-line-layer', type: 'line', source: 'survey-line', paint: { 'line-color': '#1c62ea', 'line-width': 5, 'line-opacity': 0.9 } });
        map.addLayer({ id: 'survey-points-layer', type: 'circle', source: 'survey-points', paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 2, 16, 5], 'circle-color': ['get', 'color'], 'circle-stroke-color': '#122044', 'circle-stroke-width': 0.8, 'circle-opacity': 0.78 } });
        map.addLayer({ id: 'survey-hotspots-layer', type: 'circle', source: 'survey-hotspots', paint: { 'circle-radius': 10, 'circle-color': '#f57b4f', 'circle-opacity': 0.43, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2, 'circle-stroke-opacity': 0.9 }, layout: { visibility: 'none' } });
        map.addLayer({ id: 'survey-current-halo', type: 'circle', source: 'survey-current', paint: { 'circle-radius': 15, 'circle-color': '#316be8', 'circle-opacity': 0.18 } });
        map.addLayer({ id: 'survey-current-point', type: 'circle', source: 'survey-current', paint: { 'circle-radius': 6, 'circle-color': '#205ce5', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2.5 } });
        map.addLayer({ id: 'survey-voxels-layer', type: 'fill-extrusion', source: 'survey-voxels', paint: { 'fill-extrusion-color': ['get', 'color'], 'fill-extrusion-base': ['get', 'base'], 'fill-extrusion-height': ['get', 'height'], 'fill-extrusion-opacity': 0.85 }, layout: { visibility: 'none' } });
        map.addLayer({ id: 'trace-particles-layer', type: 'circle', source: 'trace-particles', paint: { 'circle-radius': 5, 'circle-color': ['get', 'color'], 'circle-stroke-color': '#fff', 'circle-stroke-width': 0.4, 'circle-opacity': 0.78 }, layout: { visibility: 'none' } });
        map.addLayer({ id: 'selection-fill', type: 'fill', source: 'selected-polygon', paint: { 'fill-color': '#356df3', 'fill-opacity': 0.18, 'fill-outline-color': '#356df3' } });
        map.addLayer({ id: 'selection-outline', type: 'line', source: 'selected-polygon', paint: { 'line-color': '#315df2', 'line-width': 3 } });
        map.addLayer({ id: 'selection-corners-layer', type: 'circle', source: 'selected-corners', paint: { 'circle-radius': 6, 'circle-color': '#ffffff', 'circle-stroke-color': '#315df2', 'circle-stroke-width': 3 } });

        map.on('click', (event) => {
          if (selectRef.current.selecting) {
            selectRef.current.onSelectPoint([event.lngLat.lng, event.lngLat.lat]);
            return;
          }
          const features = map.queryRenderedFeatures(event.point, { layers: ['survey-current-point', 'survey-hotspots-layer', 'survey-voxels-layer', 'trace-particles-layer', 'survey-points-layer', 'voxels-3d', 'grid-2d'] });
          if (features.length) inspectRef.current(features[0].properties, event.lngLat);
        });
        map.on('mouseenter', 'grid-2d', () => { map.getCanvas().style.cursor = 'crosshair'; });
        map.on('mouseleave', 'grid-2d', () => { map.getCanvas().style.cursor = ''; });
        setReady(true);
        setMapError(false);
        onMapReady?.(map);
      });
    } catch {
      setMapError(true);
    }
    return () => {
      markersRef.current.forEach((marker) => marker.remove());
      markersRef.current = [];
      map?.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map?.getSource('scenario-grid')) return;
    map.getSource('scenario-grid').setData(mode === 'forecast' ? gridGeoJson(cells, metric) : emptyCollection);
    map.getSource('scenario-columns').setData(mode === 'forecast' ? gridGeoJson(cells, metric, 0.08) : emptyCollection);
    if (mode === 'forecast') {
      const raster = scenarioRaster(cells, metric);
      if (raster) map.getSource('scenario-raster').updateImage({ url: raster, coordinates: rasterCorners });
    }
  }, [cells, metric, mode, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map?.getLayer('grid-2d')) return;
    map.setLayoutProperty('scenario-surface', 'visibility', mode === 'forecast' && dimension === '2D' ? 'visible' : 'none');
    map.setLayoutProperty('grid-2d', 'visibility', mode === 'forecast' && dimension === '2D' ? 'visible' : 'none');
    map.setLayoutProperty('voxels-3d', 'visibility', mode === 'forecast' && dimension === '3D' ? 'visible' : 'none');
    map.setLayoutProperty('plume-axis-layer', 'visibility', mode === 'forecast' ? 'visible' : 'none');
    map.setLayoutProperty('survey-points-layer', 'visibility', mode === 'survey' && surveyView === 'sensors' ? 'visible' : 'none');
    map.setLayoutProperty('survey-line-layer', 'visibility', mode === 'survey' && surveyView === 'sensors' ? 'visible' : 'none');
    map.setLayoutProperty('survey-hotspots-layer', 'visibility', mode === 'survey' && surveyView === 'sensors' ? 'visible' : 'none');
    map.setLayoutProperty('survey-current-halo', 'visibility', mode === 'survey' && surveyView === 'sensors' ? 'visible' : 'none');
    map.setLayoutProperty('survey-current-point', 'visibility', mode === 'survey' && surveyView === 'sensors' ? 'visible' : 'none');
    map.setLayoutProperty('survey-voxels-layer', 'visibility', mode === 'survey' && dimension === '3D' ? 'visible' : 'none');
    map.setLayoutProperty('trace-particles-layer', 'visibility', mode === 'survey' && surveyView === 'particles' && dimension === '2D' ? 'visible' : 'none');
    const viewKey = mode === 'survey' ? `survey-${surveyView}` : mode;
    if (lastViewRef.current !== viewKey) {
      map.easeTo({ ...(mode === 'survey' ? SURVEY_VIEW : DELHI_VIEW), pitch: dimension === '3D' ? 52 : 0, duration: 750 });
      lastViewRef.current = viewKey;
    } else if (Math.abs(map.getPitch() - (dimension === '3D' ? 52 : 0)) > 1) {
      map.easeTo({ pitch: dimension === '3D' ? 52 : 0, duration: 550 });
    }
  }, [dimension, mode, surveyView, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map?.getSource('survey-points')) return;
    const active = surveySamples.slice(0, Math.max(1, Math.round(surveySamples.length * surveyProgress)));
    map.getSource('survey-points').setData({ type: 'FeatureCollection', features: active.filter((_, index) => index % 12 === 0 || index === active.length - 1).map((sample) => ({
      type: 'Feature', geometry: { type: 'Point', coordinates: [sample.lng, sample.lat] },
      properties: { ...sample, color: sample.color || '#f79351', provenance: 'simulated_drone_replay' },
    })) });
    map.getSource('survey-line').setData(lineFeature(active.map((sample) => [sample.lng, sample.lat])));
    const cursor = active.at(-1);
    map.getSource('survey-current').setData(cursor ? { type: 'FeatureCollection', features: [{
      type: 'Feature', geometry: { type: 'Point', coordinates: [cursor.lng, cursor.lat] },
      properties: { ...cursor, provenance: 'simulated_drone_replay' },
    }] } : emptyCollection);
    map.getSource('survey-hotspots').setData({ type: 'FeatureCollection', features: surveyHotspots.filter((point) => point.elapsed_s <= surveyProgress * surveyDuration).map((point) => ({
      type: 'Feature', geometry: { type: 'Point', coordinates: [point.lng, point.lat] },
      properties: { ...point, pm25: point.pm25, referencePm25: point.ref_pm25, altitude: point.alt_m, windSource: point.wind_source, plumeEncounter: true, color: '#f57b4f' },
    })) });
    const frame = playbackFrames[Math.round(surveyProgress * (playbackFrames.length - 1))];
    const trace = traceData(frame, voxelMeta?.origin);
    map.getSource('trace-particles').setData(trace.particles);
    map.getSource('survey-voxels').setData(surveyView === 'particles' ? trace.voxels : surveyVoxelData(active));
  }, [surveySamples, surveyProgress, surveyDuration, surveyHotspots, surveyView, playbackFrames, voxelMeta, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map?.getSource('selected-polygon')) return;
    map.getSource('selected-polygon').setData(polygonFeature(selection));
    map.getSource('selected-corners').setData({ type: 'FeatureCollection', features: selection.map((point, index) => ({
      type: 'Feature', properties: { index }, geometry: { type: 'Point', coordinates: point },
    })) });
    map.getCanvas().style.cursor = selecting ? 'crosshair' : '';
  }, [selection, selecting, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready) return;
    markersRef.current.forEach((marker) => marker.remove());
    const visibleSources = mode === 'forecast' ? SOURCES : mode === 'survey' && surveyView === 'particles' && voxelMeta?.source ? [{
      id: 'demo_stack', name: 'H5 demo stack · separate five-minute simulation', type: 'Known simulated stack',
      lng: voxelMeta.source.longitude_deg, lat: voxelMeta.source.latitude_deg, color: '#ff9648', confidence: 'H5 configured source',
    }] : [];
    markersRef.current = visibleSources.map((source) => {
      const markerElement = document.createElement('button');
      markerElement.className = 'source-marker';
      markerElement.type = 'button';
      markerElement.style.setProperty('--marker-color', source.color);
      markerElement.setAttribute('aria-label', source.name);
      markerElement.title = `${source.name} · source hypothesis`;
      markerElement.innerHTML = '<span></span>';
      markerElement.addEventListener('click', (event) => {
        event.stopPropagation();
        inspectRef.current({ name: source.name, type: source.type, provenance: source.confidence, source: true }, { lng: source.lng, lat: source.lat });
      });
      return new maplibregl.Marker({ element: markerElement }).setLngLat([source.lng, source.lat]).addTo(map);
    });
    return () => { markersRef.current.forEach((marker) => marker.remove()); markersRef.current = []; };
  }, [mode, surveyView, voxelMeta, ready]);

  return (
    <div className="map-stage" aria-label="Delhi NCR interactive pollution map">
      {mapError && <div className="map-fallback" role="status"><div className="fallback-grid" /><strong>Map tiles are unavailable</strong><span>The forecast controls, charts and exports remain available.</span></div>}
      <div className="map-canvas" ref={elementRef} />
      {!ready && !mapError && <div className="map-loading" role="status">Loading Delhi NCR map…</div>}
      <div className="map-crosshair-label">{selecting ? `${selection.length}/4 corners · click map to add` : mode === 'survey' ? surveyView === 'particles' ? 'H5 TRACE · 20 × 20 × 10 M VOXELS' : 'SIMULATED DRONE TRACK · 20 M VOXELS' : dimension === '3D' ? 'RELATIVE COLUMN HEIGHT · EXAGGERATED' : `NCR · ${Math.round((GRID.east - GRID.west) * 111 / GRID.columns * 10) / 10} KM GRID`}</div>
    </div>
  );
}
