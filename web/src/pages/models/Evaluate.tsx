import { useCallback, useEffect, useRef, useState } from 'react';
import { App as AntdApp, Alert, Button, Card, Col, Flex, Row, Space, Statistic, Tag, Typography } from 'antd';
import type { EChartsOption } from 'echarts';
import { Link, useParams } from 'react-router-dom';

import { modelsApi, type ModelEvaluation } from '../../api/models';
import { STATUS_COLORS, STATUS_LABELS, isTerminal } from '../../api/runs';
import EChart from '../../charts/EChart';
import { formatCount, formatDate, formatPercent, formatTime } from '../../utils/format';
import ModelNav from './ModelNav';

/** How many factors the importance chart asks for; the header reads the total. */
const IMPORTANCE_TOP_N = 20;
/** How often to re-check while the run is still training. */
const LIVE_POLL_MS = 5000;

export default function Evaluate() {
  const { runId = '' } = useParams();
  const { message } = AntdApp.useApp();

  const [evaluation, setEvaluation] = useState<ModelEvaluation | null>(null);
  const [loading, setLoading] = useState(true);
  const [missing, setMissing] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const /** True while a refresh is already queued, so polls cannot pile up. */
    inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const body = await modelsApi.evaluation(runId, { importanceTopN: IMPORTANCE_TOP_N });
      setEvaluation(body);
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
      inFlight.current = false;
      setLoading(false);
    }
  }, [message, runId]);

  useEffect(() => {
    void load();
  }, [load]);

  // A run still training has no rows to read yet; re-read until it settles so
  // the page fills in without a manual refresh. Nothing here starts a run.
  const live = evaluation !== null && !isTerminal(evaluation.status);
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => void load(), LIVE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [live, load]);

  if (missing) {
    return (
      <Flex vertical gap={16}>
        <ModelNav />
        <Alert
          type="error"
          showIcon
          message={`训练运行 ${runId} 不存在`}
          description="该标识不属于任何训练运行——可能已随库清理，或是别的任务类型。"
          action={
            <Link to="/models">
              <Button size="small">返回模型页面</Button>
            </Link>
          }
        />
      </Flex>
    );
  }

  if (loading && evaluation === null) {
    return (
      <Flex vertical gap={16}>
        <ModelNav />
        <Card loading />
      </Flex>
    );
  }

  if (failure !== null && evaluation === null) {
    return (
      <Flex vertical gap={16}>
        <ModelNav />
        <Alert
          type="error"
          showIcon
          message="模型评估加载失败"
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

  if (evaluation === null) return null;

  const status = evaluation.status;
  const cancelled = status === 'cancelled';
  const points = evaluation.ic_series;
  const yearly = evaluation.yearly;
  const importance = evaluation.importance;
  const windowsTrained = evaluation.metrics.windows_trained;

  const icOption: EChartsOption = {
    tooltip: { trigger: 'axis' },
    grid: { left: 56, right: 24, top: 32, bottom: 56 },
    xAxis: { type: 'category', data: points.map(point => point.trade_date) },
    yAxis: { type: 'value', scale: true, name: 'RankIC' },
    // A multi-year daily series is unreadable end to end without zooming.
    dataZoom: [
      { type: 'inside' },
      { type: 'slider', height: 20, bottom: 8 },
    ],
    series: [
      {
        name: 'RankIC',
        type: 'line',
        showSymbol: false,
        data: points.map(point => point.rank_ic),
        // The zero line is the whole point of an IC chart: which side of it the
        // model sits on is the signal.
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

  const yearlyOption: EChartsOption = {
    tooltip: {
      trigger: 'axis',
      formatter: (params: unknown) => {
        const list = (Array.isArray(params) ? params : [params]) as {
          marker?: string;
          seriesName?: string;
          value?: unknown;
          dataIndex?: number;
        }[];
        const row = yearly[list[0]?.dataIndex ?? 0];
        const head = row ? `${row.year} 年（有效 ${row.days} 天）` : '';
        const lines = list.map(item => `${item.marker ?? ''}${item.seriesName ?? ''}: ${Number(item.value).toFixed(4)}`);
        return [head, ...lines].join('<br/>');
      },
    },
    legend: { top: 0 },
    grid: { left: 56, right: 64, top: 40, bottom: 32 },
    xAxis: { type: 'category', data: yearly.map(row => String(row.year)) },
    yAxis: [
      { type: 'value', scale: true, name: 'IC 均值' },
      {
        type: 'value',
        name: '正 IC 占比',
        min: 0,
        max: 1,
        axisLabel: { formatter: (value: number) => `${Math.round(value * 100)}%` },
      },
    ],
    series: [
      { name: 'IC 均值', type: 'bar', data: yearly.map(row => row.ic_mean) },
      { name: '正 IC 占比', type: 'bar', yAxisIndex: 1, data: yearly.map(row => row.ic_positive_ratio) },
    ],
  };

  // ECharts draws the first category at the bottom; the ranking reads top-down,
  // so the least important factor has to be pushed to the bottom of the axis.
  const ranked = [...importance].reverse();
  const importanceOption: EChartsOption = {
    tooltip: {
      trigger: 'item',
      formatter: (params: unknown) => {
        const entry = ranked[(params as { dataIndex?: number }).dataIndex ?? 0];
        if (!entry) return '';
        return `${entry.factor}<br/>窗口均值: ${entry.importance.toFixed(4)}<br/>窗口标准差: ±${entry.std.toFixed(4)}`;
      },
    },
    grid: { left: 150, right: 150, top: 16, bottom: 24 },
    xAxis: { type: 'value' },
    yAxis: {
      type: 'category',
      data: ranked.map(entry => entry.factor),
      axisLabel: { width: 140, overflow: 'truncate' },
    },
    series: [
      {
        name: '窗口均值',
        type: 'bar',
        data: ranked.map(entry => entry.importance),
        // The spread is annotated on the bar rather than drawn as an error bar:
        // `custom` series are not registered in the shared chart wrapper, and a
        // number next to the bar reads the dispersion just as well.
        label: {
          show: true,
          position: 'right',
          formatter: (params: { dataIndex?: number }) => {
            const entry = ranked[params.dataIndex ?? 0];
            return entry ? `${entry.importance.toFixed(4)} ± ${entry.std.toFixed(4)}` : '';
          },
        },
      },
    ],
  };

  return (
    <Flex vertical gap={16}>
      <ModelNav />

      <Card
        title={
          <Flex gap={12} align="center">
            <span>模型评估 {evaluation.run_id}</span>
            <Tag color={STATUS_COLORS[status] ?? 'default'}>{STATUS_LABELS[status] ?? status}</Tag>
          </Flex>
        }
        extra={
          <Space>
            <Typography.Text type="secondary">
              覆盖区间 {formatDate(evaluation.start)} ~ {formatDate(evaluation.end)} · 完成 {windowsTrained} 个窗口 ·{' '}
              {/* A cancelled run's `finished_at` is the moment it was stopped,
                  not a completion; labelling it 生成于 would misdate it. */}
              {cancelled ? '取消于' : '数据生成于'} {formatTime(evaluation.finished_at)}
            </Typography.Text>
            <Button size="small" loading={loading} onClick={() => void load()}>
              刷新
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph type="secondary">
          本页读的是该次训练落库的结果，不会重新训练，也不随行情实时变化——每次训练生成一份，新的训练写入自己的 run。
        </Typography.Paragraph>

        {cancelled && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="该次训练已取消"
            description="曲线停在取消前最后一个预测日，此后的区间没有训练，因此不与完整区间的结果直接比大小。"
          />
        )}

        <Row gutter={[16, 16]}>
          <Col xs={12} lg={6}>
            <Statistic title="IC 均值" value={evaluation.metrics.ic_mean ?? undefined} precision={4} />
          </Col>
          <Col xs={12} lg={6}>
            <Statistic title="IC_IR" value={evaluation.metrics.ic_ir ?? undefined} precision={3} />
          </Col>
          <Col xs={12} lg={6}>
            <Statistic title="正 IC 占比" value={formatPercent(evaluation.metrics.ic_positive_ratio)} />
          </Col>
          <Col xs={12} lg={6}>
            <Statistic title="有效天数" value={formatCount(evaluation.metrics.ic_days)} />
          </Col>
        </Row>
      </Card>

      <Card
        size="small"
        title={`RankIC 逐日序列（${formatDate(evaluation.start)} ~ ${formatDate(evaluation.end)}，${points.length} 个预测日）`}
      >
        {points.length > 0 ? (
          <EChart option={icOption} height={360} aria-label="逐日 RankIC 曲线" />
        ) : (
          <Alert
            type="info"
            showIcon
            message="无有效 IC"
            description={
              windowsTrained > 0
                ? '该次训练没有满足最小截面样本数的预测日，因此没有可画的 RankIC 曲线。'
                : '该次训练没有任何窗口成功训练，因此没有预测，也没有 RankIC。'
            }
          />
        )}
      </Card>

      <Card size="small" title="分年度表现（IC 均值与正 IC 占比，供跨年稳定性判断）">
        {yearly.length > 0 ? (
          <EChart option={yearlyOption} height={300} aria-label="分年度 IC 均值与正 IC 占比" />
        ) : (
          <Alert
            type="info"
            showIcon
            message="没有可汇总的年度"
            description="逐日 RankIC 为空时年度汇总自然为空；IC 序列落库后这里会按自然年给出 IC 均值与正 IC 占比。"
          />
        )}
      </Card>

      <Card
        size="small"
        title={`特征重要性 Top ${importance.length} / ${evaluation.importance_total}（按窗口均值降序）`}
      >
        {importance.length > 0 ? (
          <Flex vertical gap={8}>
            <EChart
              option={importanceOption}
              height={Math.max(320, importance.length * 26 + 80)}
              aria-label="特征重要性前 N 项"
            />
            <Typography.Text type="secondary">
              条形为各窗口重要性的均值，标注中的 ± 为窗口标准差；点选条形可看该因子的两个数值。
            </Typography.Text>
          </Flex>
        ) : (
          <Alert
            type="info"
            showIcon
            message="无特征重要性"
            description={
              windowsTrained > 0
                ? '该次训练没有落库的特征重要性记录。'
                : '该次训练没有任何窗口成功训练，因此没有窗口级的特征重要性。'
            }
          />
        )}
      </Card>
    </Flex>
  );
}
