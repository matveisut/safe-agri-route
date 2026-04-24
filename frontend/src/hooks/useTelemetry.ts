import { useMemo } from 'react';
import { useMissionStore } from '../store/useMissionStore';
import { useMissionTelemetryStream } from './useMissionTelemetryStream';

/**
 * Deprecated thin-wrapper over `useMissionTelemetryStream` for backward compatibility.
 */
export function useTelemetry() {
  const plannedRoutes = useMissionStore((s) => s.plannedRoutes);
  const { start, stop, isConnected } = useMissionTelemetryStream(
    'simulation',
    plannedRoutes,
  );

  /**
   * Open the telemetry WebSocket and start the flight simulation.
   *
   * @param irm  Optional IRM value to relay to the backend so the first frame
   *             can carry an `irm_update` field — satisfying the requirement
   *             that IRM updates propagate through WebSocket frames.
   */
  const api = useMemo(
    () => ({
      startSimulation: (irm?: number) => start(irm),
      stopSimulation: stop,
      isConnected,
    }),
    [isConnected, start, stop],
  );

  return api;
}
