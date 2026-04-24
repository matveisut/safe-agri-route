/** Снимок fusion с бэкенда (WS frame `fusion`, ТЗ §10). */

export interface FusionBreakdown {
  raw_scores?: Record<string, number>;
  peril?: Record<string, number>;
  weights?: Record<string, number>;
  fused_raw?: number;
  fused_threat_level?: number;
  any_feature_low?: boolean;
}

export interface FusionSnapshot {
  fused_threat_level: number;
  jam_prob?: number;
  packet_loss_rate?: number;
  state?: string;
  breakdown: FusionBreakdown;
  auto_replan_event_id: number;
}

export interface DynamicJammerZone {
  zone_id: string;
  zone_type: string;
  origin: string;
  state: string;
  confidence?: number;
  ttl_sec?: number;
  created_at?: number;
  updated_at?: number;
  severity?: number;
  expires_in_sec?: number;
  source_drone_id?: number;
  geometry?: GeoJSON.Geometry;
  center?: { lat: number; lng: number };
  radius_m?: number;
  note?: string;
}
