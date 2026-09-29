import { request } from './http';
import type { RunStatus } from './runs';

/** One row of the training history. */
export interface ModelRunSummary {
  run_id: string;
  status: RunStatus;
  /** The one field that moves while a run is still going. */
  progress: number;
  message: string;
  /** First day the run's IC series covers — not the window it was asked for. */
  start: string | null;
  end: string | null;
  /** What the run was submitted with; `null` when it took the default range. */
  requested_start: string | null;
  requested_end: string | null;
  /** How many factors the run asked for; `null` when it took the default set. */
  factor_count: number | null;
  ic_days: number;
  windows_trained: number;
  prediction_rows: number;
  ic_mean: number | null;
  ic_ir: number | null;
  ic_positive_ratio: number | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface ModelRunPage {
  total: number;
  items: ModelRunSummary[];
}

/** One run's headline numbers, as the evaluation page's cards render them. */
export interface ModelMetrics {
  ic_mean: number | null;
  ic_ir: number | null;
  ic_positive_ratio: number | null;
  ic_days: number;
  windows_trained: number;
  prediction_rows: number;
}

/** One prediction day's RankIC. */
export interface ModelIcPoint {
  trade_date: string;
  rank_ic: number;
}

/** One natural year of the IC series — the cross-year stability read. */
export interface ModelYearSummary {
  year: number;
  ic_mean: number;
  ic_ir: number | null;
  ic_positive_ratio: number;
  days: number;
}

/** One factor's importance, averaged across the walk-forward windows. */
export interface ModelImportanceEntry {
  factor: string;
  importance: number;
  /** Spread of that average across windows. */
  std: number;
}

export interface ModelEvaluation {
  run_id: string;
  status: RunStatus;
  start: string | null;
  end: string | null;
  finished_at: string | null;
  metrics: ModelMetrics;
  ic_series: ModelIcPoint[];
  yearly: ModelYearSummary[];
  importance: ModelImportanceEntry[];
  /** How many factors the run scored in total, before the top-N cut. */
  importance_total: number;
}

/** The training run whose output file the predictions were read from. */
export interface PredictionSource {
  run_id: string;
  path: string;
  finished_at: string | null;
}

/** One stock's score for one date. */
export interface PredictionRow {
  ts_code: string;
  score: number;
}

export interface ModelPredictions {
  /** False until a training run has written a predictions file — not an error. */
  available: boolean;
  source: PredictionSource | null;
  /** Absent when `available` is false: the server returns a bare empty shape. */
  as_of?: string | null;
  top_n?: number;
  total_scored?: number;
  /** Every date the prediction file holds, oldest first. */
  available_dates: string[];
  picks: PredictionRow[];
  scores: PredictionRow[];
}

export const modelsApi = {
  /** A page of training history, newest first. */
  listRuns(params: { limit: number; offset: number }) {
    const q = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    return request<ModelRunPage>(`/models/runs?${q}`);
  },

  /** One run's evaluation. `importanceTopN` cuts the importance list server-side. */
  evaluation(runId: string, params: { importanceTopN?: number } = {}) {
    const q = new URLSearchParams();
    if (params.importanceTopN !== undefined) q.set('importance_top_n', String(params.importanceTopN));
    const query = q.toString();
    return request<ModelEvaluation>(`/models/runs/${encodeURIComponent(runId)}${query ? `?${query}` : ''}`);
  },

  /** One date's predictions, from the newest successful training run's output. */
  predictions(params: { asOf?: string | null; topN?: number } = {}) {
    const q = new URLSearchParams();
    if (params.topN !== undefined) q.set('top_n', String(params.topN));
    if (params.asOf) q.set('as_of', params.asOf);
    const query = q.toString();
    return request<ModelPredictions>(`/models/predictions${query ? `?${query}` : ''}`);
  },
};
