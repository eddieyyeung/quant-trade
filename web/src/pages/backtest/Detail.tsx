import { useCallback, useEffect, useState } from 'react';
import { App as AntdApp, Alert, Button, Card, Col, Flex, Row, Statistic, Table, Tag, Typography } from 'antd';
import type { TableProps } from 'antd';
import type { EChartsOption } from 'echarts';
import { Link, useParams } from 'react-router-dom';

import {
  backtestsApi,
  type BacktestDetail,
  type BacktestPosition,
  type BacktestTrade,
} from '../../api/backtests';
import { STATUS_COLORS, STATUS_LABELS, type RunStatus } from '../../api/runs';
import EChart from '../../charts/EChart';
import { formatDate, formatPercent, formatTime } from '../../utils/format';
import BacktestNav from './BacktestNav';

const TRADE_PAGE_SIZE = 100;
const PIE_COLORS = ['#5B8FF9', '#61DDAA', '#F6BD16', '#7262FD', '#78D3F8', '#9661BC', '#F6903D', '#008685'];

/**
 * Rebase a series on its first value, so it reads as "growth of 1".
 *
 * The two series are not in the same units coming out of the engine: the
 * strategy's NAV is the account's absolute value (initial capital included)
 * while the benchmark is a price index already normalised to 1.0. Drawing them
 * on one axis would flatten the benchmark to a line at zero, and their
 * difference would just be the account balance.
 */
function rebase(values: (number | null)[]): (number | null)[] {
  const base = values.find(value => value !== null && value !== 0);
  if (base === undefined || base === null) return values;
  return values.map(value => (value === null ? null : value / base));
}

/** A level chart: strategy NAV against its benchmark over time. */
function levelOption(detail: BacktestDetail): EChartsOption {
  const series = detail.series;
  if (!series) return {};
  const curves = [
    { name: '策略净值', values: rebase(series.nav) },
    { name: '基准净值', values: rebase(series.benchmark) },
  ];
  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, type: 'scroll' },
    grid: { left: 56, right: 24, top: 40, bottom: 56 },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', scale: true, name: '净值（期初 = 1）' },
    // Zooming is the reason these curves are drawn with ECharts: a multi-year
    // daily series is unreadable end to end.
    dataZoom: [
      { type: 'inside' },
      { type: 'slider', height: 20, bottom: 8 },
    ],
    series: curves.map(line => ({
      name: line.name,
      type: 'line' as const,
      showSymbol: false,
      connectNulls: false,
      data: series.dates.map((day, index) => [day, line.values[index]]),
    })),
  };
}

/** Rebased strategy growth minus rebased benchmark growth. */
function excessOption(detail: BacktestDetail): EChartsOption {
  const series = detail.series;
  if (!series) return {};
  const nav = rebase(series.nav);
  const benchmark = rebase(series.benchmark);
  const excess = series.dates.map((day, index) => {
    const base = benchmark[index];
    const strategy = nav[index];
    return [day, base === null || strategy === null ? null : strategy - base];
  });
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 56, right: 24, top: 24, bottom: 32 },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', scale: true, name: '超额' },
    series: [
      {
        name: '超额收益',
        type: 'line',
        showSymbol: false,
        connectNulls: false,
        color: '#5B8FF9',
        data: excess,
        markLine: {
          silent: true,
          symbol: 'none',
          label: { show: false },
          lineStyle: { type: 'dashed', color: '#8c8c8c' },
          data: [{ yAxis: 0 }],
        },
      },
    ],
  };
}

/** Drawdown as a filled area below zero. */
function drawdownOption(detail: BacktestDetail): EChartsOption {
  const series = detail.series;
  if (!series) return {};
  return {
    tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => formatPercent(value as number) },
    grid: { left: 56, right: 24, top: 24, bottom: 32 },
    xAxis: { type: 'time' },
    // One decimal: a drawdown axis spans a few percent, and rounding to whole
    // percent collapses every tick onto "-0%".
    yAxis: { type: 'value', name: '回撤', axisLabel: { formatter: (value: number) => `${(value * 100).toFixed(1)}%` } },
    series: [
      {
        name: '回撤',
        type: 'line',
        showSymbol: false,
        connectNulls: false,
        color: '#F6903D',
        areaStyle: { opacity: 0.25 },
        data: series.dates.map((day, index) => [day, series.drawdown[index]]),
      },
    ],
  };
}

function positionPieOption(positions: BacktestPosition[]): EChartsOption {
  return {
    tooltip: { trigger: 'item', valueFormatter: (value: unknown) => formatPercent(value as number) },
    legend: { bottom: 0, type: 'scroll' },
    color: PIE_COLORS,
    series: [
      {
        name: '持仓权重',
        type: 'pie',
        radius: ['38%', '66%'],
        center: ['50%', '46%'],
        data: positions.map(row => ({ name: row.ts_code, value: row.weight })),
        label: { formatter: '{b}\n{d}%' },
      },
    ],
  };
}

