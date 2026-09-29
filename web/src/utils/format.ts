import dayjs from 'dayjs';

const DATE_TIME = 'YYYY-MM-DD HH:mm:ss';

/** An API timestamp as a local date-time, or an em dash when absent. */
export function formatTime(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format(DATE_TIME) : value;
}

/** A date-only value as `YYYY-MM-DD`. */
export function formatDate(value: string | null | undefined): string {
  return value ? dayjs(value).format('YYYY-MM-DD') : '—';
}

/** Whole-number formatting for row counts, with thousands separators. */
export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return value.toLocaleString('zh-CN');
}

/** A `[0, 1]` ratio as a percentage string. */
export function formatPercent(ratio: number | null | undefined, digits = 1): string {
  if (ratio === null || ratio === undefined) return '—';
  return `${(ratio * 100).toFixed(digits)}%`;
}
