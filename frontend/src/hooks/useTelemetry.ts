import { useEffect, useRef, useState } from 'react';
import { useMissionStore } from '../store/useMissionStore';

export function useTelemetry() {
  const wsRef = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  const {
    updateTelemetry,
    updateMissionIRM,
    setMissionActive,
    setDroneStatus,
    resetDroneStatuses,
  } = useMissionStore();

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  /**
   * Open the telemetry WebSocket and start the flight simulation.
   *
   * @param irm  Optional IRM value to relay to the backend so the first frame
   *             can carry an `irm_update` field — satisfying the requirement
   *             that IRM updates propagate through WebSocket frames.
   */
  const startSimulation = (irm?: number) => {
    const routes = useMissionStore.getState().plannedRoutes;

    if (!routes || routes.length === 0) {
      alert('No planned routes available. Please generate a route first.');
      return;
    }

    wsRef.current?.close();

    const ws = new WebSocket('ws://localhost:8000/ws/telemetry');
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('Telemetry WebSocket connected.');
      setIsConnected(true);
      setMissionActive(true);

      // Mark all mission drones as active at start
      routes.forEach((r) => setDroneStatus(r.drone_id, 'active'));

      // Include irm so the server echoes it back in the first frame as irm_update
      const payload: Record<string, unknown> = { routes };
      if (irm !== undefined) payload.irm = irm;
      ws.send(JSON.stringify(payload));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        // irm_update arrives in the first frame (and after replanning restarts)
        if (data.irm_update != null) {
          updateMissionIRM(data.irm_update);
        }

        if (data.message) {
          console.log('Server message:', data.message);
          if (data.message === 'Mission Completed') {
            setIsConnected(false);
            setMissionActive(false);
          }
          return;
        }

        if (data.telemetry) {
          data.telemetry.forEach((t: { drone_id: number; lat: number; lng: number; status: string }) => {
            updateTelemetry(t.drone_id, { lat: t.lat, lng: t.lng });
          });
        }
      } catch (err) {
        console.error('Error parsing telemetry data:', err);
      }
    };

    ws.onclose = () => {
      console.log('Telemetry WebSocket closed.');
      setIsConnected(false);
    };

    ws.onerror = (err) => {
      console.error('Telemetry WebSocket error:', err);
      setIsConnected(false);
    };
  };

  const stopSimulation = () => {
    wsRef.current?.close();
    setIsConnected(false);
    setMissionActive(false);
    resetDroneStatuses();
  };

  return { startSimulation, stopSimulation, isConnected };
}
