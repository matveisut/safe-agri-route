import { useState, useEffect, useRef } from 'react';
import api from '../../services/api';
import { useMissionStore } from '../../store/useMissionStore';
import { useTelemetry } from '../../hooks/useTelemetry';
import { useLiveFusionSocket } from '../../hooks/useLiveFusionSocket';

// ---------------------------------------------------------------------------
// IRM progress bar
// ---------------------------------------------------------------------------

function IRMBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    value >= 0.7 ? 'bg-emerald-500' : value >= 0.4 ? 'bg-yellow-400' : 'bg-red-500';
  const textColor =
    value >= 0.7 ? 'text-emerald-400' : value >= 0.4 ? 'text-yellow-300' : 'text-red-400';

  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-slate-400">Reliability Index (IRM)</span>
        <span className={`font-bold ${textColor}`}>{(value).toFixed(2)}</span>
      </div>
      <div className="h-3 bg-slate-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Coverage progress bar
// ---------------------------------------------------------------------------

function ThreatFusionBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    value < 0.35 ? 'bg-emerald-500' : value < 0.65 ? 'bg-amber-500' : 'bg-red-500';
  const textColor =
    value < 0.35 ? 'text-emerald-400' : value < 0.65 ? 'text-amber-300' : 'text-red-400';

  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-slate-400">Оценка угрозы (fusion)</span>
        <span className={`font-bold ${textColor}`}>{pct}%</span>
      </div>
      <div className="h-3 bg-slate-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function CoverageBar({ value }: { value: number }) {
  const pct = Math.min(100, Math.round(value));
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-slate-400">Field Coverage</span>
        <span className="font-bold text-cyan-400">{value.toFixed(1)}%</span>
      </div>
      <div className="h-3 bg-slate-700 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full bg-cyan-500 transition-all duration-700"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export default function MissionPanel() {
  const [isPlanning, setIsPlanning] = useState(false);
  const [autoReplanNotice, setAutoReplanNotice] = useState(false);
  const lastReplanEventRef = useRef(0);

  const ALL_DRONES = [
    { id: 1, name: 'AgriFly-1', cap: '5 Ah' },
    { id: 2, name: 'AgriFly-2', cap: '7.5 Ah' },
    { id: 3, name: 'AgriFly-3', cap: '10 Ah' },
  ];

  const {
    fields,
    selectedFieldId,
    setSelectedField,
    selectedDroneIds,
    toggleDroneSelection,
    setPlannedRoutes,
    setMissionStats,
    setRiskGridPreview,
    plannedRoutes,
    missionStats,
    liveFusion,
    setLiveFusion,
    resetLiveFusion,
  } = useMissionStore();

  const { startSimulation, stopSimulation, isConnected } = useTelemetry();
  useLiveFusionSocket();

  useEffect(() => {
    if (fields.length > 0 && !selectedFieldId) {
      setSelectedField(fields[0].id);
    }
  }, [fields]);

  useEffect(() => {
    const ev = liveFusion.lastAutoReplanEvent;
    if (ev > lastReplanEventRef.current && ev > 0) {
      lastReplanEventRef.current = ev;
      setAutoReplanNotice(true);
      const t = window.setTimeout(() => setAutoReplanNotice(false), 6500);
      return () => clearTimeout(t);
    }
    lastReplanEventRef.current = ev;
  }, [liveFusion.lastAutoReplanEvent]);

  const handlePlanRoute = async () => {
    if (!selectedFieldId) {
      alert('Please select a field');
      return;
    }
    if (selectedDroneIds.length === 0) {
      alert('Please select at least one drone');
      return;
    }

    setIsPlanning(true);
    try {
      const res = await api.post('/mission/plan', {
        field_id: selectedFieldId,
        drone_ids: selectedDroneIds,
      });

      const data = res.data;
      setPlannedRoutes(data.routes);

      // Compute total waypoint count
      const waypointCount = (data.routes as any[]).reduce(
        (acc: number, r: any) => acc + r.route.length,
        0,
      );

      setMissionStats({
        irm: data.reliability_index,
        coveragePct: data.estimated_coverage_pct,
        waypointCount,
        droneCount: data.routes.length,
      });

      if (data.risk_grid_preview?.length > 0) {
        setRiskGridPreview(data.risk_grid_preview);
      }
    } catch (e) {
      console.error('Failed to plan route', e);
      alert('Failed to plan route. Check backend console.');
    } finally {
      setIsPlanning(false);
    }
  };

  return (
    <div className="flex-1 space-y-6 flex flex-col">
      {/* Step 1: Field selection */}
      <div className="bg-slate-900/80 p-5 rounded-2xl border border-slate-700/50 shadow-lg">
        <h2 className="font-bold text-xs tracking-widest text-slate-400 mb-4 uppercase">
          1. Target Field
        </h2>
        <select
          className="w-full bg-slate-800 text-slate-100 border border-slate-700 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 transition-all font-medium text-sm"
          value={selectedFieldId || ''}
          onChange={(e) => setSelectedField(Number(e.target.value))}
        >
          {fields.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      </div>

      {/* Step 2: Drone selection */}
      <div className="bg-slate-900/80 p-5 rounded-2xl border border-slate-700/50 shadow-lg">
        <h2 className="font-bold text-xs tracking-widest text-slate-400 mb-4 uppercase">
          2. Assign Drones
        </h2>
        <div className="space-y-3">
          {ALL_DRONES.map((d) => (
            <label key={d.id} className="flex items-center space-x-3 cursor-pointer group">
              <div className="relative flex items-center justify-center">
                <input
                  type="checkbox"
                  className="peer sr-only"
                  checked={selectedDroneIds.includes(d.id)}
                  onChange={() => toggleDroneSelection(d.id)}
                />
                <div className="w-5 h-5 bg-slate-800 border-2 border-slate-600 rounded peer-checked:bg-emerald-500 peer-checked:border-emerald-500 transition-colors" />
                <svg
                  className="absolute w-3 h-3 text-white opacity-0 peer-checked:opacity-100 pointer-events-none"
                  viewBox="0 0 14 10"
                  fill="none"
                >
                  <path
                    d="M1 5L5 9L13 1"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </div>
              <div>
                <span className="text-sm font-semibold text-slate-200 block group-hover:text-emerald-400 transition-colors">
                  {d.name}
                </span>
                <span className="text-xs text-slate-500 block">Battery: {d.cap}</span>
              </div>
            </label>
          ))}
        </div>
      </div>

      {/* Step 3: Execution */}
      <div className="bg-slate-900/80 p-5 rounded-2xl border border-slate-700/50 shadow-lg">
        <h2 className="font-bold text-xs tracking-widest text-slate-400 mb-4 uppercase">
          3. Execution
        </h2>
        <button
          onClick={handlePlanRoute}
          disabled={isPlanning}
          className="w-full mb-3 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-bold py-3 px-4 rounded-xl transition-all shadow-lg shadow-indigo-600/20 active:scale-95"
        >
          {isPlanning ? 'Computing CVRP…' : '1. Generate Neural Route'}
        </button>

        {!isConnected ? (
          <button
            onClick={() => startSimulation()}
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

      {/* ------------------------------------------------------------------ */}
      {/* MAVLink fusion (ТЗ §10) — опционально, без SITL не мешает демо        */}
      {/* ------------------------------------------------------------------ */}
      <div className="bg-slate-900/80 p-5 rounded-2xl border border-slate-700/50 shadow-lg space-y-3">
        <h2 className="font-bold text-xs tracking-widest text-slate-400 uppercase">
          MAVLink · Fusion
        </h2>
        <label className="flex items-start gap-3 cursor-pointer group">
          <input
            type="checkbox"
            className="mt-1 rounded border-slate-600 bg-slate-800 text-emerald-500 focus:ring-emerald-500"
            checked={liveFusion.enabled}
            onChange={(e) => {
              if (e.target.checked) {
                setLiveFusion({
                  enabled: true,
                  droneId: selectedDroneIds[0] ?? 1,
                });
              } else {
                lastReplanEventRef.current = 0;
                resetLiveFusion();
              }
            }}
          />
          <span className="text-sm text-slate-300 leading-snug">
            Подключить поток телеметрии и оценку угрозы (нужен бэкенд и SITL /
            симуляция MAVLink)
          </span>
        </label>
        {liveFusion.enabled && (
          <div className="space-y-2 pl-1">
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500">Дрон</span>
              <select
                className="bg-slate-800 text-slate-100 border border-slate-600 rounded-lg px-2 py-1 text-sm"
                value={liveFusion.droneId ?? 1}
                onChange={(e) =>
                  setLiveFusion({ droneId: Number.parseInt(e.target.value, 10) })
                }
              >
                {[1, 2, 3].map((id) => (
                  <option key={id} value={id}>
                    #{id}
                  </option>
                ))}
              </select>
            </div>
            {liveFusion.fusedThreatLevel != null ? (
              <ThreatFusionBar value={liveFusion.fusedThreatLevel} />
            ) : (
              <p className="text-xs text-slate-500">Fusion: н/д (ожидание кадров…)</p>
            )}
            {autoReplanNotice && (
              <p className="text-xs font-semibold text-amber-400 border border-amber-700/50 rounded-lg px-2 py-1.5 bg-amber-950/40">
                Авто-риск: перепланирование выполнено
              </p>
            )}
          </div>
        )}
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Metrics card — visible after a plan is computed                     */}
      {/* ------------------------------------------------------------------ */}
      {missionStats && (
        <div className="bg-slate-900/80 p-5 rounded-2xl border border-slate-700/50 shadow-lg space-y-4">
          <h2 className="font-bold text-xs tracking-widest text-slate-400 uppercase">
            Mission Metrics
          </h2>

          <IRMBar value={missionStats.irm} />
          <CoverageBar value={missionStats.coveragePct} />

          <div className="flex justify-between text-xs pt-2 border-t border-slate-700">
            <span className="text-slate-400">
              Drones:{' '}
              <span className="font-bold text-slate-200">{missionStats.droneCount}</span>
            </span>
            <span className="text-slate-400">
              Waypoints:{' '}
              <span className="font-bold text-slate-200">{missionStats.waypointCount}</span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
