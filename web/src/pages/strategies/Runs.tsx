import { useCallback, useEffect, useRef, useState } from 'react';
import { App as AntdApp, Alert, Button, Card, Flex, Progress, Table, Tag, Typography } from 'antd';
import type { TableProps } from 'antd';
import { Link } from 'react-router-dom';

import { STATUS_COLORS, STATUS_LABELS, isTerminal, type RunStatus } from '../../api/runs';
import { strategiesApi, type StrategyRunSummary } from '../../api/strategies';
import { formatCount, formatDate, formatTime } from '../../utils/format';
import StrategyNav from './StrategyNav';

const PAGE_SIZE_OPTIONS = [20, 50, 100, 200];
/** How often to re-check while a run can still change. */
const LIVE_POLL_MS = 3000;

function statusTag(status: string) {
  const key = status as RunStatus;
  return <Tag color={STATUS_COLORS[key] ?? 'default'}>{STATUS_LABELS[key] ?? status}</Tag>;
}

export default function Runs() {
  const { message } = AntdApp.useApp();

  const [rows, setRows] = useState<StrategyRunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  // Starts true: the fetch runs from an effect, so a false initial value paints
  // the empty state for a frame on every visit.
  const [loading, setLoading] = useState(true);
  const [listFailure, setListFailure] = useState<string | null>(null);
  /** True while a refresh is already queued, so polls cannot pile up. */
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const result = await strategiesApi.runs({ limit: pageSize, offset: (page - 1) * pageSize });
      setRows(result.items);
      setTotal(result.total);
      setListFailure(null);
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      // Tracked, not just toasted: the empty state below would otherwise claim
      // there are no runs for a request that failed.
      setListFailure(text);
      message.error(text);
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [message, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  // Only poll while a run can still change; a settled history page is static.
  const live = rows.some(row => !isTerminal(row.status as RunStatus));
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => void load(), LIVE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [live, load]);

  const columns: TableProps<StrategyRunSummary>['columns'] = [
    { title: '状态', dataIndex: 'status', key: 'status', width: 100, render: statusTag },
    {
      title: '策略',
      dataIndex: 'strategy',
      key: 'strategy',
      width: 160,
      render: (value: string | null) => value || '—',
    },
    {
      title: '信号日期',
      dataIndex: 'signal_date',
      key: 'signal_date',
      width: 130,
      // Absent until the run has written its signals; `formatDate` renders the
      // em dash for null rather than inventing a date.
      render: formatDate,
    },
    { title: '订单数', dataIndex: 'order_count', key: 'order_count', width: 100, render: formatCount },
    { title: '股票池', dataIndex: 'universe_size', key: 'universe_size', width: 100, render: formatCount },
    {
      title: '进度',
      key: 'progress',
      width: 150,
      render: (_, row) =>
        isTerminal(row.status as RunStatus) ? (
          <Typography.Text type="secondary">—</Typography.Text>
        ) : (
          <Progress percent={Math.round(row.progress * 100)} size="small" />
        ),
    },
    { title: '提交时间', dataIndex: 'created_at', key: 'created_at', width: 180, render: formatTime },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      fixed: 'right',
      // Gated on the run having stopped, not on it having succeeded. A run
      // cancelled after writing its signals still has signals; a run that
      // finished and selected nothing still has something to say ("no
      // signals"), and gating on `order_count` would hide that answer behind
      // an em dash. A run still going has nothing to show yet.
      render: (_, row) =>
        isTerminal(row.status as RunStatus) ? (
          <Link to={`/strategies/signals/${encodeURIComponent(row.run_id)}`}>详情</Link>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        ),
    },
  ];

  return (
    <Flex vertical gap={16}>
      <StrategyNav />

      <Card
        title="信号生成历史"
        extra={
          <Button size="small" onClick={() => void load()} loading={loading}>
            刷新
          </Button>
        }
      >
        {listFailure !== null ? (
          <Alert
            type="error"
            showIcon
            message="信号运行列表加载失败"
            description={listFailure}
            action={
              <Button size="small" onClick={() => void load()}>
                重试
              </Button>
            }
          />
        ) : !loading && total === 0 ? (
          <Alert
            type="info"
            showIcon
            message="尚无信号生成"
            description="选择策略并发起一次信号生成，结果会出现在这里。"
            // A way there, not just a sentence saying to go there.
            action={
              <Link to="/strategies">
                <Button size="small">去发起</Button>
              </Link>
            }
          />
        ) : (
          <Table<StrategyRunSummary>
            rowKey="run_id"
            columns={columns}
            dataSource={rows}
            loading={loading}
            size="small"
            virtual
            scroll={{ x: 1120, y: 480 }}
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: true,
              pageSizeOptions: PAGE_SIZE_OPTIONS,
              showTotal: count => `共 ${count} 条`,
              onChange: (nextPage, nextSize) => {
                setPageSize(nextSize);
                // Changing the page size must not leave us out of range.
                setPage(nextSize === pageSize ? nextPage : 1);
              },
            }}
          />
        )}
      </Card>
    </Flex>
  );
}
