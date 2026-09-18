// Dev: the Vite proxy forwards /api to http://localhost:9555.
// Production: the platform serves the bundle itself, so a relative base works.
const BASE = import.meta.env.VITE_API_BASE || '/api';

/** An error response from the platform API. */
export class ApiError extends Error {
  /** HTTP status, or 0 when the request never reached the server. */
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** Absolute (site-relative) URL for an API path, for `EventSource` and friends. */
export function apiUrl(path: string): string {
  return `${BASE}${path}`;
}

/** Render whatever the API put in `detail` as one readable line. */
function describe(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc: [...], msg: "...", type: "..."}]
    const parts = detail.map(entry => {
      if (entry && typeof entry === 'object' && 'msg' in entry) {
        const e = entry as { loc?: unknown[]; msg?: unknown };
        const where = Array.isArray(e.loc) ? e.loc.filter(p => p !== 'body' && p !== 'params').join('.') : '';
        return where ? `${where}: ${String(e.msg)}` : String(e.msg);
      }
      return JSON.stringify(entry);
    });
    if (parts.length) return parts.join('；');
  }
  return fallback;
}

/** Issue a JSON request and unwrap the response, or throw `ApiError`. */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    });
  } catch {
    throw new ApiError(`无法连接到后端 (${BASE})，请确认服务已启动`, 0);
  }

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = undefined;
    }
    throw new ApiError(describe(detail, `HTTP ${response.status} ${response.statusText}`), response.status);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
