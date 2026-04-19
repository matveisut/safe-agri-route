import { CircleMarker } from 'react-leaflet';
import { useMissionStore } from '../store/useMissionStore';
import { riskHeatmapColor } from '../utils/riskHeatmapColor';

/**
 * Renders the risk heat-map as a layer of small CircleMarkers inside
 * the MapContainer.  Only shown when `showRiskOverlay` is true in the store.
 */
export default function RiskOverlay() {
  const { riskGridPreview, showRiskOverlay } = useMissionStore();

  if (!showRiskOverlay || riskGridPreview.length === 0) return null;

  return (
    <>
      {riskGridPreview.map((pt, idx) => (
        <CircleMarker
          key={`risk-${idx}`}
          center={[pt.lat, pt.lng]}
          radius={9}
          pathOptions={{
            fillColor: riskHeatmapColor(pt.risk),
            fillOpacity: 0.72,
            stroke: true,
            color: 'rgba(15, 23, 42, 0.35)',
            weight: 0.5,
          }}
        />
      ))}
    </>
  );
}
