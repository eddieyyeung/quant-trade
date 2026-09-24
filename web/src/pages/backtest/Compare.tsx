import { useCallback, useEffect, useState } from 'react';
import { App as AntdApp, Alert, Button, Card, Flex, Select, Table, Tag, Tooltip, Typography } from 'antd';
import type { TableProps } from 'antd';
import type { EChartsOption } from 'echarts';
import { useSearchParams } from 'react-router-dom';

import { backtestsApi, type BacktestComparison, type BacktestComparisonEntry } from '../../api/backtests';
import { STATUS_COLORS, STATUS_LABELS, type RunStatus } from '../../api/runs';
import EChart from '../../charts/EChart';
import { formatDate, formatPercent } from '../../utils/format';
import BacktestNav from './BacktestNav';

/** Mirrors `MAX_COMPARISON_RUNS` on the server; the cap is a contract there. */
const MAX_COMPARISON_RUNS = 8;
const PALETTE = ['#5B8FF9', '#61DDAA', '#F6BD16', '#7262FD', '#78D3F8', '#9661BC', '#F6903D', '#008685'];

/**
 * Rebase a curve on its first point, so every run reads as "growth of 1".
 *
 * NAV is the account's absolute value, so a run started with 200k would sit
 * twice as high as one started with 100k without being any better. Growth is
 * the comparable quantity.
 */
function rebase(values: number[]): (number | null)[] {
  const base = values.find(value => value !== 0);
  if (base === undefined) return values;
  return values.map(value => value / base);
}

function overlayOption(runs: BacktestComparisonEntry[]): EChartsOption {
  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, type: 'scroll' },
    grid: { left: 56, right: 24, top: 48, bottom: 56 },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', scale: true, name: '净值（期初 = 1）' },
    dataZoom: [
      { type: 'inside' },
      { type: 'slider', height: 20, bottom: 8 },
    ],
    series: runs.map((run, index) => {
      const nav = rebase(run.series?.nav ?? []);
      return {
        name: run.run_id,
        type: 'line' as const,
        showSymbol: false,
        connectNulls: false,
        color: PALETTE[index % PALETTE.length],
        data: (run.series?.dates ?? []).map((day, i) => [day, nav[i] ?? null]),
      };
    }),
  };
}

