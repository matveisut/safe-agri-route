import { useEffect, useState } from 'react';
import { MapContainer, TileLayer, Polygon, Polyline, CircleMarker } from 'react-leaflet';
import api from '../../services/api';
import { useMissionStore, FieldType, RiskZoneType } from '../../store/useMissionStore';

export default function MapArea() {
  const [fields, setFields] = useState<FieldType[]>([]);
  const [riskZones, setRiskZones] = useState<RiskZoneType[]>([]);
  const [loading, setLoading] = useState(true);

  const { plannedRoutes, telemetry } = useMissionStore();

  useEffect(() => {
    async function loadData() {
      try {
        const [fieldsRes, zonesRes] = await Promise.all([
          api.get('/mission/fields'),
          api.get('/mission/risk-zones')
        ]);
        setFields(fieldsRes.data.fields);
        setRiskZones(zonesRes.data.risk_zones);
        setLoading(false);
      } catch (err) {
        console.error("Failed to load map data", err);
        setLoading(false);
      }
    }
    loadData();
  }, []);

  if (loading) {
    return <div className="flex bg-slate-800 items-center justify-center h-full w-full text-white">Loading Map Data...</div>;
  }

  // Pre-calculate the center based on the first field if available
  let center: [number, number] = [45.0428, 41.9734]; // fallback (Stavropol Krai)
  if (fields.length > 0 && fields[0].geojson) {
      const coords = JSON.parse(fields[0].geojson).coordinates[0][0]; // Polygon first coord
      center = [coords[1], coords[0]]; // Leaflet uses [lat, lng]
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

        {/* Render Fields */}
        {fields.map(f => {
          const rawGeo = JSON.parse(f.geojson);
          // GeoJSON coordinates are [lng, lat], react-leaflet Polygon expects [lat, lng]
          const positions = rawGeo.coordinates[0].map((c: number[]) => [c[1], c[0]]);
          return (
            <Polygon 
              key={`field-${f.id}`} 
              positions={positions} 
              pathOptions={{ fillColor: 'green', color: 'darkgreen', weight: 2, fillOpacity: 0.3 }}
            >
            </Polygon>
          );
        })}

        {/* Render Risk Zones */}
        {riskZones.map(rz => {
          const rawGeo = JSON.parse(rz.geojson);
          const positions = rawGeo.coordinates[0].map((c: number[]) => [c[1], c[0]]);
          // Spoofing -> Yellow, Jamming -> Red
          const colorName = rz.type.toLowerCase().includes('spoof') ? 'orange' : 'red';
          
          return (
            <Polygon 
              key={`rz-${rz.id}`} 
              positions={positions} 
              pathOptions={{ fillColor: colorName, color: colorName, weight: 1, fillOpacity: 0.4 }}
            />
          );
        })}

        {/* Render Planned Routes */}
        {plannedRoutes.map((routeData, idx) => {
          const positions = routeData.route.map(pt => [pt.lat, pt.lng] as [number, number]);
          const DRONE_COLORS = ["#3b82f6", "#8b5cf6", "#eab308", "#14b8a6"];
          const colorName = DRONE_COLORS[routeData.drone_id % DRONE_COLORS.length];
          
          return (
            <Polyline 
              key={`route-${routeData.drone_id}-${idx}`}
              positions={positions}
              pathOptions={{ color: colorName, weight: 3, dashArray: '8, 8', opacity: 0.8 }}
            />
          )
        })}

        {/* Render Telemetry Live Droplet Markers */}
        {Object.entries(telemetry).map(([droneId_str, coords]) => {
          const d_id = parseInt(droneId_str);
          const DRONE_COLORS = ["#3b82f6", "#8b5cf6", "#eab308", "#14b8a6"];
          const colorName = DRONE_COLORS[d_id % DRONE_COLORS.length];
          
          return (
            <CircleMarker 
              key={`drone-pos-${droneId_str}`}
              center={[coords.lat, coords.lng]} 
              radius={7}
              pathOptions={{ fillColor: colorName, fillOpacity: 1, color: 'white', weight: 3 }}
            />
          );
        })}

      </MapContainer>

      {/* Basic Overlays */}
      <div className="absolute top-4 right-4 z-[400] bg-slate-900/80 p-4 rounded-xl text-white shadow-lg backdrop-blur-sm border border-slate-600">
        <h3 className="font-bold text-lg mb-2">Map Legend</h3>
        <ul className="text-sm space-y-2">
          <li className="flex items-center"><span className="w-4 h-4 bg-green-500 opacity-60 inline-block mr-2 rounded"></span> Safe Field</li>
          <li className="flex items-center"><span className="w-4 h-4 bg-red-500 opacity-60 inline-block mr-2 rounded"></span> Jamming Zone</li>
          <li className="flex items-center"><span className="w-4 h-4 bg-orange-500 opacity-60 inline-block mr-2 rounded"></span> Spoofing Zone</li>
          <li className="flex items-center"><span className="w-4 h-1 bg-blue-500 inline-block mr-2 rounded"></span> Planned Path</li>
          <li className="flex items-center"><span className="w-3 h-3 bg-cyan-400 inline-block mr-2 rounded-full border-2 border-white"></span> Active Drone</li>
        </ul>
      </div>
    </div>
  );
}
