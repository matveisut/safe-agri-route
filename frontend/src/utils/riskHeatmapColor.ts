/**
 * Maps normalized risk ∈ [0, 1] to a fill colour for the heat-map overlay.
 * Uses HSL hue 120° (green) → 0° (red) so mid-range values read as yellow/orange,
 * unlike RGB (r,255,0) where most of the range looks "still green".
 */
export function riskHeatmapColor(risk: unknown): string {
  const raw = typeof risk === 'number' ? risk : Number(risk);
  const t = Math.min(1, Math.max(0, Number.isFinite(raw) ? raw : 0));
  const hue = 120 * (1 - t);
  return `hsl(${hue}, 90%, 46%)`;
}
