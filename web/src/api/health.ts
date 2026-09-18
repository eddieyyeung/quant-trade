import { useEffect, useState } from 'react';

import { request } from './http';

export interface Health {
  status: string;
  db_path: string;
  db_reachable: boolean;
  detail: string;
}

export type HealthState =
  | { kind: 'checking' }
  | { kind: 'ok'; health: Health }
  | { kind: 'unreachable'; message: string };

const POLL_MS = 30_000;

/** Track platform reachability, re-checking periodically. */
export function useHealth(): HealthState {
  const [state, setState] = useState<HealthState>({ kind: 'checking' });

  useEffect(() => {
    let cancelled = false;

    const probe = async () => {
      try {
        const health = await request<Health>('/health');
        if (!cancelled) setState({ kind: 'ok', health });
      } catch (e) {
        if (!cancelled) setState({ kind: 'unreachable', message: e instanceof Error ? e.message : String(e) });
      }
    };

    void probe();
    const timer = window.setInterval(probe, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return state;
}
