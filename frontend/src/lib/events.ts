export type Evidence = {
  id: string; item_id: string; snapshot_id: string; board_id: string; platform: string;
  source_name: string; title: string; url: string; rank: number | null;
  metrics: Record<string, { value: number | null; unit: string; definition: string; provider: string }>;
  metric_name: string; metric_label: string; observed_at: string; first_observed_at: string;
  source_event_at: string | null; snapshot_status: string; from_cache: boolean; flags: string[];
  basis: { method: string; version: string; score: number | null; reason: string; review: string };
  on_latest_board?: boolean;
};
export type RadarEvent = {
  id: string; title: string; revision: number; manual_locked: boolean; followed: boolean;
  tags: string[]; platforms: string[]; first_observed_at: string; last_observed_at: string;
  observation_count: number; total_members: number; evidence: Evidence[];
  summary: { provider: string; version: string; model: string | null; input_ids: string[];
    lines: { text: string; evidence_id: string }[]; lead: string;
    coverage: { platform_count: number; item_count: number; observation_count: number; first_observed_at: string; last_observed_at: string };
    boundary: string; signature: string; record_id: string | null };
};
export type EventCatalog = {
  selected_date: string; read_at: string; events: RadarEvent[]; pending_snapshots: number;
  algorithm: string; coverage_note: string;
  sources: { id: string; name: string; state: string; last_success_at: string | null }[];
};
export type EventDetail = {
  event: RadarEvent; all_evidence: Evidence[];
  candidates: { id: string; title: string; score: number; reason: string; revision: number }[];
  changes: { id: string; kind: string; reason: string; changed_at: string; reverted_by: string | null;
    before: Record<string, { title: string; revision: number; members: Record<string, unknown> }>;
    after: Record<string, { title: string; revision: number; members: Record<string, unknown> }>;
    can_restore: boolean }[];
};

export function filterEvents(events: RadarEvent[], query: string, scope: string, tag: string, source = "all") {
  const needle = query.trim().toLocaleLowerCase();
  return events.filter(e => (!needle || [e.title, ...e.evidence.map(s => s.title)].some(t => t.toLocaleLowerCase().includes(needle))) &&
    (scope !== "cross" || e.platforms.length > 1) && (scope !== "manual" || e.manual_locked) &&
    (scope !== "followed" || e.followed) && (!tag || e.tags.includes(tag)) &&
    (source === "all" || e.platforms.includes(source)));
}
