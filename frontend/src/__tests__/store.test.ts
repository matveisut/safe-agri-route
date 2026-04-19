/**
 * Vitest unit tests for useMissionStore.
 *
 * Run with:
 *   npx vitest run src/__tests__/store.test.ts
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { useMissionStore } from '../store/useMissionStore';
import { riskHeatmapColor } from '../utils/riskHeatmapColor';

// Reset the store state before each test to prevent cross-test pollution.
beforeEach(() => {
  useMissionStore.setState({
    selectedFieldId: null,
    selectedDroneIds: [],
    plannedRoutes: [],
    telemetry: {},
    missionStats: null,
    riskGridPreview: [],
    showRiskOverlay: false,
    missionIsActive: false,
    droneStatuses: {},
  });
});

// ---------------------------------------------------------------------------
// Field selection
// ---------------------------------------------------------------------------

describe('setSelectedField', () => {
  it('sets the selected field id', () => {
    useMissionStore.getState().setSelectedField(7);
    expect(useMissionStore.getState().selectedFieldId).toBe(7);
  });

  it('overwrites a previous selection', () => {
    useMissionStore.getState().setSelectedField(1);
    useMissionStore.getState().setSelectedField(99);
    expect(useMissionStore.getState().selectedFieldId).toBe(99);
  });
});

// ---------------------------------------------------------------------------
// Drone selection
// ---------------------------------------------------------------------------

describe('toggleDroneSelection', () => {
  it('adds a drone when not yet selected', () => {
    useMissionStore.getState().toggleDroneSelection(2);
    expect(useMissionStore.getState().selectedDroneIds).toContain(2);
  });

  it('removes a drone when already selected', () => {
    useMissionStore.setState({ selectedDroneIds: [1, 2, 3] });
    useMissionStore.getState().toggleDroneSelection(2);
    expect(useMissionStore.getState().selectedDroneIds).not.toContain(2);
    expect(useMissionStore.getState().selectedDroneIds).toContain(1);
    expect(useMissionStore.getState().selectedDroneIds).toContain(3);
  });

  it('supports multiple selections', () => {
    useMissionStore.getState().toggleDroneSelection(1);
    useMissionStore.getState().toggleDroneSelection(3);
    expect(useMissionStore.getState().selectedDroneIds).toEqual([1, 3]);
  });
});

// ---------------------------------------------------------------------------
// Planned routes
// ---------------------------------------------------------------------------

describe('setPlannedRoutes', () => {
  it('stores drone routes', () => {
    const routes = [
      { drone_id: 1, route: [{ lat: 45.04, lng: 41.97 }] },
    ];
    useMissionStore.getState().setPlannedRoutes(routes);
    expect(useMissionStore.getState().plannedRoutes).toHaveLength(1);
    expect(useMissionStore.getState().plannedRoutes[0].drone_id).toBe(1);
  });

  it('replaces existing routes', () => {
    useMissionStore.setState({
      plannedRoutes: [{ drone_id: 99, route: [] }],
    });
    useMissionStore.getState().setPlannedRoutes([]);
    expect(useMissionStore.getState().plannedRoutes).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Telemetry
// ---------------------------------------------------------------------------

describe('updateTelemetry', () => {
  it('stores coordinates keyed by drone_id', () => {
    useMissionStore.getState().updateTelemetry(1, { lat: 45.041, lng: 41.971 });
    expect(useMissionStore.getState().telemetry[1]).toEqual({ lat: 45.041, lng: 41.971 });
  });

  it('merges multiple drones', () => {
    useMissionStore.getState().updateTelemetry(1, { lat: 1, lng: 1 });
    useMissionStore.getState().updateTelemetry(2, { lat: 2, lng: 2 });
    const { telemetry } = useMissionStore.getState();
    expect(telemetry[1].lat).toBe(1);
    expect(telemetry[2].lat).toBe(2);
  });

  it('overwrites previous position for the same drone', () => {
    useMissionStore.getState().updateTelemetry(1, { lat: 10, lng: 10 });
    useMissionStore.getState().updateTelemetry(1, { lat: 20, lng: 20 });
    expect(useMissionStore.getState().telemetry[1]).toEqual({ lat: 20, lng: 20 });
  });
});

// ---------------------------------------------------------------------------
// Mission stats
// ---------------------------------------------------------------------------

describe('setMissionStats', () => {
  it('stores all metric fields', () => {
    useMissionStore.getState().setMissionStats({
      irm: 0.83,
      coveragePct: 94.3,
      waypointCount: 847,
      droneCount: 4,
    });
    const stats = useMissionStore.getState().missionStats!;
    expect(stats.irm).toBeCloseTo(0.83);
    expect(stats.coveragePct).toBeCloseTo(94.3);
    expect(stats.waypointCount).toBe(847);
    expect(stats.droneCount).toBe(4);
  });
});

describe('updateMissionIRM', () => {
  it('updates irm on existing stats without touching other fields', () => {
    useMissionStore.getState().setMissionStats({
      irm: 0.9,
      coveragePct: 80.0,
      waypointCount: 100,
      droneCount: 2,
    });
    useMissionStore.getState().updateMissionIRM(0.55);
    const stats = useMissionStore.getState().missionStats!;
    expect(stats.irm).toBeCloseTo(0.55);
    expect(stats.coveragePct).toBeCloseTo(80.0); // unchanged
  });

  it('creates a stats object when none exists', () => {
    useMissionStore.getState().updateMissionIRM(0.7);
    expect(useMissionStore.getState().missionStats?.irm).toBeCloseTo(0.7);
  });
});

// ---------------------------------------------------------------------------
// Risk overlay
// ---------------------------------------------------------------------------

describe('setRiskGridPreview', () => {
  it('stores risk grid points', () => {
    const pts = [
      { lat: 45.04, lng: 41.97, risk: 0.1 },
      { lat: 45.05, lng: 41.98, risk: 0.9 },
    ];
    useMissionStore.getState().setRiskGridPreview(pts);
    expect(useMissionStore.getState().riskGridPreview).toHaveLength(2);
    expect(useMissionStore.getState().riskGridPreview[1].risk).toBeCloseTo(0.9);
  });
});

describe('setShowRiskOverlay', () => {
  it('toggles the overlay visibility', () => {
    useMissionStore.getState().setShowRiskOverlay(true);
    expect(useMissionStore.getState().showRiskOverlay).toBe(true);
    useMissionStore.getState().setShowRiskOverlay(false);
    expect(useMissionStore.getState().showRiskOverlay).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Mission active / drone statuses
// ---------------------------------------------------------------------------

describe('setMissionActive', () => {
  it('marks the mission as active', () => {
    useMissionStore.getState().setMissionActive(true);
    expect(useMissionStore.getState().missionIsActive).toBe(true);
  });

  it('marks the mission as inactive', () => {
    useMissionStore.setState({ missionIsActive: true });
    useMissionStore.getState().setMissionActive(false);
    expect(useMissionStore.getState().missionIsActive).toBe(false);
  });
});

describe('setDroneStatus', () => {
  it('sets a drone status', () => {
    useMissionStore.getState().setDroneStatus(2, 'lost');
    expect(useMissionStore.getState().droneStatuses[2]).toBe('lost');
  });

  it('updates independent statuses for multiple drones', () => {
    useMissionStore.getState().setDroneStatus(1, 'active');
    useMissionStore.getState().setDroneStatus(2, 'lost');
    useMissionStore.getState().setDroneStatus(3, 'idle');
    const { droneStatuses } = useMissionStore.getState();
    expect(droneStatuses[1]).toBe('active');
    expect(droneStatuses[2]).toBe('lost');
    expect(droneStatuses[3]).toBe('idle');
  });
});

describe('resetDroneStatuses', () => {
  it('clears all drone statuses', () => {
    useMissionStore.setState({ droneStatuses: { 1: 'active', 2: 'lost' } });
    useMissionStore.getState().resetDroneStatuses();
    expect(useMissionStore.getState().droneStatuses).toEqual({});
  });
});

describe('riskHeatmapColor', () => {
  it('maps 0 to green hue', () => {
    expect(riskHeatmapColor(0)).toMatch(/^hsl\(120,/);
  });

  it('maps 0.5 to mid hue (yellow)', () => {
    expect(riskHeatmapColor(0.5)).toMatch(/^hsl\(60,/);
  });

  it('maps 1 to red hue', () => {
    expect(riskHeatmapColor(1)).toMatch(/^hsl\(0,/);
  });

  it('clamps invalid input', () => {
    expect(riskHeatmapColor(NaN)).toMatch(/^hsl\(120,/);
    expect(riskHeatmapColor(undefined)).toMatch(/^hsl\(120,/);
  });
});
