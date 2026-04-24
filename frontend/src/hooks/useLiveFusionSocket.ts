import { useEffect } from 'react';
import { useMissionStore } from '../store/useMissionStore';

/**
 * Deprecated thin-wrapper:
 * derives selected drone fusion from unified mission stream store state.
 */
export function useLiveFusionSocket() {
  const enabled = useMissionStore((s) => s.liveFusion.enabled);
  const droneId = useMissionStore((s) => s.liveFusion.droneId);
  const fusionByDrone = useMissionStore((s) => s.fusionByDrone);
  const setLiveFusion = useMissionStore((s) => s.setLiveFusion);

  useEffect(() => {
    if (!enabled || droneId == null) {
      setLiveFusion({ fusedThreatLevel: null, breakdown: null });
      return;
    }

    const fusion = fusionByDrone[droneId];
    setLiveFusion({
      fusedThreatLevel: fusion?.fused_threat_level ?? null,
      breakdown: fusion?.breakdown ?? null,
      lastAutoReplanEvent: fusion?.auto_replan_event_id ?? 0,
    });
  }, [enabled, droneId, fusionByDrone, setLiveFusion]);
}