export default function Compare() {
  const { message } = AntdApp.useApp();
  const [searchParams] = useSearchParams();

  const [options, setOptions] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>(() => {
    const initial = searchParams.get('runs');
    return initial ? initial.split(',').filter(Boolean) : [];
  });
  const [result, setResult] = useState<BacktestComparison | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    backtestsApi
      .list({ limit: 200, offset: 0 })
      .then(body => setOptions(body.items.map(row => row.run_id)))
      .catch((e: unknown) => message.error(e instanceof Error ? e.message : String(e)));
  }, [message]);

  const compare = useCallback(
    async (runIds: string[]) => {
      if (runIds.length === 0) {
        message.warning('请先选择至少一个回测运行');
        return;
      }
      if (runIds.length > MAX_COMPARISON_RUNS) {
        message.warning(`最多对比 ${MAX_COMPARISON_RUNS} 个运行，当前选中 ${runIds.length} 个`);
        return;
      }
      setLoading(true);
      try {
        setResult(await backtestsApi.compare(runIds));
      } catch (e) {
        message.error(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [message],
  );

  // An explicit trigger, not an effect: a comparison reads several curves at
  // once, and the page must not spend that on every visit.
  const followed = searchParams.get('runs') ?? '';
  useEffect(() => {
    const initial = followed.split(',').filter(Boolean);
    if (initial.length > 0) void compare(initial);
  }, [compare, followed]);

  const columns: TableProps<BacktestComparisonEntry>['columns'] = [
    {
      title: '运行',
      dataIndex: 'run_id',
      key: 'run_id',
      width: 220,
      render: (runId: string) => <Typography.Text copyable>{runId}</Typography.Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (status: string) => {
        const tag = (
          <Tag color={STATUS_COLORS[status as RunStatus] ?? 'default'}>
            {STATUS_LABELS[status as RunStatus] ?? status}
          </Tag>
        );
        // A cancelled curve stops early, so its metrics were computed over a
        // different window than its neighbours'. Say so on the row itself —
        // a warning above the table is easy to scroll past.
        return status === 'cancelled' ? (
          <Tooltip title="曲线止于取消日，覆盖交易日短于其他运行，指标不具可比性">{tag}</Tooltip>
        ) : (
          tag
        );
      },
    },
    {
      title: '策略',
      dataIndex: 'strategy',
      key: 'strategy',
      width: 150,
      render: (value: string | null) => value || '—',
    },
    {
      title: '实际区间',
      key: 'window',
      width: 200,
      render: (_, row) => `${formatDate(row.start)} ~ ${formatDate(row.end)}`,
    },
    {
      title: '覆盖交易日',
      key: 'covered_days',
      width: 110,
      render: (_, row) => row.series?.dates.length ?? 0,
    },
    { title: '总收益', key: 'total_return', width: 110, render: (_, row) => formatPercent(row.metrics.total_return) },
    { title: '年化', key: 'annual_return', width: 110, render: (_, row) => formatPercent(row.metrics.annual_return) },
    {
      title: '最大回撤',
      key: 'max_drawdown',
      width: 110,
      render: (_, row) => formatPercent(row.metrics.max_drawdown),
    },
    {
      title: '夏普',
      key: 'sharpe_ratio',
      width: 90,
      render: (_, row) => (row.metrics.sharpe_ratio === undefined ? '—' : row.metrics.sharpe_ratio.toFixed(2)),
    },
    {
      title: '胜率',
      key: 'win_rate',
      width: 90,
      render: (_, row) => formatPercent(row.metrics.win_rate),
    },
    {
      title: '交易次数',
      key: 'total_trades',
      width: 100,
      render: (_, row) => row.metrics.total_trades ?? '—',
    },
  ];

  const entries = result?.runs ?? [];
  const hasCancelled = entries.some(entry => entry.status === 'cancelled');

  return (
    <Flex vertical gap={16}>
      <BacktestNav />

      <Card title="回测对比">
        <Flex gap={12} wrap align="center">
          <Select
            mode="multiple"
            style={{ minWidth: 420, flex: 1 }}
            placeholder="选择要对比的回测运行"
            value={selected}
            onChange={setSelected}
            options={options.map(runId => ({ value: runId, label: runId }))}
            maxTagCount="responsive"
          />
          <Button type="primary" loading={loading} onClick={() => void compare(selected)}>
            对比
          </Button>
          <Typography.Text type="secondary">最多 {MAX_COMPARISON_RUNS} 个</Typography.Text>
        </Flex>
      </Card>

      {result && result.missing.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message="部分运行没有可对比的结果"
          description={`${result.missing.join('、')} 没有落库的净值序列，已从对比中略去。`}
        />
      )}

      {hasCancelled && (
        <Alert
          type="warning"
          showIcon
          message="对比中包含被取消的运行"
          description="覆盖区间不同，指标不具可比性：被取消的曲线止于取消日，短于其他运行。"
        />
      )}

      {result && entries.length > 0 && (
        <>
          <Card size="small" title="净值叠加">
            <EChart option={overlayOption(entries)} height={400} aria-label="多个回测的净值叠加曲线" />
          </Card>

          <Card size="small" title="指标对照">
            <Table<BacktestComparisonEntry>
              rowKey="run_id"
              columns={columns}
              dataSource={entries}
              size="small"
              pagination={false}
              scroll={{ x: 1290 }}
            />
          </Card>
        </>
      )}

      {result && entries.length === 0 && (
        <Alert
          type="info"
          showIcon
          message="没有可对比的结果"
          description="所选的运行都没有落库的净值序列。"
        />
      )}

      {!result && (
        <Card size="small">
          <Typography.Text type="secondary">选择两个以上的回测运行，点击「对比」查看净值叠加与指标对照。</Typography.Text>
        </Card>
      )}
    </Flex>
  );
}
