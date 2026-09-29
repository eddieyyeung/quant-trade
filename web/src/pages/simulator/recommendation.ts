import type { OrderRequest } from '../../api/simulator';

/**
 * The inverse of `parseOrders`.
 *
 * Recommended orders arrive as structured orders, but the form's inputs are
 * text. Writing them back out in the form's own grammar is what makes "adopt
 * the recommendation, then drop the names you disagree with" a first-class
 * path — which matters, because following the strategy every single week makes
 * the manual line track the strategy line and the per-week diff stops saying
 * anything.
 *
 * Percentages are rounded to two decimals: the input grammar is `CODE:PCT`, and
 * `600519.SH:6.666666666666667` is unreadable in a text field. The rounding is
 * far below the 100-share lot the engine rounds to anyway.
 *
 * Kept apart from the components because it is pure logic with no React in it,
 * and because a module exporting a component alongside a plain function cannot
 * be fast-refreshed.
 */
export function toFormInputs(orders: OrderRequest[]): { buy: string; sell: string } {
  const buys: string[] = [];
  const sells: string[] = [];

  for (const order of orders) {
    if (order.direction === 'SELL') {
      // The sell input is a bare code list — the percentage is implied by the
      // grammar, which expands each code to a zero-weight sell.
      sells.push(order.ts_code);
      continue;
    }
    if (order.target_pct <= 0) continue;
    const percent = Math.round(order.target_pct * 10000) / 100;
    buys.push(`${order.ts_code}:${percent}`);
  }

  return { buy: buys.join(', '), sell: sells.join(', ') };
}
