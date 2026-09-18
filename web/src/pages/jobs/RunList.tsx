import { useCallback, useEffect, useRef, useState } from 'react';
import { App as AntdApp, Button, Card, Flex, Progress, Table, Tag, Typography } from 'antd';
import type { TableProps } from 'antd';
import { Link, useNavigate } from 'react-router-dom';

import { STATUS_COLORS, STATUS_LABELS, isTerminal, runsApi, type RunSummary } from '../../api/runs';
import { formatTime } from '../../utils/format';

/** How often to re-check while something is still pending or running. */
const LIVE_POLL_MS = 2000;

const PAGE_SIZE_OPTIONS = [20, 50, 100, 200];

export default function RunList() {
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();

  const [rows, setRows] = useState<RunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);
  const [cancelling, setCancelling] = useState<string | null>(null);
  /** True while a refresh is already queued, so polls cannot pile up. */
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const result = await runsApi.list(pageSize, (page - 1) * pageSize);
      setRows(result.items);
      setTotal(result.total);
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [message, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  // Only poll while something can still change; an idle history page is static.
  const live = rows.some(row => !isTerminal(row.status));
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => void load(), LIVE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [live, load]);

  const cancel = async (runId: string) => {
    setCancelling(runId);
    try {
      await runsApi.cancel(runId);
      message.success('已请求取消，任务会在下一个检查点停止');
      await load();
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setCancelling(null);
    }
  };

  const columns: TableProps<RunSummary>['columns'] = [
    {
      title: '任务类型',
      dataIndex: 'kind',
      key: 'kind',
      width: 140,
      render: (kind: string, row) => <Link to={`/jobs/${row.run_id}`}>{kind}</Link>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: RunSummary['status']) => <Tag color={STATUS_COLORS[status]}>{STATUS_LABELS[status]}</Tag>,
    },
    {
      title: '进度',
      dataIndex: 'progress',
      key: 'progress',
      width: 180,
      render: (progress: number, row) => (
        <Progress percent={Math.round(progress * 100)} size="small" status={row.status === 'failed' ? 'exception' : undefined} />
      ),
    },
    {
      title: '当前步骤',
      dataIndex: 'message',
      key: 'message',
      width: 260,
      render: (text: string) => (
        <Typography.Text type="secondary" ellipsis={{ tooltip: text }}>
          {text || '—'}
        </Typography.Text>
      ),
    },
    {
      title: '提交时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: formatTime,
    },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      key: 'started_at',
      width: 180,
      render: formatTime,
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      fixed: 'right',
      render: (_, row) => (
        <Flex gap={8}>
          <Button size="small" onClick={() => navigate(`/jobs/${row.run_id}`)}>
            详情
          </Button>
          {row.status === 'running' && (
            <Button
              size="small"
              danger
              loading={cancelling === row.run_id}
              onClick={() => void cancel(row.run_id)}
            >
              取消
            </Button>
          )}
        </Flex>
      ),
    },
  ];

  return (
    <Card
      title="任务中心"
      extra={
        <Flex gap={12} align="center">
          <Typography.Text type="secondary">共 {total} 条运行记录</Typography.Text>
          <Button size="small" onClick={() => void load()} loading={loading}>
            刷新
          </Button>
        </Flex>
      }
    >
      <Table<RunSummary>
        rowKey="run_id"
        columns={columns}
        dataSource={rows}
        loading={loading}
        size="small"
        // Virtual scrolling keeps a 200-row page responsive, and needs a fixed
        // body height plus an explicit width on every column.
        virtual
        scroll={{ x: 1180, y: 520 }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: PAGE_SIZE_OPTIONS,
          showTotal: count => `共 ${count} 条`,
          onChange: (nextPage, nextSize) => {
            setPageSize(nextSize);
            // Changing the page size must not leave us on an out-of-range page.
            setPage(nextSize === pageSize ? nextPage : 1);
          },
        }}
      />
    </Card>
  );
}
