export type Metric = { value: number | null; unit: string; provider: string; definition: string };
export type HotItem = {
  id: string; external_id: string; title: string; url: string; item_kind: string;
  rank: number | null; display_order: number; is_pinned: boolean; metrics: Record<string, Metric>;
  flags: string[]; first_observed_at: string; published_at: string | null;
  source_event_at: string | null; rank_change: number | null;
};
export type Snapshot = {
  id: string; observed_at: string; upstream_updated_at: string | null; item_count: number;
  status: string; from_cache: boolean; parser_version: string; items: HotItem[];
};
export type SnapshotSummary = Pick<Snapshot, "id" | "observed_at" | "item_count" | "status" | "from_cache">;
export type Board = {
  id: string; source_id: string; source_name: string; source_url: string; provider: string;
  name: string; item_kind: string; metric_name: string; metric_label: string;
  interval_seconds: number; state: string; stale: boolean; last_success_at: string | null;
  enabled: boolean;
  latest_run: null | { id: string; status: string; created_at: string; finished_at: string | null;
    attempt_count: number; error_code: string | null; error_message: string | null };
  snapshot: Snapshot | null;
};
export type ManagedSource = Omit<Board, "snapshot"> & {
  snapshot_items: number | null; first_observed_at: string | null; window_start: string;
  window: { total: number; success: number; partial: number; failed: number; unfinished: number };
  recent_runs: { id: string; created_at: string; status: string; attempt_count: number; error_code: string | null; item_count: number | null }[];
  changes: { id: string; changed_at: string; before: { enabled: boolean; interval_seconds: number }; after: { enabled: boolean; interval_seconds: number } }[];
};
export type DashboardData = {
  selected_date: string; timezone: string; read_at: string; first_observed_at: string | null;
  stage: string; coverage_note: string; boards: Board[];
  stats: { enabled_sources: number; healthy_sources: number; new_items: number; visible_items: number; snapshots: number };
};
