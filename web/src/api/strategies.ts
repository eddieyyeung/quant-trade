import { request } from './http';

/** The registered strategies, plus the one the server config selects. */
export interface StrategyRegistry {
  items: string[];
  default: string;
}

/** One signal-generation run as the history list renders it. */
export interface StrategyRunSummary {
  run_id: string;
  status: string;
  created_at: string | null;
  finished_at: string | null;
  progress: number;
  /** Absent until the run has written its signals — never 0. */
  signal_date: string | null;
  strategy: string | null;
  order_count: number | null;
  universe_size: number | null;
}

export interface StrategyRunPage {
  total: number;
  offset: number;
  limit: number;
  items: StrategyRunSummary[];
}

/** One order in a generated signal set, in the order the engine emitted it. */
export interface StrategySignalOrder {
  seq: number;
  ts_code: string;
  direction: string;
  /** A fraction of the book (0.10 = 10%), not a percentage. */
  target_pct: number;
  reason: string;
}

export interface StrategySignalPage {
  run_id: string;
  /** How the run ended — the signal rows cannot say, they only hold orders. */
  status: string;
  finished_at: string | null;
  strategy: string | null;
  signal_date: string | null;
  universe_size: number | null;
  total: number;
  offset: number;
  limit: number;
  orders: StrategySignalOrder[];
}

export const strategiesApi = {
  /** Registered strategy names, for the submit form's selector. */
  list() {
    return request<StrategyRegistry>('/strategies');
  },

  runs(params: { limit: number; offset: number }) {
    const q = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    return request<StrategyRunPage>(`/strategies/runs?${q}`);
  },

  signals(runId: string, params: { limit: number; offset: number }) {
    const q = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    return request<StrategySignalPage>(`/strategies/runs/${encodeURIComponent(runId)}?${q}`);
  },
};
