import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { App as AntdApp, Alert, Button, Card, DatePicker, Empty, Flex, Select, Space, Table, Tag, Typography } from 'antd';
import type { TableProps } from 'antd';
import type { EChartsOption } from 'echarts';
import dayjs from 'dayjs';
import { Link } from 'react-router-dom';

import { modelsApi, type ModelPredictions, type PredictionRow } from '../../api/models';
import EChart from '../../charts/EChart';
import { formatCount, formatDate, formatTime } from '../../utils/format';
import ModelNav from './ModelNav';

const TOP_N_OPTIONS = [10, 15, 20, 30, 50];
const DEFAULT_TOP_N = 15;
/** Fixed bin count, so two dates' distributions can be read against each other. */
const BINS = 20;

/** A compact axis label for a score, which is a small number near zero. */
function formatScore(value: number): string {
  return String(Number(value.toPrecision(3)));
}

/**
 * Equal-width bins over one day's scores.
 *
 * The distribution is drawn from the same array the picks table is sliced out
 * of, so the two can never disagree about what was predicted that day.
 */
function scoreHistogram(scores: PredictionRow[]): { labels: string[]; counts: number[] } {
  if (scores.length === 0) return { labels: [], counts: [] };
  let min = scores[0].score;
  let max = scores[0].score;
  for (const row of scores) {
    if (row.score < min) min = row.score;
    if (row.score > max) max = row.score;
  }
  // Every stock scored the same: one bar, since no bin would have a width.
  if (min === max) return { labels: [formatScore(min)], counts: [scores.length] };

  const width = (max - min) / BINS;
  const counts = new Array<number>(BINS).fill(0);
  for (const row of scores) {
    // The top score lands exactly on `BINS`; it belongs in the last bin.
    counts[Math.min(BINS - 1, Math.floor((row.score - min) / width))] += 1;
  }
  return { labels: counts.map((_, index) => formatScore(min + index * width)), counts };
}

