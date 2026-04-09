import React, { useState, useEffect } from 'react';
import api from '../../services/api';
import { useMissionStore, FieldType } from '../../store/useMissionStore';
import { useTelemetry } from '../../hooks/useTelemetry';

export default function MissionPanel() {
  const [fields, setFields] = useState<FieldType[]>([]);
  const [isPlanning, setIsPlanning] = useState(false);
  
  // Hardcoded known drones for MVP. Ideally fetched from GET /drones backend endpoint.
  const ALL_DRONES = [
    { id: 1, name: "AgriFly-1", cap: "5 Ah" },
    { id: 2, name: "AgriFly-2", cap: "7.5 Ah" },
    { id: 3, name: "AgriFly-3", cap: "10 Ah" }
  ];

  const { 
    selectedFieldId, 
    setSelectedField, 
    selectedDroneIds, 
    toggleDroneSelection, 
    setPlannedRoutes,
    plannedRoutes
  } = useMissionStore();

  const { startSimulation, stopSimulation, isConnected } = useTelemetry();

  // Load fields for dropdown
  useEffect(() => {
    async function fetchFields() {
      try {
        const res = await api.get('/mission/fields');
        setFields(res.data.fields);
        if (res.data.fields.length > 0 && !selectedFieldId) {
          setSelectedField(res.data.fields[0].id);
        }
      } catch (e) {
        console.error(e);
      }
    }
    fetchFields();
  }, []);

  const handlePlanRoute = async () => {
    if (!selectedFieldId || selectedDroneIds.length === 0) {
      alert("Please select a field and at least one drone");
      return;
    }
    
    setIsPlanning(true);
    try {
      const payload = {
        field_id: selectedFieldId,
        drone_ids: selectedDroneIds
      };
      
      const res = await api.post('/mission/plan', payload);
      setPlannedRoutes(res.data.routes);
      console.log("Successfully planned routes:", res.data.routes);
    } catch (e) {
      console.error("Failed to plan route", e);
      alert("Failed to plan route. Check backend console.");
    } finally {
      setIsPlanning(false);
    }
  };

  return (
    <div className="flex-1 space-y-6 flex flex-col">
      <div className="bg-slate-900/80 p-5 rounded-2xl border border-slate-700/50 shadow-lg">
        <h2 className="font-bold text-xs tracking-widest text-slate-400 mb-4 uppercase">1. Target Field</h2>
        <select 
          className="w-full bg-slate-800 text-slate-100 border border-slate-700 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 transition-all font-medium text-sm"
          value={selectedFieldId || ""}
          onChange={(e) => setSelectedField(Number(e.target.value))}
        >
          {fields.map(f => (
            <option key={f.id} value={f.id}>{f.name}</option>
          ))}
        </select>
      </div>
      
      <div className="bg-slate-900/80 p-5 rounded-2xl border border-slate-700/50 shadow-lg">
        <h2 className="font-bold text-xs tracking-widest text-slate-400 mb-4 uppercase">2. Assign Drones</h2>
        <div className="space-y-3">
          {ALL_DRONES.map(d => (
            <label key={d.id} className="flex items-center space-x-3 cursor-pointer group">
              <div className="relative flex items-center justify-center">
                <input 
                  type="checkbox" 
                  className="peer sr-only"
                  checked={selectedDroneIds.includes(d.id)}
                  onChange={() => toggleDroneSelection(d.id)}
                />
                <div className="w-5 h-5 bg-slate-800 border-2 border-slate-600 rounded peer-checked:bg-emerald-500 peer-checked:border-emerald-500 transition-colors"></div>
                <svg className="absolute w-3 h-3 text-white opacity-0 peer-checked:opacity-100 pointer-events-none" viewBox="0 0 14 10" fill="none">
                  <path d="M1 5L5 9L13 1" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </div>
              <div className="flexflex-col">
                <span className="text-sm font-semibold text-slate-200 block group-hover:text-emerald-400 transition-colors">{d.name}</span>
                <span className="text-xs text-slate-500 block">Battery: {d.cap}</span>
              </div>
            </label>
          ))}
        </div>
      </div>

      <div className="bg-slate-900/80 p-5 rounded-2xl border border-slate-700/50 shadow-lg">
        <h2 className="font-bold text-xs tracking-widest text-slate-400 mb-4 uppercase">3. Execution</h2>
        <button 
          onClick={handlePlanRoute}
          disabled={isPlanning}
          className="w-full mb-3 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-bold py-3 px-4 rounded-xl transition-all shadow-lg shadow-indigo-600/20 active:scale-95"
        >
          {isPlanning ? "Computing CVRP..." : "1. Generate Neural Route"}
        </button>
        
        {!isConnected ? (
          <button 
            onClick={startSimulation}
            disabled={plannedRoutes.length === 0}
            className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold py-3 px-4 rounded-xl transition-all shadow-lg shadow-emerald-500/20 active:scale-95"
          >
            2. Start Telemetry Sim
          </button>
        ) : (
          <button 
            onClick={stopSimulation}
            className="w-full bg-red-600 hover:bg-red-500 text-white font-bold py-3 px-4 rounded-xl transition-all shadow-lg shadow-red-500/20 active:scale-95 animate-pulse"
          >
            Stop Simulation
          </button>
        )}
      </div>
    </div>
  );
}
