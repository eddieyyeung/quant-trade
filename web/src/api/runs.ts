import { apiUrl, request } from './http';

/** Lifecycle of a run. Mirrors `RunStatus` on the server. */
export type RunStatus = 'pending' | 'running' | 'ok' | 'failed' | 'cancelled' | 'interrupted';

export const TERMINAL_STATUSES: RunStatus[] = ['ok', 'failed', 'cancelled', 'interrupted'];

export function isTerminal(status: RunStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

/** Human-readable labels, so every page names a status the same way. */
export const STATUS_LABELS: Record<RunStatus, string> = {
  pending: '排队中',
  running: '运行中',
  ok: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
};

export const STATUS_COLORS: Record<RunStatus, string> = {
  pending: 'default',
  running: 'processing',
  ok: 'success',
  failed: 'error',
  cancelled: 'warning',
  interrupted: 'warning',
};

/** A run as it appears in the history list. */
export interface RunSummary {
  run_id: string;
  kind: string;
  status: RunStatus;
  progress: number;
  message: string;
  trigger: string;
  idempotency_key: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

/** A run including the parameters it was submitted with — enough to re-run it. */
export interface RunDetail extends RunSummary {
  params: Record<string, unknown>;
}

export interface RunPage {
  total: number;
  items: RunSummary[];
}

/** Where an artifact's bytes live, since `ref` alone is ambiguous. */
export type ArtifactStorage = 'table' | 'parquet' | 'html';

export interface Artifact {
  artifact_id: string;
  run_id: string;
  kind: string;
  storage: ArtifactStorage;
  ref: string;
  row_count: number | null;
  meta: Record<string, unknown>;
  created_at: string | null;
}

/** One line of a run's log, as delivered over SSE. */
export interface RunLogLine {
  seq: number;
  ts: string | null;
  level: string;
  message: string;
}

export interface SubmitResult {
  run_id: string;
  kind: string;
  status: RunStatus;
}

export const runsApi = {
  /** A page of run history, newest first. */
  list(limit: number, offset: number) {
    const q = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    return request<RunPage>(`/runs?${q}`);
  },

  get(runId: string) {
    return request<RunDetail>(`/runs/${runId}`);
  },

  artifacts(runId: string) {
    return request<Artifact[]>(`/runs/${runId}/artifacts`);
  },

  /** Queue any registered task type. Returns as soon as the run is persisted. */
  submit(kind: string, params: Record<string, unknown>) {
    return request<SubmitResult>('/runs', {
      method: 'POST',
      body: JSON.stringify({ kind, params }),
    });
  },

  cancel(runId: string) {
    return request<{ run_id: string; cancelling: boolean }>(`/runs/${runId}/cancel`, { method: 'POST' });
  },

  /** The SSE endpoint for a run's log stream, for use with `EventSource`. */
  logUrl(runId: string) {
    return apiUrl(`/runs/${runId}/logs`);
  },
};
