import { useEffect, useRef } from 'react';
import { useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet-draw';
import 'leaflet-draw/dist/leaflet.draw.css';

export type DrawMode = 'field' | 'risk-zone' | null;

interface Props {
  mode: DrawMode;
  onPolygonComplete: (geojsonGeometry: string) => void;
}

/**
 * Thin wrapper around the leaflet-draw polygon handler.
 *
 * Must be rendered *inside* a <MapContainer> so that useMap() resolves.
 * Activates a new draw session whenever `mode` becomes non-null, and
 * deactivates when it becomes null or on cleanup.
 */
export default function DrawControl({ mode, onPolygonComplete }: Props) {
  const map = useMap();
  // Keep a stable reference so cleanup always cancels the right handler.
  const handlerRef = useRef<any>(null);
  const drawnItemsRef = useRef<L.FeatureGroup>(new L.FeatureGroup());

  useEffect(() => {
    const drawnItems = drawnItemsRef.current;
    map.addLayer(drawnItems);

    return () => {
      map.removeLayer(drawnItems);
    };
  }, [map]);

  useEffect(() => {
    // Cancel any previous handler that might still be active.
    if (handlerRef.current) {
      try { handlerRef.current.disable(); } catch { /* already disabled */ }
      handlerRef.current = null;
    }

    if (!mode) return;

    const color = mode === 'field' ? '#10b981' : '#ef4444';

    // L.Draw.Polygon is added to the L namespace by the 'leaflet-draw' side-effect import.
    const LDraw = (L as any).Draw;
    const handler = new LDraw.Polygon(map, {
      shapeOptions: {
        color,
        fillColor: color,
        fillOpacity: 0.25,
        weight: 2,
      },
      showLength: false,
      metric: true,
    });

    handler.enable();
    handlerRef.current = handler;

    const onCreated = (e: any) => {
      drawnItemsRef.current.clearLayers();
      drawnItemsRef.current.addLayer(e.layer);
      // Emit the raw GeoJSON geometry string to the parent.
      const geometry = JSON.stringify(e.layer.toGeoJSON().geometry);
      onPolygonComplete(geometry);
    };

    map.on((L as any).Draw.Event.CREATED, onCreated);

    return () => {
      try { handler.disable(); } catch { /* noop */ }
      map.off((L as any).Draw.Event.CREATED, onCreated);
      handlerRef.current = null;
    };
  }, [map, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  return null;
}
