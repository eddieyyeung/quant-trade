// On-demand ECharts registration.
//
// Importing the full `echarts` package would pull every chart type and
// component into the main bundle; this file registers only what the platform
// draws — lines (NAV, IC series), bars (decay), a heatmap (correlation) and a
// pie (backtest position weights). Add to `echarts.use` rather than importing
// `echarts` directly anywhere else.
import { BarChart, HeatmapChart, LineChart, PieChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([
  LineChart,
  BarChart,
  HeatmapChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  VisualMapComponent,
  DataZoomComponent,
  CanvasRenderer,
]);

export { echarts };
