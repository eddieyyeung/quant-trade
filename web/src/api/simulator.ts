import { request } from './http';

/**
 * The simulator's client.
 *
 * Built on the platform's shared `request`, so base resolution and error
 * normalisation are the same here as everywhere else. The simulator backend is
 * its own FastAPI app mounted into the platform, but its paths are the
 * platform's `/api/sessions*`, so nothing here needs a second base.
 *
 * Unit convention throughout: every `*_pct` field is a fraction (0.10 = 10%),
 * and the pages multiply by 100 to display it. Prices and values are raw yuan.
 */

export interface SessionSummary {
  id: string;
  name: string;
  start_date: string;
  end_date: string | null;
  cursor_date: string;
  reference_strategy: string | null;
  status: 'active' | 'paused' | 'completed';
  created_at: string;
  updated_at: string;
}

export interface MarketOverview {
  benchmark_close: number;
  benchmark_weekly_return: number;
}

export interface PortfolioItem {
  ts_code: string;
  shares: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  pnl_pct: number;
  weight_pct: number;
}

export interface FactorRankItem {
  rank: number;
  ts_code: string;
  composite_score: number;
}

export interface StrategySignalItem {
  ts_code: string;
  target_pct: number;
  direction: 'BUY' | 'SELL';
  reason: string;
}

export interface Snapshot {
  signal_date: string;
  exec_date: string;
  week_number: number;
  total_weeks: number;
  total_value: number;
  cash: number;
  market: MarketOverview | null;
  portfolio: PortfolioItem[];
  factor_ranking: FactorRankItem[];
  /**
   * `null` and `[]` mean different things and the page must keep them apart:
   * `null` is "no reference strategy was configured", `[]` is "the strategy was
   * configured and emitted nothing this week".
   */
  strategy_signals: StrategySignalItem[] | null;
  /**
   * `strategy_signals` reshaped into orders the step endpoint accepts, built
   * server-side so the signal-to-order rules live with the domain. Same
   * `null` / `[]` distinction as the signals above.
   */
  recommended_orders: OrderRequest[] | null;
  /** Which strategy the recommendation came from, for labelling the adoption. */
  recommendation_source: string | null;
  data_warnings: string[];
}

export interface SessionDetail {
  session_id: string;
  cursor_date: string;
  week_number: number;
  total_weeks: number;
  portfolio_value: number;
  previous_decisions: number;
  snapshot: Snapshot;
}

export interface CreateSessionResult {
  session_id: string;
  cursor_date: string;
  total_weeks: number;
  snapshot: Snapshot;
}

export interface ExecutedOrder {
  ts_code: string;
  direction: 'BUY' | 'SELL';
  shares: number;
  price: number;
  reason: string;
}

export interface StepResult {
  cursor_advanced: boolean;
  next_cursor_date: string;
  portfolio_total_value: number;
  portfolio_cash: number;
  holding_count: number;
  decision_number: number;
  executed_orders: ExecutedOrder[];
  warnings: string[];
}

export interface SkipResult {
  cursor_advanced: boolean;
  next_cursor_date: string;
  portfolio_total_value: number;
}

/**
 * How a week's decision lined up with the recommendation for it.
 *
 * The two *target portfolios* are compared, not the orders and not the
 * resulting holdings: the same portfolio can be reached by different orders,
 * and an order-level comparison would call "bought it in two lots" a deviation.
 */
export interface WeeklyDeviation {
  followed: boolean;
  /** Recommended, and not taken. */
  dropped: string[];
  /** Taken, and not recommended. */
  added: string[];
}

export interface WeeklyDiff {
  week_number: number;
  cursor_date: string;
  /**
   * `null` is "there was no recommendation this week" — no reference strategy,
   * or one that produced no signals. That is not the same as following it, so
   * it must not be rendered as a match.
   */
  deviation: WeeklyDeviation | null;
  concentration_warning: string | null;
  drawdown_warning: string | null;
}

export interface ComparisonMetrics {
  total_return: number;
  annual_return: number;
  annual_volatility: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate: number;
}

export interface ComparisonResult {
  weeks_completed: number;
  nav_manual: { trade_date: string; nav: number }[];
  nav_strategy: { trade_date: string; nav: number }[] | null;
  nav_benchmark: { trade_date: string; nav: number }[] | null;
  metrics: Record<string, ComparisonMetrics>;
  weekly_diffs: WeeklyDiff[];
  /** Why the strategy line is missing, when it is. Shown, never swallowed. */
  strategy_error: string | null;
  html_path: string | null;
}

export interface OrderRequest {
  ts_code: string;
  target_pct: number;
  direction: 'BUY' | 'SELL';
  /**
   * Why the order exists. Optional — the backend falls back to a generic
   * reason — but a recommended order carries the strategy's own rationale, and
   * it is what makes the fill receipt readable.
   */
  reason?: string;
}

export interface CreateSessionParams {
  name: string;
  start_date: string;
  end_date?: string;
  capital?: number;
  ref?: string;
}

export const simulatorApi = {
  listSessions(status?: string) {
    const q = status ? `?status=${encodeURIComponent(status)}` : '';
    return request<SessionSummary[]>(`/sessions${q}`);
  },

  /**
   * Create a session.
   *
   * The parameters go in the query string, not a body — that is the shape the
   * simulator's endpoint declares, so this mirrors it rather than forcing it.
   * Unset fields are dropped so the backend decides their defaults.
   */
  createSession(params: CreateSessionParams) {
    const q = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== '') q.set(key, String(value));
    }
    return request<CreateSessionResult>(`/sessions?${q}`, { method: 'POST' });
  },

  getSession(sessionId: string) {
    return request<SessionDetail>(`/sessions/${encodeURIComponent(sessionId)}`);
  },

  step(sessionId: string, orders: OrderRequest[], notes = '') {
    return request<StepResult>(`/sessions/${encodeURIComponent(sessionId)}/step`, {
      method: 'POST',
      body: JSON.stringify({ orders, notes }),
    });
  },

  skip(sessionId: string) {
    return request<SkipResult>(`/sessions/${encodeURIComponent(sessionId)}/skip`, { method: 'POST' });
  },

  compare(sessionId: string) {
    return request<ComparisonResult>(`/sessions/${encodeURIComponent(sessionId)}/compare`);
  },

  deleteSession(sessionId: string) {
    return request<{ deleted: string }>(`/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
  },
};
