import { useEffect, useState } from 'react';
import { App as AntdApp, Alert, Button, Card, DatePicker, Flex, Select, Space, Typography } from 'antd';
import type { EChartsOption } from 'echarts';
import type { Dayjs } from 'dayjs';

import { factorsApi, type CorrelationResult } from '../../api/factors';
import EChart from '../../charts/EChart';
import FactorNav from './FactorNav';
import { readSelectedFactors, writeSelectedFactors } from './selection';

const { RangePicker } = DatePicker;

/** Mirrors the server-side cap on `FactorCorrelationParams.factors`. */
const MAX_FACTORS = 50;

export default function Correlation() {
  const { message } = AntdApp.useApp();
  const [options, setOptions] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>(() => readSelectedFactors());
  const [window, setWindow] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const [result, setResult] = useState<CorrelationResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    factorsApi
      .list({ limit: 200, offset: 0 })
      .then(body => setOptions(body.items.filter(row => row.persisted).map(row => row.name)))
      .catch((e: unknown) => message.error(e instanceof Error ? e.message : String(e)));
  }, [message]);

  const tooMany = selected.length > MAX_FACTORS;

  const compute = async () => {
    if (selected.length < 2 || tooMany) return;
    const [start, end] = window ?? [null, null];
    setLoading(true);
    try {
      setResult(
        await factorsApi.correlation(selected, {
          start: start ? start.format('YYYY-MM-DD') : undefined,
          end: end ? end.format('YYYY-MM-DD') : undefined,
        }),
      );
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const factors = result?.factors ?? [];
  const matrix = result?.matrix ?? [];

  // ECharts heatmap wants [xIndex, yIndex, value]; a null cell renders empty
  // rather than as a zero, which would read as "uncorrelated".
  const cells: [number, number, number | string][] = [];
  matrix.forEach((row, y) => {
    row.forEach((value, x) => cells.push([x, y, value === null ? '-' : value]));
  });

  const option: EChartsOption = {
    tooltip: { position: 'top' },
    grid: { left: 110, right: 24, top: 24, bottom: 96 },
    xAxis: { type: 'category', data: factors, axisLabel: { rotate: 45 } },
    yAxis: { type: 'category', data: factors, axisLabel: { width: 100, overflow: 'truncate' } },
    visualMap: {
      min: -1,
      max: 1,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      inRange: { color: ['#3B6FD4', '#F2F4F7', '#C0392B'] },
    },
    series: [
      {
        type: 'heatmap',
        data: cells,
        label: {
          show: factors.length <= 12,
          formatter: (params: { value?: unknown }) => {
            const cell = params.value;
            return Array.isArray(cell) ? Number(cell[2]).toFixed(2) : '';
          },
        },
      },
    ],
  };

  return (
    <Flex vertical gap={16}>
      <FactorNav />

      <Card
        title="因子相关性"
        extra={
          <Space>
            <RangePicker
              allowEmpty={[true, true]}
              value={window}
              onChange={value => setWindow(value as [Dayjs | null, Dayjs | null] | null)}
            />
            <Button
              type="primary"
              size="small"
              loading={loading}
              disabled={selected.length < 2 || tooMany}
              onClick={() => void compute()}
            >
              计算
            </Button>
          </Space>
        }
      >
        <Flex vertical gap={12}>
          <Select
            mode="multiple"
            showSearch
            placeholder="选择至少两个因子"
            style={{ width: '100%' }}
            value={selected}
            options={options.map(name => ({ value: name, label: name }))}
            onChange={value => {
              setSelected(value);
              writeSelectedFactors(value);
            }}
            maxTagCount="responsive"
          />

          <Typography.Text type="secondary">
            计算在点击「计算」后触发，不会随页面加载自动执行。矩阵元素为区间内逐交易日截面相关的均值。
          </Typography.Text>

          {tooMany && (
            <Alert
              type="error"
              showIcon
              message={`最多同时计算 ${MAX_FACTORS} 个因子，当前已选 ${selected.length} 个`}
            />
          )}

          {selected.length === 1 && <Alert type="info" showIcon message="至少需要选择两个因子" />}
        </Flex>
      </Card>

      {result && (
        <Card
          size="small"
          title="相关矩阵"
          extra={
            <Typography.Text type="secondary">
              有效交易日 {result.date_count} 个
              {result.skipped_dates > 0 ? `，跳过 ${result.skipped_dates} 个截面过窄的交易日` : ''}
            </Typography.Text>
          }
        >
          {matrix.length > 0 ? (
            <EChart option={option} height={Math.max(320, factors.length * 24 + 140)} />
          ) : (
            <Alert
              type="info"
              showIcon
              message="没有可用的相关性结果"
              description="所选区间内没有满足最小截面样本数的交易日。"
            />
          )}
        </Card>
      )}
    </Flex>
  );
}
