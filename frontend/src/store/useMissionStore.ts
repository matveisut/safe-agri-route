import { create } from 'zustand';

import type { DynamicJammerZone, FusionBreakdown, FusionSnapshot } from '../types/fusion';

export interface RoutePoint {
  lat: number;
  lng: number;
}

export interface DroneRoute {
  drone_id: number;
  route: RoutePoint[];
}

export interface Coordinates {
  lat: number;
  lng: number;
}

export interface FieldType {
  id: number;
  name: string;
  geojson: any;
}

export interface RiskZoneType {
  id: number;
  type: string;
  severity_weight: number;
  geojson: any;
}

export interface RiskGridPoint {
  lat: number;
  lng: number;
  risk: number;
}

export interface MissionStats {
  irm: number;
  coveragePct: number;
  waypointCount: number;
  droneCount: number;
}

export type DroneStatus = 'active' | 'lost' | 'idle';

/** Поток MAVLink + fusion (Промпт 12); без SITL поля остаются null / выкл. */
export interface LiveFusionState {
  enabled: boolean;
  droneId: number | null;
  fusedThreatLevel: number | null;
  breakdown: FusionBreakdown | null;
  lastAutoReplanEvent: number;
}

export type FusionByDrone = Record<number, FusionSnapshot>;

interface MissionState {
  fields: FieldType[];
  selectedFieldId: number | null;
  selectedDroneIds: number[];
  plannedRoutes: DroneRoute[];
  telemetry: Record<number, Coordinates>;

  // Post-planning metrics
  missionStats: MissionStats | null;

  // Risk heat-map
  riskGridPreview: RiskGridPoint[];
  showRiskOverlay: boolean;

  // Operational status
  missionIsActive: boolean;
  droneStatuses: Record<number, DroneStatus>;

  liveFusion: LiveFusionState;
  fusionByDrone: FusionByDrone;
  dynamicJammerZones: DynamicJammerZone[];
  missionTelemetryMode: 'simulation' | 'live';
  suspectedDrawMode: boolean;
  missionId: number;

  // Actions
  setFields: (fields: FieldType[]) => void;
  setSelectedField: (id: number) => void;
  toggleDroneSelection: (id: number) => void;
  setPlannedRoutes: (routes: DroneRoute[]) => void;
  updateTelemetry: (drone_id: number, coords: Coordinates) => void;
  setMissionStats: (stats: MissionStats) => void;
  updateMissionIRM: (irm: number) => void;
  setRiskGridPreview: (points: RiskGridPoint[]) => void;
  setShowRiskOverlay: (show: boolean) => void;
  setMissionActive: (active: boolean) => void;
  setDroneStatus: (droneId: number, status: DroneStatus) => void;
  resetDroneStatuses: () => void;

  setLiveFusion: (patch: Partial<LiveFusionState>) => void;
  resetLiveFusion: () => void;
  setFusionByDrone: (fusion: FusionByDrone) => void;
  setDynamicJammerZones: (zones: DynamicJammerZone[]) => void;
  setMissionTelemetryMode: (mode: 'simulation' | 'live') => void;
  setSuspectedDrawMode: (enabled: boolean) => void;
  setMissionId: (missionId: number) => void;
}

export const useMissionStore = create<MissionState>((set) => ({
  fields: [],
  selectedFieldId: null,
  selectedDroneIds: [1, 2, 3],
  plannedRoutes: [],
  telemetry: {},
  missionStats: null,
  riskGridPreview: [],
  showRiskOverlay: false,
  missionIsActive: false,
  droneStatuses: {},

  liveFusion: {
    enabled: false,
    droneId: null,
    fusedThreatLevel: null,
    breakdown: null,
    lastAutoReplanEvent: 0,
  },
  fusionByDrone: {},
  dynamicJammerZones: [],
  missionTelemetryMode: 'simulation',
  suspectedDrawMode: false,
  missionId: 1,

  setFields: (fields) => set({ fields }),

  setSelectedField: (id) => set({ selectedFieldId: id }),

  toggleDroneSelection: (id) =>
    set((state) => {
      const isSelected = state.selectedDroneIds.includes(id);
      return {
        selectedDroneIds: isSelected
          ? state.selectedDroneIds.filter((d) => d !== id)
          : [...state.selectedDroneIds, id],
      };
    }),

  setPlannedRoutes: (routes) => set({ plannedRoutes: routes }),

  updateTelemetry: (drone_id, coords) =>
    set((state) => ({
      telemetry: { ...state.telemetry, [drone_id]: coords },
    })),

  setMissionStats: (stats) => set({ missionStats: stats }),

  updateMissionIRM: (irm) =>
    set((state) => ({
      missionStats: state.missionStats
        ? { ...state.missionStats, irm }
        : { irm, coveragePct: 0, waypointCount: 0, droneCount: 0 },
    })),

  setRiskGridPreview: (points) => set({ riskGridPreview: points }),

  setShowRiskOverlay: (show) => set({ showRiskOverlay: show }),

  setMissionActive: (active) => set({ missionIsActive: active }),

  setDroneStatus: (droneId, status) =>
    set((state) => ({
      droneStatuses: { ...state.droneStatuses, [droneId]: status },
    })),

  resetDroneStatuses: () => set({ droneStatuses: {} }),

  setLiveFusion: (patch) =>
    set((state) => ({
      liveFusion: { ...state.liveFusion, ...patch },
    })),

  resetLiveFusion: () =>
    set({
      liveFusion: {
        enabled: false,
        droneId: null,
        fusedThreatLevel: null,
        breakdown: null,
        lastAutoReplanEvent: 0,
      },
    }),

  setFusionByDrone: (fusion) => set({ fusionByDrone: fusion }),
  setDynamicJammerZones: (zones) => set({ dynamicJammerZones: zones }),
  setMissionTelemetryMode: (mode) => set({ missionTelemetryMode: mode }),
  setSuspectedDrawMode: (enabled) => set({ suspectedDrawMode: enabled }),
  setMissionId: (missionId) => set({ missionId }),
}));
