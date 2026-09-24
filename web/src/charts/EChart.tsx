import type { EChartsOption } from 'echarts';
// The `esm/` entry, not `lib/core`: `lib/` is CJS with an `exports.default`,
// and a deep import of it skips the bundler's interop, so React receives a
// module namespace object instead of a component (React error #130).
import ReactEChartsCore from 'echarts-for-react/esm/core';

import { echarts } from './echarts';

interface Props {
  option: EChartsOption;
  /** Chart height in pixels. Charts in this platform are fixed-height blocks. */
  height?: number;
  /** When true nothing renders — the page shows its own empty state instead. */
  empty?: boolean;
  'aria-label'?: string;
}

/**
 * The one way charts are drawn in this platform.
 *
 * A thin wrapper over `echarts-for-react`'s core entry so every chart shares the
 * same on-demand registration, height convention and merge behaviour
 * (`notMerge`: a shorter series must not leave the previous option's traces on
 * screen).
 */
export default function EChart({ option, height = 360, empty = false, ...rest }: Props) {
  if (empty) return null;
  return (
    <ReactEChartsCore
      echarts={echarts}
      option={option}
      notMerge
      lazyUpdate
      style={{ height, width: '100%' }}
      {...rest}
    />
  );
}
