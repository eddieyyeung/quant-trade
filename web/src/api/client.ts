import type {
  SessionSummary,
  CreateSessionResult,
  SessionDetail,
  StepResult,
  ComparisonResult,
  OrderRequest,
} from '../types';

// Dev mode: Vite proxy handles /api → localhost:9555
// Production: set VITE_API_BASE env or point to deployed backend
const BASE = import.meta.env.VITE_API_BASE || '/api';

async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const url = `${BASE}${path}`;
  console.log(`[API] ${opts.method || 'GET'} ${url}`, opts.body ? JSON.parse(opts.body as string) : '');

  let res: Response;
  try {
    res = await fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    });
  } catch (err) {
    console.error(`[API] 网络错误: ${url}`, err);
    throw new Error(`无法连接到后端 (${BASE})，请确认 API 服务已启动`);
  }

  if (!res.ok) {
    let detail: string;
    try {
      const j = await res.json();
      detail = j.detail || `HTTP ${res.status}`;
    } catch {
      detail = `HTTP ${res.status} ${res.statusText}`;
    }
    console.error(`[API] 错误 ${res.status}: ${url} — ${detail}`);
    throw new Error(detail);
  }

  const data = await res.json();
  console.log(`[API] 成功 ${res.status}: ${url}`);
  return data as T;
}

export const api = {
  listSessions(status?: string) {
    const q = status ? `?status=${status}` : '';
    return request<SessionSummary[]>(`/sessions${q}`);
  },

  createSession(params: {
    name: string;
    start_date: string;
    end_date?: string;
    capital?: number;
    ref?: string;
  }) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== '') q.set(k, String(v));
    }
    return request<CreateSessionResult>(`/sessions?${q}`, { method: 'POST' });
  },

  getSession(id: string) {
    return request<SessionDetail>(`/sessions/${id}`);
  },

  step(id: string, orders: OrderRequest[], notes: string = '') {
    return request<StepResult>(`/sessions/${id}/step`, {
      method: 'POST',
      body: JSON.stringify({ orders, notes }),
    });
  },

  skip(id: string) {
    return request<{ cursor_advanced: boolean; next_cursor_date: string; portfolio_total_value: number }>(
      `/sessions/${id}/skip`,
      { method: 'POST' }
    );
  },

  compare(id: string) {
    return request<ComparisonResult>(`/sessions/${id}/compare`);
  },

  deleteSession(id: string) {
    return request<{ deleted: string }>(`/sessions/${id}`, { method: 'DELETE' });
  },
};