export default function Detail() {
  const { runId = '' } = useParams();
  const { message } = AntdApp.useApp();

  const [detail, setDetail] = useState<BacktestDetail | null>(null);
  const [trades, setTrades] = useState<BacktestTrade[]>([]);
  const [tradeTotal, setTradeTotal] = useState(0);
  const [tradePage, setTradePage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [missing, setMissing] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [tradesFailure, setTradesFailure] = useState<string | null>(null);

  // React Router reuses this component instance when only `:runId` changes, so
  // without this a failed load of the next run would leave the previous run's
  // curves, header and trades on screen under the new URL.
  useEffect(() => {
    setDetail(null);
    setTrades([]);
    setTradeTotal(0);
    setTradePage(1);
    setMissing(false);
    setFailure(null);
    setTradesFailure(null);
    setLoading(true);
  }, [runId]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const body = await backtestsApi.detail(runId);
      setDetail(body);
      setMissing(false);
      setFailure(null);
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      if ((e as { status?: number }).status === 404) {
        setMissing(true);
      } else {
        // A toast disappears; without this the page would then render nothing
        // at all, which reads as "broken" rather than "the request failed".
        setFailure(text);
        message.error(text);
      }
    } finally {
      setLoading(false);
    }
  }, [message, runId]);

  const loadTrades = useCallback(async () => {
    try {
      const body = await backtestsApi.trades(runId, {
        limit: TRADE_PAGE_SIZE,
        offset: (tradePage - 1) * TRADE_PAGE_SIZE,
      });
      setTrades(body.items);
      setTradeTotal(body.total);
      setTradesFailure(null);
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      // Without this the empty state would read "无成交" for a request that
      // failed — a false statement about the run, not an error report.
      setTradesFailure(text);
      message.error(text);
    }
  }, [message, runId, tradePage]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadTrades();
  }, [loadTrades]);

  if (missing) {
    return (
      <Flex vertical gap={16}>
        <BacktestNav />
        <Alert
          type="error"
          showIcon
          message={`回测 ${runId} 不存在`}
          description="该运行没有落库的回测结果，可能已被清理，或这个标识不属于回测任务。"
          action={
            <Link to="/backtest">
              <Button size="small">返回回测列表</Button>
            </Link>
          }
        />
      </Flex>
    );
  }

  if (loading && detail === null) {
    return (
      <Flex vertical gap={16}>
        <BacktestNav />
        <Card loading />
      </Flex>
    );
  }

  if (failure !== null && detail === null) {
    return (
      <Flex vertical gap={16}>
        <BacktestNav />
        <Alert
          type="error"
          showIcon
          message="回测详情加载失败"
          description={failure}
          action={
            <Button size="small" onClick={() => void load()}>
              重试
            </Button>
          }
        />
      </Flex>
    );
  }

  if (detail === null) return null;

  const status = detail.status as RunStatus;
  const series = detail.series;
  const hasSeries = series !== null && series.nav.length > 0;
  const positions = detail.positions;
  const hasBenchmark = series !== null && series.benchmark.some(value => value !== null);

  const positionColumns: TableProps<BacktestPosition>['columns'] = [
    { title: '代码', dataIndex: 'ts_code', key: 'ts_code', width: 140 },
    { title: '股数', dataIndex: 'shares', key: 'shares', width: 100 },
    { title: '成本价', dataIndex: 'avg_cost', key: 'avg_cost', width: 110, render: (v: number) => v.toFixed(2) },
    { title: '现价', dataIndex: 'current_price', key: 'current_price', width: 110, render: (v: number) => v.toFixed(2) },
    { title: '市值', dataIndex: 'market_value', key: 'market_value', width: 130, render: (v: number) => v.toFixed(2) },
    {
      title: '权重',
      dataIndex: 'weight',
      key: 'weight',
      width: 100,
      render: (weight: number) => formatPercent(weight),
    },
  ];

  const tradeColumns: TableProps<BacktestTrade>['columns'] = [
    { title: '序号', dataIndex: 'seq', key: 'seq', width: 80 },
    { title: '日期', dataIndex: 'trade_date', key: 'trade_date', width: 120, render: formatDate },
    { title: '方向', dataIndex: 'action', key: 'action', width: 80 },
    { title: '代码', dataIndex: 'ts_code', key: 'ts_code', width: 140 },
    { title: '股数', dataIndex: 'shares', key: 'shares', width: 100 },
    { title: '价格', dataIndex: 'price', key: 'price', width: 100, render: (v: number) => v.toFixed(2) },
    { title: '佣金', dataIndex: 'commission', key: 'commission', width: 100, render: (v: number) => v.toFixed(2) },
    { title: '印花税', dataIndex: 'stamp_duty', key: 'stamp_duty', width: 100, render: (v: number) => v.toFixed(2) },
    { title: '过户费', dataIndex: 'transfer_fee', key: 'transfer_fee', width: 100, render: (v: number) => v.toFixed(2) },
  ];

  return (
    <Flex vertical gap={16}>
      <BacktestNav />

      <Card
        title={
          <Flex gap={12} align="center">
            <span>回测 {detail.run_id}</span>
            <Tag color={STATUS_COLORS[status] ?? 'default'}>{STATUS_LABELS[status] ?? detail.status}</Tag>
          </Flex>
        }
        extra={
          <Typography.Text type="secondary">
            策略 {detail.strategy ?? '—'} · 区间 {formatDate(detail.start)} ~ {formatDate(detail.end)} ·{' '}
            {/* A cancelled run's `finished_at` is the moment it was stopped, not
                a completion; labelling it 完成于 would misdate the result. */}
            {detail.status === 'cancelled' ? '取消于' : '完成于'} {formatTime(detail.finished_at)}
          </Typography.Text>
        }
      >
        {detail.status === 'cancelled' && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="该回测被取消"
            description="曲线停在取消那一周，之后的区间没有执行，因此不与完整区间的回测直接比大小。"
          />
        )}

        <Row gutter={[16, 16]}>
          <Col xs={12} md={8} lg={4}>
            <Statistic title="总收益" value={formatPercent(detail.metrics.total_return)} />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <Statistic title="年化收益" value={formatPercent(detail.metrics.annual_return)} />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <Statistic title="最大回撤" value={formatPercent(detail.metrics.max_drawdown)} />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <Statistic
              title="夏普"
              value={detail.metrics.sharpe_ratio === undefined ? '—' : detail.metrics.sharpe_ratio.toFixed(2)}
            />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <Statistic
              title="Calmar"
              value={detail.metrics.calmar_ratio === undefined ? '—' : detail.metrics.calmar_ratio.toFixed(2)}
            />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <Statistic title="周胜率" value={formatPercent(detail.metrics.win_rate)} />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <Statistic title="交易次数" value={detail.metrics.total_trades ?? 0} precision={0} />
          </Col>
          {/* The engine leaves both at 0.0 when there is no benchmark, which
              reads as "the benchmark was flat" rather than "there wasn't one". */}
          <Col xs={12} md={8} lg={4}>
            <Statistic title="基准收益" value={hasBenchmark ? formatPercent(detail.metrics.benchmark_return) : '—'} />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <Statistic title="超额收益" value={hasBenchmark ? formatPercent(detail.metrics.excess_return) : '—'} />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <Statistic title="期末现金" value={detail.cash ?? '—'} precision={2} />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <Statistic title="期末总资产" value={detail.total_value ?? '—'} precision={2} />
          </Col>
        </Row>
      </Card>

      <Card size="small" title="净值与基准">
        {hasSeries ? (
          <Flex vertical gap={16}>
            <EChart option={levelOption(detail)} height={380} aria-label="策略净值与基准净值曲线" />
            {hasBenchmark && (
              <Card size="small" title="超额收益（策略净值 − 基准净值）">
                <EChart option={excessOption(detail)} height={200} />
              </Card>
            )}
            <Card size="small" title="回撤">
              <EChart option={drawdownOption(detail)} height={200} />
            </Card>
          </Flex>
        ) : (
          <Alert
            type="info"
            showIcon
            message="没有可展示的净值曲线"
            description="该运行没有产生净值序列——可能是区间内没有交易日，或第一个调仓周之前就被取消。"
          />
        )}
      </Card>

      <Card size="small" title={`期末持仓（${positions.length} 只）`}>
        {positions.length > 0 ? (
          <Row gutter={16}>
            <Col xs={24} lg={14}>
              <Table<BacktestPosition>
                rowKey="ts_code"
                columns={positionColumns}
                dataSource={positions}
                size="small"
                pagination={false}
                scroll={{ x: 680 }}
              />
            </Col>
            <Col xs={24} lg={10}>
              <EChart option={positionPieOption(positions)} height={320} aria-label="持仓权重分布" />
            </Col>
          </Row>
        ) : (
          <Alert type="info" showIcon message="期末空仓" description="该次回测结束时组合没有持有任何股票。" />
        )}
      </Card>

      <Card size="small" title={`交易明细（共 ${tradeTotal} 笔）`}>
        {/* A table with nine headers and no rows says less than one line of
            text; the 期末空仓 block below already works this way. The failure
            branch comes first: a failed request is not "no trades". */}
        {tradesFailure !== null ? (
          <Alert
            type="error"
            showIcon
            message="交易明细加载失败"
            description={tradesFailure}
            action={
              <Button size="small" onClick={() => void loadTrades()}>
                重试
              </Button>
            }
          />
        ) : tradeTotal === 0 ? (
          <Alert type="info" showIcon message="无成交" description="该次回测没有产生任何成交记录。" />
        ) : (
          <Table<BacktestTrade>
            rowKey="seq"
            columns={tradeColumns}
            dataSource={trades}
            size="small"
            virtual
            scroll={{ x: 1020, y: 420 }}
            pagination={{
              current: tradePage,
              pageSize: TRADE_PAGE_SIZE,
              total: tradeTotal,
              showSizeChanger: false,
              showTotal: count => `共 ${count} 笔`,
              onChange: nextPage => setTradePage(nextPage),
            }}
          />
        )}
      </Card>
    </Flex>
  );
}
