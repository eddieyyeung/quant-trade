import { request } from './http';

/** Headline metrics the run list shows per row. */
export type BacktestMetrics = Record<string, number>;

/** One row of the backtest run list. */
export interface BacktestRunSummary {
  run_id: string;
  status: string;
  strategy: string | null;
  /** First day the NAV series covers — not the submitted start date. */
  start: string | null;
  end: string | null;
  nav_points: number;
  /** How far the run has got, so a running row can show a progress bar. */
  progress: number;
  created_at: string | null;
  finished_at: string | null;
  metrics: BacktestMetrics;
}

export interface BacktestRunPage {
  total: number;
  offset: number;
  limit: number;
  items: BacktestRunSummary[];
}

/** One run's curves: parallel lists, gaps kept as null. */
export interface BacktestSeries {
  run_id: string;
  dates: string[];
  nav: number[];
  benchmark: (number | null)[];
  drawdown: (number | null)[];
}

export interface BacktestPosition {
  ts_code: string;
  shares: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  /** Share of the invested book, not of the whole account. */
  weight: number;
}

export interface BacktestDetail {
  run_id: string;
  status: string;
  strategy: string | null;
  start: string | null;
  end: string | null;
  created_at: string | null;
  finished_at: string | null;
  metrics: BacktestMetrics;
  cash: number | null;
  total_value: number | null;
  series: BacktestSeries | null;
  positions: BacktestPosition[];
}

export interface BacktestTrade {
  seq: number;
  trade_date: string;
  action: string;
  ts_code: string;
  shares: number;
  price: number;
  commission: number;
  stamp_duty: number;
  transfer_fee: number;
}

export interface BacktestTradePage {
  run_id: string;
  total: number;
  offset: number;
  limit: number;
  items: BacktestTrade[];
}

export interface BacktestComparisonEntry {
  run_id: string;
  status: string;
  strategy: string | null;
  start: string | null;
  end: string | null;
  metrics: BacktestMetrics;
  series: BacktestSeries | null;
}

export interface BacktestComparison {
  runs: BacktestComparisonEntry[];
  /** Requested run ids that have no persisted results. */
  missing: string[];
}

export const backtestsApi = {
  list(params: { limit: number; offset: number }) {
    const q = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    return request<BacktestRunPage>(`/backtests?${q}`);
  },

  detail(runId: string) {
    return request<BacktestDetail>(`/backtests/${encodeURIComponent(runId)}`);
  },

  trades(runId: string, params: { limit: number; offset: number }) {
    const q = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    return request<BacktestTradePage>(`/backtests/${encodeURIComponent(runId)}/trades?${q}`);
  },

  /** Repeated `runs` params, matching the list query param on the route. */
  compare(runIds: string[]) {
    const q = new URLSearchParams();
    for (const runId of runIds) q.append('runs', runId);
    return request<BacktestComparison>(`/backtests/compare?${q}`);
  },

  strategies() {
    return request<{ items: string[]; default: string }>('/backtests/strategies');
  },
};
