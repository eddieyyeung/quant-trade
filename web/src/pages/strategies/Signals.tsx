import { useCallback, useEffect, useState } from 'react';
import { App as AntdApp, Alert, Button, Card, Descriptions, Flex, Table, Tag } from 'antd';
import type { TableProps } from 'antd';
import { Link, useParams } from 'react-router-dom';

import { STATUS_COLORS, STATUS_LABELS, type RunStatus } from '../../api/runs';
import { strategiesApi, type StrategySignalOrder, type StrategySignalPage } from '../../api/strategies';
import { formatCount, formatDate, formatTime } from '../../utils/format';
import StrategyNav from './StrategyNav';

const PAGE_SIZE = 100;

const columns: TableProps<StrategySignalOrder>['columns'] = [
  { title: '#', dataIndex: 'seq', key: 'seq', width: 70 },
  {
    title: '方向',
    dataIndex: 'direction',
    key: 'direction',
    width: 90,
    render: (direction: string) => <Tag color={direction === 'BUY' ? 'success' : 'error'}>{direction}</Tag>,
  },
  { title: '代码', dataIndex: 'ts_code', key: 'ts_code', width: 140 },
  {
    title: '目标仓位',
    dataIndex: 'target_pct',
    key: 'target_pct',
    width: 110,
    render: (value: number) => `${(value * 100).toFixed(1)}%`,
  },
  { title: '理由', dataIndex: 'reason', key: 'reason' },
];

export default function Signals() {
  const { runId = '' } = useParams();
  const { message } = AntdApp.useApp();

  const [data, setData] = useState<StrategySignalPage | null>(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [missing, setMissing] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  // React Router reuses this instance when only `:runId` changes, so without
  // this the previous run's signal table would sit under the next run's URL.
  useEffect(() => {
    setData(null);
    setPage(1);
    setMissing(false);
    setFailure(null);
    setLoading(true);
  }, [runId]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const body = await strategiesApi.signals(runId, { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE });
      setData(body);
      setMissing(false);
      setFailure(null);
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      if ((e as { status?: number }).status === 404) {
        setMissing(true);
      } else {
        // A toast disappears; without this the page would render nothing at
        // all, which reads as broken rather than as a failed request.
        setFailure(text);
        message.error(text);
      }
    } finally {
      setLoading(false);
    }
  }, [message, page, runId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (missing) {
    return (
      <Flex vertical gap={16}>
        <StrategyNav />
        <Alert
          type="error"
          showIcon
          message={`信号运行 ${runId} 不存在`}
          description="该运行没有产出信号，可能仍在排队、被取消，或这个标识不属于信号生成任务。"
          action={
            <Link to="/strategies/signals">
              <Button size="small">返回信号历史</Button>
            </Link>
          }
        />
      </Flex>
    );
  }

  if (loading && data === null) {
    return (
      <Flex vertical gap={16}>
        <StrategyNav />
        <Card loading />
      </Flex>
    );
  }

  if (failure !== null && data === null) {
    return (
      <Flex vertical gap={16}>
        <StrategyNav />
        <Alert
          type="error"
          showIcon
          message="信号加载失败"
          description={failure}
          action={
            <Flex gap={8}>
              <Button size="small" onClick={() => void load()}>
                重试
              </Button>
              <Link to="/strategies/signals">
                <Button size="small">返回信号历史</Button>
              </Link>
            </Flex>
          }
        />
      </Flex>
    );
  }

  if (data === null) return null;

  return (
    <Flex vertical gap={16}>
      <StrategyNav />

      <Card
        title={`信号 ${data.run_id}`}
        extra={
          <Link to="/strategies/signals">
            <Button size="small">返回信号历史</Button>
          </Link>
        }
      >
        <Descriptions column={2} size="small" bordered>
          <Descriptions.Item label="状态">
            <Tag color={STATUS_COLORS[data.status as RunStatus] ?? 'default'}>
              {STATUS_LABELS[data.status as RunStatus] ?? data.status}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="完成时间">
            {/* A cancelled run's `finished_at` is the moment it stopped, not a
                completion; labelling it 完成于 would misdate the result. */}
            {data.status === 'cancelled' ? '取消于 ' : '完成于 '}
            {formatTime(data.finished_at)}
          </Descriptions.Item>
          <Descriptions.Item label="策略">{data.strategy ?? '—'}</Descriptions.Item>
          {/* The date the engine actually used. When the request left it blank
              the service falls back to the most recent trade date, so showing
              what the caller submitted would name a date that took no part in
              the computation. */}
          <Descriptions.Item label="信号日期">{formatDate(data.signal_date)}</Descriptions.Item>
          <Descriptions.Item label="订单数">{formatCount(data.total)}</Descriptions.Item>
          <Descriptions.Item label="股票池">{formatCount(data.universe_size)}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card size="small" title={`调仓信号（共 ${data.total} 笔）`}>
        {data.total === 0 ? (
          <Alert
            type="info"
            showIcon
            message="无信号"
            description="该次运行没有产出任何订单——股票池为空，或区间内没有可用的因子数据。"
          />
        ) : (
          <Table<StrategySignalOrder>
            rowKey="seq"
            columns={columns}
            dataSource={data.orders}
            loading={loading}
            size="small"
            // Rows arrive in the engine's order and stay that way: the strategy
            // decides whether sells precede buys, and re-sorting would discard
            // that. Pagination is the server's.
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              total: data.total,
              showSizeChanger: false,
              showTotal: count => `共 ${count} 笔`,
              onChange: nextPage => setPage(nextPage),
            }}
          />
        )}
      </Card>
    </Flex>
  );
}
