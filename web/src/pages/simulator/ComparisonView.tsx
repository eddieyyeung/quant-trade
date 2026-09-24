import { useCallback, useEffect, useState } from 'react';
import { App as AntdApp, Alert, Button, Card, Col, Flex, Row, Table, Tag, Typography } from 'antd';
import type { TableProps } from 'antd';
import type { EChartsOption } from 'echarts';

import { simulatorApi, type ComparisonMetrics, type ComparisonResult, type WeeklyDiff } from '../../api/simulator';
import EChart from '../../charts/EChart';
import { formatPercent } from '../../utils/format';

/** The three curves, in draw order. */
const CURVES = [
  { key: 'nav_manual', label: '手动', color: '#5B8FF9', width: 2 },
  { key: 'nav_strategy', label: '策略', color: '#61DDAA', width: 1.5 },
  { key: 'nav_benchmark', label: '基准', color: '#8c8c8c', width: 1 },
] as const;

const SUBJECT_LABELS: Record<string, string> = { manual: '手动', strategy: '策略', benchmark: '基准' };

const METRIC_ROWS: { key: keyof ComparisonMetrics; label: string; percent: boolean; colored: boolean }[] = [
  { key: 'total_return', label: '累计收益', percent: true, colored: true },
  { key: 'annual_return', label: '年化收益', percent: true, colored: true },
  { key: 'sharpe_ratio', label: '夏普比率', percent: false, colored: true },
  // Drawdown is a magnitude, not a gain or a loss, so it carries no sign
  // colouring — painting it red would say "this went wrong" rather than
  // "this is how deep the worst dip was".
  { key: 'max_drawdown', label: '最大回撤', percent: true, colored: false },
  { key: 'win_rate', label: '周胜率', percent: true, colored: true },
];

/**
 * The three NAV curves on one axis.
 *
 * No rebasing: the engine already normalises every series to 1.0 at the
 * session start, so they are comparable as they arrive. Dividing by the first
 * value again — as the backtest pages must, because their benchmark is an
 * index while their strategy NAV is an absolute account value — would scale
 * these curves a second time for no reason.
 */
function navOption(data: ComparisonResult): EChartsOption {
  const dates = new Set<string>();
  const byDate = new Map<string, Map<string, number>>();

  for (const curve of CURVES) {
    const series = data[curve.key];
    const lookup = new Map<string, number>();
    for (const point of series ?? []) {
      dates.add(point.trade_date);
      lookup.set(point.trade_date, point.nav);
    }
    byDate.set(curve.key, lookup);
  }

  const axis = [...dates].sort();
  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, type: 'scroll' },
    grid: { left: 56, right: 24, top: 40, bottom: 56 },
    xAxis: { type: 'category', data: axis, boundaryGap: false },
    yAxis: { type: 'value', scale: true, name: '净值（期初 = 1）' },
    dataZoom: [
      { type: 'inside' },
      { type: 'slider', height: 20, bottom: 8 },
    ],
    series: CURVES.filter(curve => (data[curve.key]?.length ?? 0) > 0).map(curve => ({
      name: curve.label,
      type: 'line' as const,
      showSymbol: false,
      // Gaps stay gaps: a missing day is not a zero, and connecting across it
      // would invent a value the session never had.
      connectNulls: false,
      color: curve.color,
      lineStyle: { width: curve.width, ...(curve.key === 'nav_benchmark' ? { type: 'dashed' as const } : {}) },
      data: axis.map(day => byDate.get(curve.key)?.get(day) ?? null),
    })),
  };
}

/** A `string[]` cell. Empty is shown, not left blank — an empty cell reads as missing data. */
function codeList(codes: string[]): string {
  return codes.join('、') || '—';
}

