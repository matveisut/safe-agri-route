import { useState } from 'react';
import api from '../services/api';
import { useMissionStore, DroneStatus } from '../store/useMissionStore';
import { useTelemetry } from '../hooks/useTelemetry';

const ALL_DRONES = [
  { id: 1, name: 'AgriFly-1' },
  { id: 2, name: 'AgriFly-2' },
  { id: 3, name: 'AgriFly-3' },
];

const DRONE_COLORS: Record<number, string> = {
  1: '#3b82f6',
  2: '#8b5cf6',
  3: '#eab308',
};

/**
 * Status badge shown next to each drone name.
 */
function StatusBadge({ status }: { status: DroneStatus }) {
  const cfg = {
    active:  { label: 'IN FLIGHT', cls: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' },
    lost:    { label: 'LOST',      cls: 'bg-red-500/20    text-red-400    border-red-500/30    animate-pulse' },
    idle:    { label: 'IDLE',      cls: 'bg-slate-600/30  text-slate-400  border-slate-600/30' },
  }[status] ?? { label: status.toUpperCase(), cls: 'bg-slate-600/30 text-slate-400 border-slate-600/30' };

  return (
    <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${cfg.cls}`}>
      {cfg.label}
    </span>
  );
}

/**
 * Drone status panel — displayed in the sidebar when a mission is active.
 *
 * Shows each drone's current status and a "Simulate Loss" button that:
 *   1. Marks the drone as LOST in the store.
 *   2. Calls POST /mission/1/simulate-loss?drone_id={id}.
 *   3. Updates planned routes with the replanner's response.
 *   4. Restarts the telemetry WebSocket with the new routes + updated IRM.
 */
export default function DroneStatusPanel() {
  const {
    selectedDroneIds,
    selectedFieldId,
    plannedRoutes,
    missionIsActive,
    droneStatuses,
    setDroneStatus,
    setPlannedRoutes,
    updateMissionIRM,
  } = useMissionStore();

  const { isConnected, startSimulation, stopSimulation } = useTelemetry();

  const [replanning, setReplanning] = useState<number | null>(null); // drone_id being replanned
  const [disabled, setDisabled]     = useState<Set<number>>(new Set());

  // Only show drones that are part of the current mission plan.
  const activeDrones = ALL_DRONES.filter((d) => selectedDroneIds.includes(d.id));

  if (!missionIsActive || activeDrones.length === 0) return null;

  const handleSimulateLoss = async (droneId: number) => {
    if (!selectedFieldId || disabled.has(droneId)) return;

    setDisabled((prev) => new Set(prev).add(droneId));
    setDroneStatus(droneId, 'lost');
    setReplanning(droneId);

    try {
      const remainingDroneIds = selectedDroneIds.filter((id) => id !== droneId);
      const visitedCounts: Record<number, number> = {};
      selectedDroneIds.forEach((id) => { visitedCounts[id] = 0; });

      const res = await api.post(
        `/mission/1/simulate-loss?drone_id=${droneId}`,
        {
          field_id:      selectedFieldId,
          drone_ids:     remainingDroneIds,
          current_routes: plannedRoutes,
          visited_counts: visitedCounts,
        },
      );

      if (res.data.status !== 'mission_failed') {
        setPlannedRoutes(res.data.updated_routes);
        updateMissionIRM(res.data.new_irm);

        // Restart telemetry simulation with the new routes so the map
        // immediately shows the redistributed paths.
        if (isConnected) {
          stopSimulation();
          // Small delay so the WS connection fully closes before reopening.
          await new Promise<void>((r) => setTimeout(r, 400));
        }
        startSimulation(res.data.new_irm);
      }
    } catch (err) {
      console.error('simulate-loss failed', err);
      // Roll back the LOST status on error
      setDroneStatus(droneId, 'active');
      setDisabled((prev) => { const s = new Set(prev); s.delete(droneId); return s; });
    } finally {
      setReplanning(null);
    }
  };

  return (
    <div className="bg-slate-900/80 p-5 rounded-2xl border border-slate-700/50 shadow-lg">
      <h2 className="font-bold text-xs tracking-widest text-slate-400 mb-4 uppercase">
        Drone Status
      </h2>

      <div className="space-y-3">
        {activeDrones.map((drone) => {
          const status: DroneStatus = droneStatuses[drone.id] ?? 'active';
          const isReplanning = replanning === drone.id;
          const isLost       = status === 'lost';
          const dotColor     = DRONE_COLORS[drone.id] ?? '#64748b';

          return (
            <div
              key={drone.id}
              className="flex items-center justify-between gap-2 py-2 border-b border-slate-700/50 last:border-0"
            >
              {/* Drone indicator dot + name */}
              <div className="flex items-center gap-2 min-w-0">
                <span
                  className="w-3 h-3 rounded-full flex-shrink-0"
                  style={{ backgroundColor: dotColor }}
                />
                <span className="text-sm font-semibold text-slate-200 truncate">
                  {drone.name}
                </span>
                <StatusBadge status={status} />
              </div>

              {/* Simulate loss button */}
              {!isLost && (
                <button
                  onClick={() => handleSimulateLoss(drone.id)}
                  disabled={disabled.has(drone.id)}
                  className="flex-shrink-0 text-xs px-2 py-1 bg-orange-600/20 hover:bg-orange-600/40 text-orange-400 border border-orange-600/30 rounded-lg transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {isReplanning ? (
                    <span className="animate-pulse">Replanning…</span>
                  ) : (
                    '⚡ Simulate Loss'
                  )}
                </button>
              )}
            </div>
          );
        })}
      </div>

      {replanning && (
        <p className="mt-3 text-xs text-yellow-400 animate-pulse text-center">
          Redistributing waypoints…
        </p>
      )}
    </div>
  );
}
