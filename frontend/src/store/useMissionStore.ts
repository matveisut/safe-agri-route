import { create } from 'zustand';

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

interface MissionState {
  selectedFieldId: number | null;
  selectedDroneIds: number[];
  plannedRoutes: DroneRoute[];
  telemetry: Record<number, Coordinates>;
  
  // Actions
  setSelectedField: (id: number) => void;
  toggleDroneSelection: (id: number) => void;
  setPlannedRoutes: (routes: DroneRoute[]) => void;
  updateTelemetry: (drone_id: number, coords: Coordinates) => void;
}

export const useMissionStore = create<MissionState>((set) => ({
  selectedFieldId: null,
  selectedDroneIds: [],
  plannedRoutes: [],
  telemetry: {},

  setSelectedField: (id) => set({ selectedFieldId: id }),
  
  toggleDroneSelection: (id) => set((state) => {
    const isSelected = state.selectedDroneIds.includes(id);
    if (isSelected) {
      return { selectedDroneIds: state.selectedDroneIds.filter(d => d !== id) };
    } else {
      return { selectedDroneIds: [...state.selectedDroneIds, id] };
    }
  }),
  
  setPlannedRoutes: (routes) => set({ plannedRoutes: routes }),
  
  updateTelemetry: (drone_id, coords) => set((state) => ({
    telemetry: {
      ...state.telemetry,
      [drone_id]: coords
    }
  }))
}));
