import { CircleMarker } from 'react-leaflet';
import { useMissionStore } from '../store/useMissionStore';

/**
 * Interpolates a risk value [0, 1] to an RGB colour string.
 *   0.0 → green  (0, 255, 0)
 *   0.5 → yellow (255, 255, 0)
 *   1.0 → red    (255, 0, 0)
 */
function riskToColor(risk: number): string {
  if (risk < 0.5) {
    const r = Math.round(risk * 2 * 255);
    return `rgb(${r}, 255, 0)`;
  } else {
    const g = Math.round((1 - (risk - 0.5) * 2) * 255);
    return `rgb(255, ${g}, 0)`;
  }
}

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
          radius={8}
          pathOptions={{
            fillColor: riskToColor(pt.risk),
            fillOpacity: 0.4,
            stroke: false,
            // stroke must be explicitly false — CircleMarker defaults to true
            color: 'transparent',
            weight: 0,
          }}
        />
      ))}
    </>
  );
}
