import { request } from './http';

/** Row count and date span of one market data table. */
export interface TableStat {
  table: string;
  rows: number;
  earliest: string | null;
  latest: string | null;
}

export interface DataStatus {
  db_path: string;
  latest_trade_date: string | null;
  universe_size: number;
  tables: TableStat[];
}

/** How much of a universe has synced kline data. */
export interface Coverage {
  as_of: string;
  universe_size: number;
  covered: number;
  ratio: number;
  missing_total: number;
  missing: string[];
  missing_offset: number;
  missing_limit: number;
}

/** One day of the trade calendar. */
export interface CalendarDay {
  trade_date: string;
  is_open: boolean;
}

export interface TradeCalendarView {
  start: string;
  end: string;
  open_days: number;
  closed_days: number;
  days: CalendarDay[];
}

export const dataApi = {
  status() {
    return request<DataStatus>('/data/status');
  },

  /** The missing-code list is paged; a fresh database can be missing everything. */
  coverage(limit: number, offset: number) {
    const q = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    return request<Coverage>(`/data/coverage?${q}`);
  },

  calendar(start: string, end: string) {
    const q = new URLSearchParams({ start, end });
    return request<TradeCalendarView>(`/data/calendar?${q}`);
  },
};
