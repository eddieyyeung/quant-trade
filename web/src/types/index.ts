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
  strategy_signals: StrategySignalItem[] | null;
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

export interface WeeklyDiff {
  week_number: number;
  cursor_date: string;
  user_only: string[];
  strategy_only: string[];
  common: string[];
  drawdown_warning: string | null;
}

export interface Metrics {
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
  metrics: Record<string, Metrics>;
  weekly_diffs: WeeklyDiff[];
  html_path: string | null;
}

export interface OrderRequest {
  ts_code: string;
  target_pct: number;
  direction: 'BUY' | 'SELL';
}
