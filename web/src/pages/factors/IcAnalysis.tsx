import { useCallback, useEffect, useState } from 'react';
import { App as AntdApp, Alert, Button, Card, Col, Empty, Flex, Row, Select, Space, Statistic, Typography } from 'antd';
import type { EChartsOption } from 'echarts';
import { useNavigate } from 'react-router-dom';

import { factorsApi, type IcDecay, type IcSeries } from '../../api/factors';
import { runsApi } from '../../api/runs';
import EChart from '../../charts/EChart';
import { formatPercent } from '../../utils/format';
import FactorNav from './FactorNav';
import { readSelectedFactors } from './selection';

const PERIODS = [1, 5, 10, 20];

export default function IcAnalysis() {
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();

  const [factor, setFactor] = useState<string | null>(null);
  const [forwardPeriod, setForwardPeriod] = useState(5);
  const [options, setOptions] = useState<string[]>([]);
  const [series, setSeries] = useState<IcSeries | null>(null);
  const [decay, setDecay] = useState<IcDecay | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // The catalogue supplies the options; the library page's ticks pick the default.
  useEffect(() => {
    let cancelled = false;
    factorsApi
      .list({ limit: 200, offset: 0 })
      .then(body => {
        if (cancelled) return;
        const names = body.items.filter(row => row.persisted).map(row => row.name);
        setOptions(names);
        const preferred = readSelectedFactors().find(name => names.includes(name));
        setFactor(preferred ?? names[0] ?? null);
      })
      .catch((e: unknown) => message.error(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [message]);

  const load = useCallback(async () => {
    if (!factor) return;
    setLoading(true);
    try {
      const [ic, decayed] = await Promise.all([
        factorsApi.ic(factor, { forwardPeriod }),
        factorsApi.icDecay(factor, {}),
      ]);
      setSeries(ic);
      setDecay(decayed);
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [factor, forwardPeriod, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const submitCompute = async () => {
    if (!factor) return;
    setSubmitting(true);
    try {
      const run = await runsApi.submit('factor_ic', { factors: [factor], forward_periods: PERIODS });
      message.success('IC 计算任务已提交，正在跳转任务中心');
      navigate(`/jobs/${run.run_id}`);
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const points = series?.series ?? [];
  const hasData = points.length > 0;

  const icOption: EChartsOption = {
    tooltip: { trigger: 'axis' },
    grid: { left: 56, right: 24, top: 32, bottom: 56 },
    xAxis: { type: 'category', data: points.map(point => point.trade_date) },
    yAxis: { type: 'value', scale: true, name: 'RankIC' },
    series: [
      {
        name: 'RankIC',
        type: 'line',
        showSymbol: false,
        data: points.map(point => point.rank_ic),
        // The zero line is the whole point of an IC chart: which side of it the
        // factor sits on is the signal.
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

  const decayItems = decay?.items ?? [];
  const decayOption: EChartsOption = {
    tooltip: { trigger: 'axis' },
    grid: { left: 56, right: 24, top: 32, bottom: 40 },
    xAxis: { type: 'category', data: decayItems.map(item => `${item.forward_period}日`) },
    yAxis: { type: 'value', name: 'IC 均值' },
    series: [{ name: 'IC 均值', type: 'bar', data: decayItems.map(item => item.ic_mean) }],
  };

  return (
    <Flex vertical gap={16}>
      <FactorNav />

      <Card
        title="IC 分析"
        extra={
          <Space>
            <Select
              showSearch
              placeholder="选择因子"
              style={{ width: 200 }}
              value={factor}
              options={options.map(name => ({ value: name, label: name }))}
              onChange={setFactor}
            />
            <Select
              style={{ width: 120 }}
              value={forwardPeriod}
              options={PERIODS.map(period => ({ value: period, label: `${period} 日` }))}
              onChange={setForwardPeriod}
            />
            <Button size="small" loading={loading} onClick={() => void load()}>
              刷新
            </Button>
            <Button type="primary" size="small" loading={submitting} disabled={!factor} onClick={() => void submitCompute()}>
              计算该因子 IC
            </Button>
          </Space>
        }
      >
        {hasData ? (
          <Flex vertical gap={16}>
            <Row gutter={16}>
              <Col span={6}>
                <Statistic title="IC 均值" value={series?.summary.ic_mean ?? undefined} precision={4} />
              </Col>
              <Col span={6}>
                <Statistic title="IC_IR" value={series?.summary.ic_ir ?? undefined} precision={3} />
              </Col>
              <Col span={6}>
                <Statistic title="IC 胜率" value={formatPercent(series?.summary.ic_positive_ratio)} />
              </Col>
              <Col span={6}>
                <Statistic title="IC 标准差" value={series?.summary.ic_std ?? undefined} precision={4} />
              </Col>
            </Row>

            <Card size="small" title={`RankIC 序列（${forwardPeriod} 日持有期，${points.length} 个交易日）`}>
              <EChart option={icOption} height={340} />
            </Card>

            <Card size="small" title="因子衰减（各持有期的 IC 均值）">
              <EChart option={decayOption} height={260} empty={decayItems.length === 0} />
              {decayItems.length === 0 && (
                <Typography.Text type="secondary">该因子只有一个持有期的记录，暂无衰减对比。</Typography.Text>
              )}
            </Card>
          </Flex>
        ) : (
          <Alert
            type="info"
            showIcon
            message="尚无 IC 数据"
            description={
              <Flex vertical gap={8} align="flex-start">
                <span>该因子还没有计算过 IC。IC 序列落库后，这里会显示曲线、IC_IR 与胜率。</span>
                <Button type="primary" size="small" loading={submitting} disabled={!factor} onClick={() => void submitCompute()}>
                  提交 IC 计算任务
                </Button>
              </Flex>
            }
          />
        )}

        {!factor && <Empty description="请先在因子库中选择一个因子" />}
      </Card>
    </Flex>
  );
}
