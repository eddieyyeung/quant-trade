import type { OrderRequest } from '../../api/simulator';

/**
 * Turn the two decision inputs into orders.
 *
 * The input grammar is carried over unchanged from the standalone simulator:
 * buys are comma-separated `CODE:PCT` pairs whose percentage is divided by 100
 * (the backend's `target_pct` is a fraction), sells are a bare comma-separated
 * code list expanded to zero-weight sells, and buys are emitted before sells.
 * The ordering is not cosmetic — the engine reads cash as it fills, so sells
 * landing first would change what the buys can afford.
 *
 * A malformed pair is skipped rather than failing the whole submission: typing
 * `600519.SH:10, 002594.SZ` should place the order it can, not reject both.
 *
 * Kept apart from the form component because it is pure logic with no React in
 * it, and because a module that exports both a component and a plain function
 * cannot be fast-refreshed.
 */
export function parseOrders(buyInput: string, sellInput: string): OrderRequest[] {
  const orders: OrderRequest[] = [];

  if (buyInput.trim()) {
    for (const part of buyInput.split(',')) {
      // First two segments only, as before: anything past a second colon is
      // ignored rather than reinterpreted.
      const [code, pct] = part.trim().split(':');
      // A non-finite weight covers both a missing percentage and a non-numeric
      // one; either would serialise to `null` and fail server-side, so an
      // unusable pair is skipped instead of failing the whole submission.
      const weight = Number(pct) / 100;
      if (!code || !pct || !Number.isFinite(weight)) continue;
      orders.push({ ts_code: code.trim(), target_pct: weight, direction: 'BUY' });
    }
  }

  if (sellInput.trim()) {
    for (const part of sellInput.split(',')) {
      const code = part.trim();
      if (code) orders.push({ ts_code: code, target_pct: 0, direction: 'SELL' });
    }
  }

  return orders;
}
