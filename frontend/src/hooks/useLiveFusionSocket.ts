import { useEffect, useRef } from 'react';

import { WS_ORIGIN } from '../config';
import type { FusionBreakdown } from '../types/fusion';
import { useMissionStore } from '../store/useMissionStore';

/**
 * Поддерживает WebSocket `/ws/telemetry/{drone_id}` при включённом live fusion.
 * Обновляет позицию дрона на карте и снимок fusion из поля `fusion`.
 */
export function useLiveFusionSocket() {
  const enabled = useMissionStore((s) => s.liveFusion.enabled);
  const droneId = useMissionStore((s) => s.liveFusion.droneId);
  const setLiveFusion = useMissionStore((s) => s.setLiveFusion);
  const updateTelemetry = useMissionStore((s) => s.updateTelemetry);

  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!enabled || droneId == null) {
      wsRef.current?.close();
      wsRef.current = null;
      setLiveFusion({ fusedThreatLevel: null, breakdown: null });
      return;
    }

    const id = droneId;
    const ws = new WebSocket(`${WS_ORIGIN}/ws/telemetry/${id}`);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as Record<string, unknown>;
        if (typeof data.lat === 'number' && typeof data.lng === 'number') {
          updateTelemetry(id, { lat: data.lat, lng: data.lng });
        }
        const fusion = data.fusion as
          | {
              fused_threat_level?: number;
              breakdown?: Record<string, unknown>;
              auto_replan_event_id?: number;
            }
          | undefined;
        if (fusion) {
          setLiveFusion({
            fusedThreatLevel:
              typeof fusion.fused_threat_level === 'number'
                ? fusion.fused_threat_level
                : null,
            breakdown: (fusion.breakdown as FusionBreakdown) ?? null,
            lastAutoReplanEvent: fusion.auto_replan_event_id ?? 0,
          });
        }
      } catch {
        /* malformed frame */
      }
    };

    ws.onclose = () => {
      wsRef.current = null;
    };

    ws.onerror = () => {
      wsRef.current?.close();
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [enabled, droneId, setLiveFusion, updateTelemetry]);
}