export default function Predict() {
  const { message } = AntdApp.useApp();

  const [body, setBody] = useState<ModelPredictions | null>(null);
  /** `null` asks the server for its own default date rather than naming one. */
  const [asOf, setAsOf] = useState<string | null>(null);
  const [topN, setTopN] = useState(DEFAULT_TOP_N);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);
  const /** True while a request is outstanding, so clicks cannot pile up. */
    inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const next = await modelsApi.predictions({ asOf, topN });
      setBody(next);
      setFailure(null);
      // A fresh page names no date, and the server answers with the database's
      // latest trade date — which can sit past the last day the training wrote.
      // Fall back to the newest date the file actually holds.
      if (asOf === null && next.available && next.scores.length === 0 && next.available_dates.length > 0) {
        setAsOf(next.available_dates[next.available_dates.length - 1]);
      }
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      // A toast disappears; without this the page would then render nothing at
      // all, which reads as "broken" rather than "the request failed".
      setFailure(text);
      message.error(text);
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [asOf, message, topN]);

  useEffect(() => {
    void load();
  }, [load]);

  const availableDates = body?.available_dates;
  // The picker's panel asks whether each of its cells is selectable; a set
  // built per render would be thousands of inserts for one date click.
  const dateSet = useMemo(() => new Set(availableDates ?? []), [availableDates]);
  const histogram = useMemo(() => scoreHistogram(body?.scores ?? []), [body]);

  if (failure !== null && body === null) {
    return (
      <Flex vertical gap={16}>
        <ModelNav />
        <Alert
          type="error"
          showIcon
          message="预测加载失败"
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

  if (loading && body === null) {
    return (
      <Flex vertical gap={16}>
        <ModelNav />
        <Card loading />
      </Flex>
    );
  }

  // No predictions file is the state a fresh install is in, not a failure.
  if (body === null || !body.available) {
    return (
      <Flex vertical gap={16}>
        <ModelNav />
        <Card title="预测">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Flex vertical gap={4}>
                <Typography.Text strong>尚无预测</Typography.Text>
                <Typography.Text type="secondary">
                  还没有任何训练写出预测文件。先完成一次训练，这里会展示它写下的最近一日选股与得分分布。
                </Typography.Text>
              </Flex>
            }
          >
            <Link to="/models">
              <Button type="primary">去训练一个模型</Button>
            </Link>
          </Empty>
        </Card>
      </Flex>
    );
  }

  const source = body.source;
  const shown = body.as_of ?? null;
  const dates = availableDates ?? [];
  const picks = body.picks;
  const totalScored = body.total_scored ?? 0;
  const hasPicks = picks.length > 0;
  const first = dates[0];
  const last = dates[dates.length - 1];

  const pickColumns: TableProps<PredictionRow>['columns'] = [
    {
      title: '排名',
      key: 'rank',
      width: 80,
      render: (_, __, index) => index + 1,
    },
    {
      title: '股票代码',
      dataIndex: 'ts_code',
      key: 'ts_code',
      render: (code: string) => <Typography.Text code>{code}</Typography.Text>,
    },
    {
      title: '得分',
      dataIndex: 'score',
      key: 'score',
      width: 140,
      render: (value: number) => value.toFixed(4),
    },
  ];

  const distributionOption: EChartsOption = {
    tooltip: { trigger: 'axis' },
    grid: { left: 56, right: 24, top: 32, bottom: 72 },
    xAxis: { type: 'category', data: histogram.labels, axisLabel: { rotate: 45 } },
    yAxis: { type: 'value', name: '股票数' },
    series: [{ name: '股票数', type: 'bar', data: histogram.counts }],
  };

  return (
    <Flex vertical gap={16}>
      <ModelNav />

      <Card
        title={
          <Flex gap={12} align="center">
            <span>预测</span>
            {shown && <Tag>{shown}</Tag>}
          </Flex>
        }
        extra={
          <Space>
            <DatePicker
              allowClear={false}
              value={shown ? dayjs(shown) : null}
              // Only dates the file holds are selectable, so "no predictions
              // for this day" cannot be reached by picking a date.
              disabledDate={current => !dateSet.has(current.format('YYYY-MM-DD'))}
              onChange={value => setAsOf(value ? value.format('YYYY-MM-DD') : null)}
            />
            <Select
              style={{ width: 110 }}
              value={topN}
              options={TOP_N_OPTIONS.map(count => ({ value: count, label: `前 ${count} 名` }))}
              onChange={setTopN}
            />
            <Button size="small" loading={loading} onClick={() => void load()}>
              刷新
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph type="secondary">
          该份预测来自训练运行 <Typography.Text code>{source?.run_id ?? '—'}</Typography.Text>
          （完成于 {formatTime(source?.finished_at)}），覆盖 {formatCount(dates.length)} 个交易日（{formatDate(first)} ~{' '}
          {formatDate(last)}）。本页只展示最近一次成功训练写下的输出，不提供按历史运行查看预测的入口。
        </Typography.Paragraph>

        {failure !== null && <Alert type="error" showIcon style={{ marginBottom: 16 }} message="预测刷新失败" description={failure} />}

        {hasPicks ? (
          <Flex vertical gap={16}>
            <Card size="small" title={`Top ${picks.length} 选股（当日共 ${formatCount(totalScored)} 只有得分）`}>
              <Table<PredictionRow>
                rowKey="ts_code"
                columns={pickColumns}
                dataSource={picks}
                loading={loading}
                size="small"
                pagination={false}
              />
            </Card>

            <Card size="small" title={`得分分布（${BINS} 个等宽分箱，与选股表同源）`}>
              <Flex vertical gap={8}>
                <EChart option={distributionOption} height={320} aria-label="当日全部得分的分布" />
                <Typography.Text type="secondary">
                  横轴为分箱下界，纵轴为该分数区间内的股票数；分箱数固定，便于与其它日期横向比较。
                </Typography.Text>
              </Flex>
            </Card>
          </Flex>
        ) : (
          <Alert
            type="info"
            showIcon
            message="该日无预测"
            description="预测文件里没有这一天的记录——可能该日不是交易日，或训练区间未覆盖该日。请选择其它日期。"
          />
        )}
      </Card>
    </Flex>
  );
}
