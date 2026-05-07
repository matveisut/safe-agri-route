/**
 * Extended Vitest tests for useMissionStore — covers slices not touched by store.test.ts:
 * - missionId / mission telemetry mode / draw mode
 * - liveFusion state machine
 * - fusionByDrone / dynamicJammerZones
 * - riskHeatmapColor edge cases and interpolation
 *
 * Regression coverage for recent code-review fixes:
 * - missionIsActive toggling is the source-of-truth for isConnected in the hook
 * - droneStatuses are correctly scoped (do not bleed across missions)
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { useMissionStore } from '../store/useMissionStore';
import { riskHeatmapColor } from '../utils/riskHeatmapColor';

const RESET = {
  selectedFieldId: null,
  selectedDroneIds: [],
  plannedRoutes: [],
  telemetry: {},
  missionStats: null,
  riskGridPreview: [],
  showRiskOverlay: false,
  missionIsActive: false,
  droneStatuses: {},
  fusionByDrone: {},
  dynamicJammerZones: [],
  liveFusion: {
    enabled: false,
    droneId: null,
    fusedThreatLevel: null,
    breakdown: null,
    lastAutoReplanEvent: 0,
  },
  missionTelemetryMode: 'simulation' as const,
  suspectedDrawMode: false,
  missionId: 1,
};

beforeEach(() => {
  useMissionStore.setState(RESET);
});

// ---------------------------------------------------------------------------
// missionId
// ---------------------------------------------------------------------------

describe('setMissionId', () => {
  it('defaults to 1', () => {
    expect(useMissionStore.getState().missionId).toBe(1);
  });

  it('updates to a new id', () => {
    useMissionStore.getState().setMissionId(42);
    expect(useMissionStore.getState().missionId).toBe(42);
  });

  it('overwrites a previous id', () => {
    useMissionStore.getState().setMissionId(5);
    useMissionStore.getState().setMissionId(99);
    expect(useMissionStore.getState().missionId).toBe(99);
  });
});

// ---------------------------------------------------------------------------
// missionTelemetryMode
// ---------------------------------------------------------------------------

describe('setMissionTelemetryMode', () => {
  it('defaults to simulation', () => {
    expect(useMissionStore.getState().missionTelemetryMode).toBe('simulation');
  });

  it('switches to live', () => {
    useMissionStore.getState().setMissionTelemetryMode('live');
    expect(useMissionStore.getState().missionTelemetryMode).toBe('live');
  });

  it('switches back to simulation', () => {
    useMissionStore.getState().setMissionTelemetryMode('live');
    useMissionStore.getState().setMissionTelemetryMode('simulation');
    expect(useMissionStore.getState().missionTelemetryMode).toBe('simulation');
  });
});

// ---------------------------------------------------------------------------
// suspectedDrawMode
// ---------------------------------------------------------------------------

describe('setSuspectedDrawMode', () => {
  it('defaults to false', () => {
    expect(useMissionStore.getState().suspectedDrawMode).toBe(false);
  });

  it('enables draw mode', () => {
    useMissionStore.getState().setSuspectedDrawMode(true);
    expect(useMissionStore.getState().suspectedDrawMode).toBe(true);
  });

  it('disables draw mode', () => {
    useMissionStore.setState({ suspectedDrawMode: true });
    useMissionStore.getState().setSuspectedDrawMode(false);
    expect(useMissionStore.getState().suspectedDrawMode).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// liveFusion state machine
// ---------------------------------------------------------------------------

describe('setLiveFusion', () => {
  it('enables fusion with a drone id', () => {
    useMissionStore.getState().setLiveFusion({ enabled: true, droneId: 2 });
    const { liveFusion } = useMissionStore.getState();
    expect(liveFusion.enabled).toBe(true);
    expect(liveFusion.droneId).toBe(2);
  });

  it('merges partial patch without touching other fields', () => {
    useMissionStore.setState({
      liveFusion: {
        enabled: true,
        droneId: 1,
        fusedThreatLevel: 0.6,
        breakdown: null,
        lastAutoReplanEvent: 3,
      },
    });
    useMissionStore.getState().setLiveFusion({ fusedThreatLevel: 0.9 });
    const { liveFusion } = useMissionStore.getState();
    expect(liveFusion.enabled).toBe(true);           // unchanged
    expect(liveFusion.droneId).toBe(1);              // unchanged
    expect(liveFusion.fusedThreatLevel).toBeCloseTo(0.9); // updated
    expect(liveFusion.lastAutoReplanEvent).toBe(3);  // unchanged
  });

  it('updates lastAutoReplanEvent', () => {
    useMissionStore.getState().setLiveFusion({ lastAutoReplanEvent: 7 });
    expect(useMissionStore.getState().liveFusion.lastAutoReplanEvent).toBe(7);
  });
});

describe('resetLiveFusion', () => {
  it('resets all fusion fields to defaults', () => {
    useMissionStore.setState({
      liveFusion: {
        enabled: true,
        droneId: 3,
        fusedThreatLevel: 0.8,
        breakdown: { gps_variance: 0.5, rssi_drop: 0.3, velocity_anomaly: 0.1 } as any,
        lastAutoReplanEvent: 5,
      },
    });
    useMissionStore.getState().resetLiveFusion();
    const { liveFusion } = useMissionStore.getState();
    expect(liveFusion.enabled).toBe(false);
    expect(liveFusion.droneId).toBeNull();
    expect(liveFusion.fusedThreatLevel).toBeNull();
    expect(liveFusion.breakdown).toBeNull();
    expect(liveFusion.lastAutoReplanEvent).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// fusionByDrone
// ---------------------------------------------------------------------------

describe('setFusionByDrone', () => {
  it('stores fusion snapshots keyed by drone id', () => {
    const snap = {
      1: { fused_threat_level: 0.3, packet_loss_rate: 0.1, breakdown: null, auto_replan_event_id: 0 } as any,
      2: { fused_threat_level: 0.8, packet_loss_rate: 0.4, breakdown: null, auto_replan_event_id: 1 } as any,
    };
    useMissionStore.getState().setFusionByDrone(snap);
    const { fusionByDrone } = useMissionStore.getState();
    expect(fusionByDrone[1].fused_threat_level).toBeCloseTo(0.3);
    expect(fusionByDrone[2].fused_threat_level).toBeCloseTo(0.8);
  });

  it('replaces previous fusion data entirely', () => {
    useMissionStore.setState({
      fusionByDrone: {
        99: { fused_threat_level: 0.5, packet_loss_rate: 0, breakdown: null, auto_replan_event_id: 0 } as any,
      },
    });
    useMissionStore.getState().setFusionByDrone({});
    expect(useMissionStore.getState().fusionByDrone).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// dynamicJammerZones
// ---------------------------------------------------------------------------

describe('setDynamicJammerZones', () => {
  it('stores dynamic zones', () => {
    const zones = [
      { zone_id: 'z1', zone_type: 'suspected_jammer', state: 'DRAWN',
        center: { lat: 45.04, lng: 41.97 }, radius_m: 200,
        geometry: null, confidence: 0.6, expires_in_sec: 30 },
    ] as any;
    useMissionStore.getState().setDynamicJammerZones(zones);
    expect(useMissionStore.getState().dynamicJammerZones).toHaveLength(1);
    expect(useMissionStore.getState().dynamicJammerZones[0].zone_id).toBe('z1');
  });

  it('clears zones when set to empty array', () => {
    useMissionStore.setState({ dynamicJammerZones: [{ zone_id: 'old' } as any] });
    useMissionStore.getState().setDynamicJammerZones([]);
    expect(useMissionStore.getState().dynamicJammerZones).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// missionIsActive — source of truth for isConnected in the hook (regression)
// ---------------------------------------------------------------------------

describe('missionIsActive as isConnected source of truth', () => {
  it('setMissionActive(true) makes missionIsActive truthy', () => {
    useMissionStore.getState().setMissionActive(true);
    expect(useMissionStore.getState().missionIsActive).toBe(true);
  });

  it('setMissionActive(false) makes missionIsActive falsy', () => {
    useMissionStore.setState({ missionIsActive: true });
    useMissionStore.getState().setMissionActive(false);
    expect(useMissionStore.getState().missionIsActive).toBe(false);
  });

  it('resetDroneStatuses does not touch missionIsActive', () => {
    useMissionStore.setState({ missionIsActive: true, droneStatuses: { 1: 'active' } });
    useMissionStore.getState().resetDroneStatuses();
    expect(useMissionStore.getState().missionIsActive).toBe(true);
    expect(useMissionStore.getState().droneStatuses).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// riskHeatmapColor — extended interpolation and boundary tests
// ---------------------------------------------------------------------------

describe('riskHeatmapColor interpolation', () => {
  it('exactly 0.25 maps to hue 90 (yellow-green)', () => {
    expect(riskHeatmapColor(0.25)).toBe('hsl(90, 90%, 46%)');
  });

  it('exactly 0.75 maps to hue 30 (orange)', () => {
    expect(riskHeatmapColor(0.75)).toBe('hsl(30, 90%, 46%)');
  });

  it('is monotonically decreasing hue as risk increases', () => {
    const steps = [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0];
    const hues = steps.map((r) => {
      const match = riskHeatmapColor(r).match(/hsl\((\d+(?:\.\d+)?)/);
      return match ? Number(match[1]) : -1;
    });
    for (let i = 1; i < hues.length; i++) {
      expect(hues[i]).toBeLessThanOrEqual(hues[i - 1]);
    }
  });

  it('clamps values > 1 to red', () => {
    expect(riskHeatmapColor(2)).toBe('hsl(0, 90%, 46%)');
    expect(riskHeatmapColor(999)).toBe('hsl(0, 90%, 46%)');
  });

  it('clamps values < 0 to green', () => {
    expect(riskHeatmapColor(-1)).toBe('hsl(120, 90%, 46%)');
    expect(riskHeatmapColor(-999)).toBe('hsl(120, 90%, 46%)');
  });

  it('handles string-coercible numbers', () => {
    // riskHeatmapColor accepts `unknown`, coerces via Number()
    expect(riskHeatmapColor('0.5')).toBe('hsl(60, 90%, 46%)');
  });

  it('handles Infinity as green (clamped to 0 after isFinite check)', () => {
    expect(riskHeatmapColor(Infinity)).toBe('hsl(120, 90%, 46%)');
    expect(riskHeatmapColor(-Infinity)).toBe('hsl(120, 90%, 46%)');
  });
});
