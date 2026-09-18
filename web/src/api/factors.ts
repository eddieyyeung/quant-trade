import { request } from './http';

/** One row of the factor catalogue. */
export interface FactorRow {
  name: string;
  category: string;
  /** True when the factor has rows in `factor_values`. */
  persisted: boolean;
  rows: number;
  earliest: string | null;
  latest: string | null;
}

export interface FactorPage {
  total: number;
  offset: number;
  limit: number;
  items: FactorRow[];
}

export interface FactorCoverage {
  name: string;
  rows: number;
  earliest: string | null;
  latest: string | null;
}

/** One day of a persisted IC series. */
export interface IcPoint {
  trade_date: string;
  ic: number | null;
  rank_ic: number | null;
  sample_size: number;
}

export interface IcSummary {
  ic_mean: number | null;
  ic_std: number | null;
  ic_ir: number | null;
  ic_positive_ratio: number | null;
}

export interface IcSeries {
  factor: string;
  forward_period: number;
  start: string | null;
  end: string | null;
  count: number;
  summary: IcSummary;
  series: IcPoint[];
}

export interface IcDecayItem {
  forward_period: number;
  ic_mean: number | null;
  ic_positive_ratio: number | null;
  count: number;
}

export interface IcDecay {
  factor: string;
  start: string | null;
  end: string | null;
  items: IcDecayItem[];
}

/** One NAV curve: parallel date/value lists, ready for a line series. */
export interface NavSeries {
  name: string;
  dates: string[];
  values: number[];
}

export interface QuantileResult {
  factor: string;
  n_groups: number;
  forward_period: number;
  rebalance_count: number;
  skipped_dates: number;
  groups: NavSeries[];
  long_short: NavSeries | null;
}

export interface CorrelationResult {
  factors: string[];
  matrix: (number | null)[][];
  date_count: number;
  skipped_dates: number;
}

interface Window {
  start?: string;
  end?: string;
}

function windowQuery(params: Window): URLSearchParams {
  const q = new URLSearchParams();
  if (params.start) q.set('start', params.start);
  if (params.end) q.set('end', params.end);
  return q;
}

export const factorsApi = {
  list(params: { category?: string; limit: number; offset: number }) {
    const q = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    if (params.category) q.set('category', params.category);
    return request<FactorPage>(`/factors?${q}`);
  },

  coverage() {
    return request<{ total: number; items: FactorCoverage[] }>('/factors/coverage');
  },

  ic(factor: string, params: Window & { forwardPeriod: number }) {
    const q = windowQuery(params);
    q.set('factor', factor);
    q.set('forward_period', String(params.forwardPeriod));
    return request<IcSeries>(`/factors/ic?${q}`);
  },

  icDecay(factor: string, params: Window) {
    const q = windowQuery(params);
    q.set('factor', factor);
    return request<IcDecay>(`/factors/ic/decay?${q}`);
  },

  quantile(params: Window & { factor: string; nGroups: number; forwardPeriod: number }) {
    const q = windowQuery(params);
    q.set('factor', params.factor);
    q.set('n_groups', String(params.nGroups));
    q.set('forward_period', String(params.forwardPeriod));
    return request<QuantileResult>(`/factors/quantile?${q}`);
  },

  /** Repeated `factors` params, matching the list query param on the route. */
  correlation(factors: string[], params: Window) {
    const q = windowQuery(params);
    for (const name of factors) q.append('factors', name);
    return request<CorrelationResult>(`/factors/correlation?${q}`);
  },
};
