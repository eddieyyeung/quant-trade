import { useEffect, useState } from 'react';
import { App as AntdApp, Alert, Button, Card, DatePicker, Flex, Form, InputNumber, Select, Space, Typography } from 'antd';
import type { EChartsOption } from 'echarts';
import type { Dayjs } from 'dayjs';

import { factorsApi, type NavSeries, type QuantileResult } from '../../api/factors';
import EChart from '../../charts/EChart';
import FactorNav from './FactorNav';
import { readSelectedFactors } from './selection';

const { RangePicker } = DatePicker;

interface FormValues {
  factor: string;
  window?: [Dayjs | null, Dayjs | null] | null;
  n_groups: number;
  forward_period: number;
}

const GROUP_COLORS = ['#5B8FF9', '#61DDAA', '#F6BD16', '#7262FD', '#78D3F8', '#9661BC', '#F6903D', '#008685'];

function navOption(series: NavSeries[]): EChartsOption {
  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, type: 'scroll' },
    grid: { left: 56, right: 24, top: 48, bottom: 40 },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', scale: true, name: '净值' },
    series: series.map((line, index) => ({
      name: line.name,
      type: 'line',
      showSymbol: false,
      color: GROUP_COLORS[index % GROUP_COLORS.length],
      data: line.dates.map((day, i) => [day, line.values[i]]),
    })),
  };
}

export default function Quantile() {
  const { message } = AntdApp.useApp();
  const [form] = Form.useForm<FormValues>();
  const [options, setOptions] = useState<string[]>([]);
  const [result, setResult] = useState<QuantileResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    factorsApi
      .list({ limit: 200, offset: 0 })
      .then(body => {
        const names = body.items.filter(row => row.persisted).map(row => row.name);
        setOptions(names);
        const preferred = readSelectedFactors().find(name => names.includes(name));
        form.setFieldValue('factor', preferred ?? names[0]);
      })
      .catch((e: unknown) => message.error(e instanceof Error ? e.message : String(e)));
  }, [form, message]);

  const submit = async (values: FormValues) => {
    const [start, end] = values.window ?? [null, null];
    setLoading(true);
    try {
      setResult(
        await factorsApi.quantile({
          factor: values.factor,
          nGroups: values.n_groups,
          forwardPeriod: values.forward_period,
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

  const groups = result?.groups ?? [];

  return (
    <Flex vertical gap={16}>
      <FactorNav />

      <Card title="分层回测">
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="这是因子区分度的统计视图，不是可交易组合的收益"
          description="分组内等权、满仓、不计交易成本，也不考虑涨跌停与停牌。曲线用来判断因子把截面分得开不开，不能当作策略回测结果。"
        />

        <Form<FormValues>
          form={form}
          layout="inline"
          initialValues={{ n_groups: 5, forward_period: 5, window: null }}
          onFinish={values => void submit(values)}
        >
          <Form.Item name="factor" label="因子" rules={[{ required: true, message: '请选择因子' }]}>
            <Select
              showSearch
              style={{ width: 200 }}
              placeholder="选择因子"
              options={options.map(name => ({ value: name, label: name }))}
            />
          </Form.Item>
          <Form.Item name="window" label="区间">
            <RangePicker allowEmpty={[true, true]} />
          </Form.Item>
          <Form.Item name="n_groups" label="分组数">
            <InputNumber min={2} max={20} />
          </Form.Item>
          <Form.Item name="forward_period" label="持有期">
            <InputNumber min={1} addonAfter="日" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading}>
              计算
            </Button>
          </Form.Item>
        </Form>
      </Card>

      {result && (
        <Card
          size="small"
          title={`分组净值（${result.n_groups} 组，共 ${result.rebalance_count} 个调仓日）`}
          extra={
            result.skipped_dates > 0 ? (
              <Typography.Text type="secondary">跳过 {result.skipped_dates} 个截面过窄的交易日</Typography.Text>
            ) : null
          }
        >
          {groups.length > 0 ? (
            <Flex vertical gap={16}>
              <EChart option={navOption(groups)} height={380} />
              {result.long_short && (
                <Card size="small" title="多空组合净值（顶组 − 底组）">
                  <EChart option={navOption([result.long_short])} height={260} />
                </Card>
              )}
            </Flex>
          ) : (
            <Alert
              type="info"
              showIcon
              message="所选区间没有可用的因子值"
              description="该因子在这段时间内没有落库数据，或每个交易日的有效截面都不足以分组。"
            />
          )}
        </Card>
      )}

      {!result && (
        <Card size="small">
          <Space>
            <Typography.Text type="secondary">选择因子与区间后点击「计算」。</Typography.Text>
          </Space>
        </Card>
      )}
    </Flex>
  );
}
