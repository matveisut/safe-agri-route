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
  breakdown: FusionBreakdown;
  auto_replan_event_id: number;
}
