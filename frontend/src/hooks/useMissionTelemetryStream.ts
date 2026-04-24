import { useCallback, useEffect, useRef, useState } from 'react';

import { WS_ORIGIN } from '../config';
import api from '../services/api';
import type { DynamicJammerZone, FusionSnapshot } from '../types/fusion';
import type { DroneRoute } from '../store/useMissionStore';
import { useMissionStore } from '../store/useMissionStore';

type MissionStreamMode = 'simulation' | 'live';

type MissionFrame = {
  protocol?: string;
  source?: string;
  telemetry?: Array<{
    drone_id: number;
    lat: number;
    lng: number;
    status?: string;
  }>;
  fusion_by_drone?: Record<string, FusionSnapshot>;
  dynamic_zones?: DynamicJammerZone[];
  irm_update?: number;
  message?: string | null;
};

/**
 * Unified mission telemetry stream over `/ws/telemetry/mission`.
 * One socket transports telemetry, fusion and dynamic zones.
 */
export function useMissionTelemetryStream(
  mode: MissionStreamMode,
  routes: DroneRoute[],
  irm?: number,
) {
  const wsRef = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [liveStartStatus, setLiveStartStatus] = useState<
    'idle' | 'starting' | 'started' | 'partial' | 'failed'
  >('idle');
  const [liveStartMessage, setLiveStartMessage] = useState<string | null>(null);

  const {
    updateTelemetry,
    updateMissionIRM,
    setMissionActive,
    setDroneStatus,
    resetDroneStatuses,
    setFusionByDrone,
    setDynamicJammerZones,
    selectedFieldId,
    missionId,
  } = useMissionStore();

  const stop = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    setIsConnected(false);
    setMissionActive(false);
    resetDroneStatuses();
    if (mode === 'live') {
      setLiveStartStatus('idle');
      setLiveStartMessage(null);
    }
  }, [resetDroneStatuses, setMissionActive]);

  const start = useCallback(
    async (nextIrm?: number) => {
      if (!routes || routes.length === 0) {
        alert('No planned routes available. Please generate a route first.');
        return;
      }
      if (mode === 'live') {
        if (!selectedFieldId) {
          alert('Please select a field before starting live telemetry.');
          return;
        }
        setLiveStartStatus('starting');
        setLiveStartMessage('Запускаем миссию в SITL...');
        const missionDroneIds = routes.map((r) => r.drone_id);
        try {
          const startRes = await api.post(`/mission/${missionId}/start`, {
            routes,
            altitude_m: 30.0,
          });
          const startStatus = String(startRes?.data?.status ?? '');
          if (startStatus === 'failed') {
            setLiveStartStatus('failed');
            setLiveStartMessage('SITL отклонил старт (status=failed). Проверь MAVLink/SITL логи.');
            alert('SITL mission start failed. Check backend logs and SITL links.');
            return;
          }
          if (startStatus === 'partial') {
            setLiveStartStatus('partial');
            setLiveStartMessage('Миссия стартовала частично (не все дроны).');
            console.warn('Mission started partially:', startRes.data);
          } else {
            setLiveStartStatus('started');
            setLiveStartMessage('Миссия успешно запущена в SITL.');
          }
        } catch (err) {
          console.error('Failed to start live mission:', err);
          setLiveStartStatus('failed');
          setLiveStartMessage('Не удалось выполнить /mission/{id}/start.');
          alert('Failed to start live mission in SITL.');
          return;
        }

        const visitedCounts: Record<number, number> = {};
        missionDroneIds.forEach((id) => {
          visitedCounts[id] = 0;
        });
        try {
          await api.post(`/mission/${missionId}/fusion-context`, {
            field_id: selectedFieldId,
            drone_ids: missionDroneIds,
            current_routes: routes,
            visited_counts: visitedCounts,
          });
        } catch (err) {
          console.error('Failed to register fusion context:', err);
          setLiveStartStatus('failed');
          setLiveStartMessage('Fusion context не зарегистрирован.');
          alert('Failed to register fusion context for live mission.');
          return;
        }
      }

      wsRef.current?.close();
      const ws = new WebSocket(`${WS_ORIGIN}/ws/telemetry/mission`);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        setMissionActive(true);
        routes.forEach((r) => setDroneStatus(r.drone_id, 'active'));
        const payload: Record<string, unknown> = {
          protocol: 'v1',
          mode,
          routes,
        };
        const irmValue = nextIrm ?? irm;
        if (irmValue !== undefined) payload.irm = irmValue;
        ws.send(JSON.stringify(payload));
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as MissionFrame;
          if (typeof data.irm_update === 'number') {
            updateMissionIRM(data.irm_update);
          }
          if (Array.isArray(data.telemetry)) {
            data.telemetry.forEach((t) => {
              updateTelemetry(t.drone_id, { lat: t.lat, lng: t.lng });
              const s = (t.status ?? '').toLowerCase();
              if (s === 'lost') setDroneStatus(t.drone_id, 'lost');
              else if (s === 'idle') setDroneStatus(t.drone_id, 'idle');
              else setDroneStatus(t.drone_id, 'active');
            });
          }
          if (data.fusion_by_drone) {
            const mapped: Record<number, FusionSnapshot> = {};
            Object.entries(data.fusion_by_drone).forEach(([k, v]) => {
              const id = Number.parseInt(k, 10);
              if (!Number.isNaN(id)) mapped[id] = v;
            });
            setFusionByDrone(mapped);
          }
          if (Array.isArray(data.dynamic_zones)) {
            setDynamicJammerZones(data.dynamic_zones);
          }
          if (data.message === 'Mission Completed') {
            setIsConnected(false);
            setMissionActive(false);
          }
        } catch (err) {
          console.error('Error parsing mission telemetry frame:', err);
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        setIsConnected(false);
      };
      ws.onerror = () => {
        wsRef.current?.close();
      };
    },
    [
      irm,
      missionId,
      mode,
      routes,
      selectedFieldId,
      setDroneStatus,
      setDynamicJammerZones,
      setFusionByDrone,
      setMissionActive,
      updateMissionIRM,
      updateTelemetry,
    ],
  );

  useEffect(() => () => wsRef.current?.close(), []);

  return { start, stop, isConnected, liveStartStatus, liveStartMessage };
}
