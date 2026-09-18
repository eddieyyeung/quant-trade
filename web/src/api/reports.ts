import { apiUrl, request } from './http';

/** One weekly report as the history list renders it. */
export interface ReportSummary {
  run_id: string;
  status: string;
  created_at: string | null;
  finished_at: string | null;
  progress: number;
  /** The date the report's signals were computed for — its data freshness. */
  signal_date: string | null;
  order_count: number | null;
  trade_count: number | null;
  /** The report file's own name, which is what a download saves as. */
  file_name: string;
}

export interface ReportPage {
  total: number;
  offset: number;
  limit: number;
  items: ReportSummary[];
}

export interface ReportDetail extends ReportSummary {
  error: string | null;
}

export const reportsApi = {
  list(params: { limit: number; offset: number }) {
    const q = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    return request<ReportPage>(`/reports?${q}`);
  },

  detail(runId: string) {
    return request<ReportDetail>(`/reports/${encodeURIComponent(runId)}`);
  },

  /**
   * The report's own URL, for `iframe src` and for the download link.
   *
   * Served as a document rather than a JSON field: the report inlines its
   * charts as base64, so handing it through JSON to reach a `srcDoc` would put
   * the whole thing in script memory twice.
   */
  htmlUrl(runId: string, options: { download?: boolean } = {}) {
    const q = options.download ? '?download=1' : '';
    return apiUrl(`/reports/${encodeURIComponent(runId)}/html${q}`);
  },
};
