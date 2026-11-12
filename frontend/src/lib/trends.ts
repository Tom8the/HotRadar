import type { Metric } from "./types";
export type TrendPoint = {
  observed_at: string; status: "present" | "failed" | "collecting" | "unknown_partial" | "not_on_board";
  snapshot_id: string | null; run_id: string; evidence_id: string | null;
  title: string | null; url: string | null; rank: number | null; metric: Metric | null;
  snapshot_status: string | null; from_cache: boolean | null; parser_version: string;
  interval_seconds: number | null; connectable_before: boolean; metric_compatible: boolean;
};
export type TrendSeries = {
  item_id: string; board_id: string; platform: string; source_name: string; title: string;
  metric_name: string; metric_label: string; metric_definition: Metric | null; points: TrendPoint[];
  comparison: { rank_delta: number | null; metric_delta: number | null; metric_percent: number | null;
    from_evidence_id: string | null; to_evidence_id: string | null; window_seconds: number | null; reason: string };
  stats: { observations: number; cached: number; partial: number; best_rank: number | null; last_rank: number | null;
    last_metric: Metric | null; first_observed_at: string | null; last_observed_at: string | null;
    observed_span_seconds: number | null; latest_status: string };
};
export type TrendGap = { start: string; end: string; kind: "before_first" | "missing" | "paused" };
export type TrendSource = {
  id: string; name: string; platform: string; state: string; enabled: boolean; stale: boolean;
  last_success_at: string | null; interval_seconds: number; metric_name: string; metric_label: string;
  coverage: { requested_days: number; sampled_days: number; snapshots: number; first_ever_observed_at: string | null;
    first_observed_at: string | null; last_observed_at: string | null; failed_runs: number;
    partial_snapshots: number; cached_snapshots: number; legacy_cadence_snapshots: number;
    gaps: TrendGap[]; definition: string;
    days: { date: string; snapshots: number | null; state: string; failed_runs: number; partial_snapshots: number;
      cached_snapshots: number; empty_snapshots: number; first_observed_at: string | null;
      last_observed_at: string | null; gap_seconds: number; open_day: boolean }[] };
};
export type TrendChoice = { id: string; title: string; source_titles: string[]; revision: number; platforms: string[]; members: number;
  followed: boolean; manual_locked: boolean; observations: number; first_observed_at: string; last_observed_at: string };
export type TrendOverview = {
  start_date: string; end_date: string; window_start: string; window_end: string; read_at: string;
  timezone: string; version: string; sampled_days: number; requested_days: number; seven_days_verified: boolean;
  coverage_note: string; relationship_note: string; sources: TrendSource[]; events: TrendChoice[];
};
export type EventTrend = { event: { id: string; title: string; revision: number; manual_locked: boolean };
  start_date: string; end_date: string; window_start: string; window_end: string; read_at: string;
  version: string; sources: TrendSource[]; series: TrendSeries[]; relationship_note: string };

export function shiftDay(day: string, offset: number) {
  const at = new Date(day + "T00:00:00Z"); at.setUTCDate(at.getUTCDate() + offset); return at.toISOString().slice(0, 10);
}
export function rangeFor(mode: string, anchor: string, customStart: string, customEnd: string) {
  if (mode === "custom") return { start: customStart, end: customEnd };
  if (mode === "previous") return { start: shiftDay(anchor, -1), end: shiftDay(anchor, -1) };
  return { start: mode === "day" ? anchor : shiftDay(anchor, -6), end: anchor };
}
export function pointValue(point: TrendPoint, mode: "rank" | "metric"): number | null {
  if (point.status !== "present") return null;
  return mode === "rank" ? point.rank : point.metric_compatible ? point.metric?.value ?? null : null;
}
export function curveSegments(points: TrendPoint[], mode: "rank" | "metric") {
  const segments: TrendPoint[][] = [];
  let current: TrendPoint[] = [];
  for (const point of points) {
    const value = pointValue(point, mode);
    if (value === null) { if (current.length) segments.push(current); current = []; continue; }
    if (!point.connectable_before && current.length) { segments.push(current); current = []; }
    current.push(point);
  }
  if (current.length) segments.push(current);
  return segments;
}
export function filterTrendChoices(events: TrendChoice[], query: string, source: string) {
  const needle = query.trim().toLocaleLowerCase();
  return events.filter(e => (!needle || [e.title, ...e.source_titles].some(t => t.toLocaleLowerCase().includes(needle))) && (source === "all" || e.platforms.includes(source)));
}
