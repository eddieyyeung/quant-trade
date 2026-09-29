import type { BacktestSeries } from '../../api/backtests';

/** One chart's worth of curves, all derived from a single anchor. */
export interface AnchoredCurves {
  /** Index of the first point of the visible window. */
  index: number;
  /** That point's date — the string the y-axis name quotes. */
  day: string;
  nav: (number | null)[];
  benchmark: (number | null)[];
  /** `nav − benchmark` of the arrays above, never a third rebase. */
  excess: (number | null)[];
}

/**
 * Index of the first point at or after a dataZoom `start` percentage.
 *
 * On a `type: 'time'` axis, `start`/`end` are percentages of the axis extent,
 * and that extent is a pair of timestamps (ECharts derives it from the raw data
 * via `scaleRawExtentInfo.makeNoZoom()`). The map is therefore linear in TIME,
 * not in index:
 *
 *     t     = tFirst + (start / 100) * (tLast - tFirst)
 *     index = first i with t_i >= t
 *
 * Index arithmetic — `round(start / 100 * (length - 1))` — is the bug this
 * exists to avoid: trading days are not evenly spaced, so a window opening
 * after a long holiday covers fewer points than the same *time* width
 * midweek, and over an eleven-year daily series the two drift by weeks.
 *
 * `start = 0` gives index 0 and `start = 100` gives the last point, so a
 * full-extent chart anchors exactly where dividing by the series' own first
 * value used to.
 *
 * Assumes `dates` is ascending (a bisection over time) and that the x axis
 * carries no explicit `min`/`max`, which would widen the extent the percentage
 * is measured against. Dates are not sorted here: sorting would desync the
 * parallel value arrays the caller holds.
 */
export function anchorIndexAt(dates: string[], startPercent: number): number {
  const count = dates.length;
  if (count === 0) return 0;
  const times = dates.map(day => Date.parse(day));
  const clamped = Math.min(Math.max(startPercent, 0), 100);
  const target = times[0] + (clamped / 100) * (times[count - 1] - times[0]);
  let low = 0;
  let high = count - 1;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (times[mid] < target) low = mid + 1;
    else high = mid;
  }
  return low;
}

/** First usable divisor at or after `index` — a gap is skipped, not a zero. */
function baseAt(values: (number | null)[], index: number): number | null {
  for (let i = index; i < values.length; i += 1) {
    const value = values[i];
    // `!= null` also catches `undefined` from a ragged array, which the older
    // `value !== null && value !== 0` let through as an undefined divisor.
    if (value != null && value !== 0) return value;
  }
  return null;
}

function scaled(values: (number | null)[], base: number | null): (number | null)[] {
  if (base === null) return values.map(() => null);
  return values.map(value => (value == null ? null : value / base));
}

/**
 * Re-anchor a run's curves on the first point of the window the user is
 * looking at.
 *
 * Strategy NAV is the account's absolute value and the benchmark is a price
 * index, so they cannot share a y-axis as they arrive (design D12) — each is
 * divided by its own value at the anchor. Anchoring on the window rather than
 * on inception is what keeps a zoomed chart readable: the hole a strategy dug
 * in 2018-2019 must not decide how it reads in 2021-2023.
 *
 * The excess is the difference of the two arrays actually plotted, so the two
 * charts cannot disagree about the same run.
 *
 * Caller's precondition: `dates` is non-empty and as long as the two value
 * arrays. The page holds it with `hasSeries`, so an empty series never reaches
 * here; `anchorIndexAt` returning 0 for an empty list would otherwise put
 * `undefined` in the axis name.
 */
export function anchorCurves(
  series: Pick<BacktestSeries, 'dates' | 'nav' | 'benchmark'>,
  startPercent: number,
): AnchoredCurves {
  const index = anchorIndexAt(series.dates, startPercent);
  const nav = scaled(series.nav, baseAt(series.nav, index));
  const benchmark = scaled(series.benchmark, baseAt(series.benchmark, index));
  const excess = series.dates.map((_day, i) => {
    const strategy = nav[i];
    const baseline = benchmark[i];
    return strategy == null || baseline == null ? null : strategy - baseline;
  });
  return { index, day: series.dates[index], nav, benchmark, excess };
}
