import { useEffect, useState } from 'react';
import { MapContainer, TileLayer, Polygon, Polyline, CircleMarker } from 'react-leaflet';
import api from '../../services/api';
import { useMissionStore, FieldType, RiskZoneType } from '../../store/useMissionStore';
import DrawControl, { DrawMode } from '../../components/DrawControl';
import RiskOverlay from '../../components/RiskOverlay';

// ---------------------------------------------------------------------------
// Risk-zone configuration modal (shown after polygon is drawn)
// ---------------------------------------------------------------------------

interface RebZoneFormProps {
  onConfirm: (zoneType: string, severity: number) => void;
  onCancel: () => void;
  isSaving: boolean;
}

function RebZoneForm({ onConfirm, onCancel, isSaving }: RebZoneFormProps) {
  const [zoneType, setZoneType] = useState<'jammer' | 'restricted'>('jammer');
  const [severity, setSeverity] = useState(0.5);

  return (
    <div className="absolute inset-0 z-[1000] flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-slate-800 border border-slate-600 rounded-2xl p-6 w-80 shadow-2xl text-white">
        <h3 className="font-bold text-lg mb-4 text-red-400">Configure REB Zone</h3>

        <label className="block text-sm text-slate-400 mb-1">Zone Type</label>
        <select
          value={zoneType}
          onChange={(e) => setZoneType(e.target.value as 'jammer' | 'restricted')}
          className="w-full bg-slate-700 border border-slate-600 rounded-lg p-2 mb-4 text-sm"
        >
          <option value="jammer">Jammer (GPS / RF Blocker)</option>
          <option value="restricted">Restricted Area</option>
        </select>

        <label className="block text-sm text-slate-400 mb-1">
          Severity: <span className="font-bold text-red-400">{severity.toFixed(1)}</span>
        </label>
        <input
          type="range"
          min={0.1}
          max={1.0}
          step={0.1}
          value={severity}
          onChange={(e) => setSeverity(parseFloat(e.target.value))}
          className="w-full accent-red-500 mb-6"
        />

        <div className="flex gap-3">
          <button
            onClick={onCancel}
            className="flex-1 bg-slate-700 hover:bg-slate-600 rounded-xl py-2 text-sm transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => onConfirm(zoneType, severity)}
            disabled={isSaving}
            className="flex-1 bg-red-600 hover:bg-red-500 disabled:opacity-50 rounded-xl py-2 text-sm font-semibold transition-colors"
          >
            {isSaving ? 'Saving…' : 'Save Zone'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Field name modal (shown after a field polygon is drawn)
// ---------------------------------------------------------------------------

interface FieldNameFormProps {
  onConfirm: (name: string) => void;
  onCancel: () => void;
  isSaving: boolean;
}

function FieldNameForm({ onConfirm, onCancel, isSaving }: FieldNameFormProps) {
  const [name, setName] = useState('');

  return (
    <div className="absolute inset-0 z-[1000] flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-slate-800 border border-slate-600 rounded-2xl p-6 w-80 shadow-2xl text-white">
        <h3 className="font-bold text-lg mb-4 text-emerald-400">Name this Field</h3>

        <input
          type="text"
          placeholder="e.g. North Field Block A"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full bg-slate-700 border border-slate-600 rounded-lg p-2 mb-6 text-sm placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-emerald-500"
          autoFocus
        />

        <div className="flex gap-3">
          <button
            onClick={onCancel}
            className="flex-1 bg-slate-700 hover:bg-slate-600 rounded-xl py-2 text-sm transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => onConfirm(name.trim() || 'Unnamed Field')}
            disabled={isSaving}
            className="flex-1 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-xl py-2 text-sm font-semibold transition-colors"
          >
            {isSaving ? 'Saving…' : 'Save Field'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main MapArea component
// ---------------------------------------------------------------------------

const DRONE_COLORS = ['#3b82f6', '#8b5cf6', '#eab308', '#14b8a6'];

export default function MapArea() {
  const [fields, setFields] = useState<FieldType[]>([]);
  const [riskZones, setRiskZones] = useState<RiskZoneType[]>([]);
  const [loading, setLoading] = useState(true);

  // Drawing state
  const [drawMode, setDrawMode] = useState<DrawMode>(null);
  const [pendingGeojson, setPendingGeojson] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const {
    plannedRoutes,
    telemetry,
    showRiskOverlay,
    setShowRiskOverlay,
    missionIsActive,
    selectedFieldId,
    selectedDroneIds,
  } = useMissionStore();

  // Alias used for replanning payload (same reference, separate name for clarity)
  const currentRoutes = plannedRoutes;

  // -------------------------------------------------------------------------
  // Load initial map data
  // -------------------------------------------------------------------------
  const loadMapData = async () => {
    try {
      const [fieldsRes, zonesRes] = await Promise.all([
        api.get('/mission/fields'),
        api.get('/mission/risk-zones'),
      ]);
      setFields(fieldsRes.data.fields);
      setRiskZones(zonesRes.data.risk_zones);
    } catch (err) {
      console.error('Failed to load map data', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadMapData(); }, []);

  // -------------------------------------------------------------------------
  // Draw callbacks
  // -------------------------------------------------------------------------
  const handlePolygonComplete = (geojson: string) => {
    setPendingGeojson(geojson);
    setDrawMode(null); // deactivate draw handler; keep drawMode type in state via pendingGeojson path
  };

  const handleSaveField = async (name: string) => {
    if (!pendingGeojson) return;
    setIsSaving(true);
    try {
      await api.post('/mission/fields', { name, geojson: pendingGeojson });
      setPendingGeojson(null);
      await loadMapData(); // refresh field layer
    } catch (err) {
      console.error('Failed to save field', err);
      alert('Failed to save field. Check backend logs.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleSaveRebZone = async (zoneType: string, severity: number) => {
    if (!pendingGeojson) return;
    setIsSaving(true);
    try {
      // Persist the new zone regardless of mission state
      await api.post('/mission/risk-zones', {
        zone_type: zoneType,
        severity_weight: severity,
        geojson: pendingGeojson,
      });

      // If a mission is active, trigger dynamic replanning
      if (missionIsActive && selectedFieldId && currentRoutes.length > 0) {
        await api.post('/mission/1/risk-zones', {
          field_id: selectedFieldId,
          drone_ids: selectedDroneIds,
          new_zone: {
            geometry: JSON.parse(pendingGeojson),
            severity,
            zone_type: zoneType,
          },
          current_routes: currentRoutes,
          visited_counts: {},
        });
      }

      setPendingGeojson(null);
      await loadMapData(); // refresh risk zone layer
    } catch (err) {
      console.error('Failed to save REB zone', err);
      alert('Failed to save REB zone. Check backend logs.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancelDraw = () => {
    setPendingGeojson(null);
    setDrawMode(null);
  };

  // -------------------------------------------------------------------------
  // Active draw mode tracking (field vs risk-zone) survives polygon completion
  // -------------------------------------------------------------------------
  const [lastDrawMode, setLastDrawMode] = useState<DrawMode>(null);

  const activateDraw = (mode: DrawMode) => {
    setLastDrawMode(mode);
    setDrawMode(mode);
    setPendingGeojson(null);
  };

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  if (loading) {
    return (
      <div className="flex bg-slate-800 items-center justify-center h-full w-full text-white">
        Loading Map Data...
      </div>
    );
  }

  let center: [number, number] = [45.0428, 41.9734]; // fallback: Stavropol Krai
  if (fields.length > 0 && fields[0].geojson) {
    const coords = JSON.parse(fields[0].geojson).coordinates[0][0];
    center = [coords[1], coords[0]];
  }

  return (
    <div className="relative w-full h-full border-4 border-slate-700 rounded-xl overflow-hidden shadow-2xl">
      <MapContainer
        center={center}
        zoom={14}
        scrollWheelZoom={true}
        className="w-full h-full"
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        {/* Fields */}
        {fields.map((f) => {
          const rawGeo = JSON.parse(f.geojson);
          const positions = rawGeo.coordinates[0].map((c: number[]) => [c[1], c[0]]);
          return (
            <Polygon
              key={`field-${f.id}`}
              positions={positions}
              pathOptions={{ fillColor: 'green', color: 'darkgreen', weight: 2, fillOpacity: 0.3 }}
            />
          );
        })}

        {/* Risk Zones */}
        {riskZones.map((rz) => {
          const rawGeo = JSON.parse(rz.geojson);
          const positions = rawGeo.coordinates[0].map((c: number[]) => [c[1], c[0]]);
          const colorName = rz.type.toLowerCase().includes('spoof') ? 'orange' : 'red';
          return (
            <Polygon
              key={`rz-${rz.id}`}
              positions={positions}
              pathOptions={{ fillColor: colorName, color: colorName, weight: 1, fillOpacity: 0.4 }}
            />
          );
        })}

        {/* Planned Routes */}
        {plannedRoutes.map((routeData, idx) => {
          const positions = routeData.route.map((pt) => [pt.lat, pt.lng] as [number, number]);
          const color = DRONE_COLORS[routeData.drone_id % DRONE_COLORS.length];
          return (
            <Polyline
              key={`route-${routeData.drone_id}-${idx}`}
              positions={positions}
              pathOptions={{ color, weight: 3, dashArray: '8, 8', opacity: 0.8 }}
            />
          );
        })}

        {/* Live Drone Markers */}
        {Object.entries(telemetry).map(([droneId_str, coords]) => {
          const d_id = parseInt(droneId_str);
          const color = DRONE_COLORS[d_id % DRONE_COLORS.length];
          return (
            <CircleMarker
              key={`drone-pos-${droneId_str}`}
              center={[coords.lat, coords.lng]}
              radius={7}
              pathOptions={{ fillColor: color, fillOpacity: 1, color: 'white', weight: 3 }}
            />
          );
        })}

        {/* Risk Heat-map overlay */}
        <RiskOverlay />

        {/* Draw polygon handler (activates when drawMode is non-null) */}
        <DrawControl mode={drawMode} onPolygonComplete={handlePolygonComplete} />
      </MapContainer>

      {/* ------------------------------------------------------------------ */}
      {/* Draw toolbar — top-left overlay                                     */}
      {/* ------------------------------------------------------------------ */}
      <div className="absolute top-4 left-4 z-[400] flex flex-col gap-2">
        <button
          onClick={() => activateDraw(drawMode === 'field' ? null : 'field')}
          className={`px-3 py-2 rounded-xl text-xs font-bold shadow-lg transition-all border ${
            drawMode === 'field'
              ? 'bg-emerald-500 text-white border-emerald-400 ring-2 ring-emerald-300'
              : 'bg-slate-800/90 text-emerald-400 border-slate-600 hover:border-emerald-500 hover:bg-slate-700/90'
          }`}
        >
          ✏️ Draw Field
        </button>
        <button
          onClick={() => activateDraw(drawMode === 'risk-zone' ? null : 'risk-zone')}
          className={`px-3 py-2 rounded-xl text-xs font-bold shadow-lg transition-all border ${
            drawMode === 'risk-zone'
              ? 'bg-red-500 text-white border-red-400 ring-2 ring-red-300'
              : 'bg-slate-800/90 text-red-400 border-slate-600 hover:border-red-500 hover:bg-slate-700/90'
          }`}
        >
          ⚡ Draw REB Zone
        </button>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Legend + risk overlay toggle — top-right                           */}
      {/* ------------------------------------------------------------------ */}
      <div className="absolute top-4 right-4 z-[400] bg-slate-900/80 p-4 rounded-xl text-white shadow-lg backdrop-blur-sm border border-slate-600">
        <h3 className="font-bold text-lg mb-2">Map Legend</h3>
        <ul className="text-sm space-y-2">
          <li className="flex items-center">
            <span className="w-4 h-4 bg-green-500 opacity-60 inline-block mr-2 rounded" /> Safe Field
          </li>
          <li className="flex items-center">
            <span className="w-4 h-4 bg-red-500 opacity-60 inline-block mr-2 rounded" /> Jamming Zone
          </li>
          <li className="flex items-center">
            <span className="w-4 h-4 bg-orange-500 opacity-60 inline-block mr-2 rounded" /> Spoofing Zone
          </li>
          <li className="flex items-center">
            <span className="w-4 h-1 bg-blue-500 inline-block mr-2 rounded" /> Planned Path
          </li>
          <li className="flex items-center">
            <span className="w-3 h-3 bg-cyan-400 inline-block mr-2 rounded-full border-2 border-white" /> Active Drone
          </li>
        </ul>

        <label className="flex items-center gap-2 mt-3 pt-3 border-t border-slate-700 cursor-pointer text-sm select-none">
          <input
            type="checkbox"
            checked={showRiskOverlay}
            onChange={(e) => setShowRiskOverlay(e.target.checked)}
            className="accent-yellow-400 w-4 h-4"
          />
          <span className="text-yellow-300 font-semibold">Risk Heat-map</span>
        </label>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Modal: configure field name                                         */}
      {/* ------------------------------------------------------------------ */}
      {pendingGeojson && lastDrawMode === 'field' && (
        <FieldNameForm
          onConfirm={handleSaveField}
          onCancel={handleCancelDraw}
          isSaving={isSaving}
        />
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Modal: configure REB zone settings                                 */}
      {/* ------------------------------------------------------------------ */}
      {pendingGeojson && lastDrawMode === 'risk-zone' && (
        <RebZoneForm
          onConfirm={handleSaveRebZone}
          onCancel={handleCancelDraw}
          isSaving={isSaving}
        />
      )}
    </div>
  );
}