const diffColumns: TableProps<WeeklyDiff>['columns'] = [
  { title: '周', dataIndex: 'week_number', key: 'week_number', width: 70 },
  { title: '日期', dataIndex: 'cursor_date', key: 'cursor_date', width: 120 },
  {
    title: '采纳',
    key: 'followed',
    width: 110,
    render: (_value, row) => {
      if (row.deviation === null) return <Typography.Text type="secondary">无参考策略</Typography.Text>;
      return row.deviation.followed ? <Tag color="success">完全跟随</Tag> : <Tag color="warning">有偏离</Tag>;
    },
  },
  {
    title: '你剔除',
    key: 'dropped',
    render: (_value, row) => (row.deviation === null ? '—' : codeList(row.deviation.dropped)),
  },
  {
    title: '你额外加',
    key: 'added',
    render: (_value, row) => (row.deviation === null ? '—' : codeList(row.deviation.added)),
  },
  {
    title: '警告',
    key: 'warning',
    width: 200,
    render: (_value, row) => row.drawdown_warning ?? row.concentration_warning ?? '—',
  },
];

export default function ComparisonView({ sessionId }: { sessionId: string }) {
  const { message } = AntdApp.useApp();

  const [data, setData] = useState<ComparisonResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await simulatorApi.compare(sessionId));
      setFailure(null);
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      setFailure(text);
      message.error(text);
    } finally {
      setLoading(false);
    }
  }, [message, sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (failure !== null && data === null) {
    return (
      <Alert
        type="error"
        showIcon
        message="对比报告加载失败"
        description={failure}
        action={
          <Button size="small" onClick={() => void load()}>
            重试
          </Button>
        }
      />
    );
  }

  if (loading && data === null) {
    return <Card loading />;
  }

  if (data === null) return null;

  const hasCurves = CURVES.some(curve => (data[curve.key]?.length ?? 0) > 0);

  return (
    <Flex vertical gap={16}>
      <Card size="small" title={`净值曲线（已完成 ${data.weeks_completed} 周）`}>
        {/* A missing strategy line has to say why. Rendering two curves and
            nothing else is how the date-type bug stayed invisible: the page
            looked like "no strategy configured" either way. */}
        {data.strategy_error !== null && (
          <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="策略净值线不可用" description={data.strategy_error} />
        )}
        {hasCurves ? (
          <EChart option={navOption(data)} height={360} aria-label="手动、参考策略与基准的净值曲线" />
        ) : (
          <Alert type="info" showIcon message="尚无净值数据" description="该会话还没有推进过任何一周，因此没有可比对的净值序列。" />
        )}
      </Card>

      <Card size="small" title="关键指标">
        <Row gutter={[16, 16]}>
          {Object.entries(data.metrics).map(([subject, metrics]) => (
            <Col key={subject} xs={24} md={12} lg={8}>
              <Card size="small" title={SUBJECT_LABELS[subject] ?? subject}>
                {METRIC_ROWS.map(row => {
                  const value = metrics[row.key];
                  const shown = typeof value === 'number' ? (row.percent ? formatPercent(value, 2) : value.toFixed(2)) : '—';
                  const color =
                    row.colored && typeof value === 'number' ? (value >= 0 ? '#3f8600' : '#cf1322') : undefined;
                  return (
                    <Flex key={row.key} justify="space-between" style={{ fontSize: 13, margin: '4px 0' }}>
                      <Typography.Text type="secondary">{row.label}</Typography.Text>
                      <Typography.Text strong style={{ color }}>
                        {shown}
                      </Typography.Text>
                    </Flex>
                  );
                })}
              </Card>
            </Col>
          ))}
        </Row>
      </Card>

      <Card
        size="small"
        title="逐周决策差异"
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            比较的是当周的目标组合（想买什么），不是期末持仓
          </Typography.Text>
        }
      >
        {data.weekly_diffs.length > 0 ? (
          <Table<WeeklyDiff>
            rowKey="week_number"
            columns={diffColumns}
            dataSource={data.weekly_diffs}
            size="small"
            pagination={false}
            scroll={{ x: 860 }}
          />
        ) : (
          <Alert type="info" showIcon message="尚无决策差异" description="该会话还没有完成任何一周的决策。" />
        )}
      </Card>

      {data.html_path && (
        <Card size="small" title="对比报告文件">
          {/* Shown, not linked: the file lives on the server's filesystem, and
              the platform serves no route for it. */}
          <Typography.Text code>{data.html_path}</Typography.Text>
        </Card>
      )}
    </Flex>
  );
}
