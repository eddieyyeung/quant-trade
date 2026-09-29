import { useCallback, useEffect, useState } from 'react';
import { App as AntdApp, Alert, Button, Card, Flex, Modal, Table, Tag, Typography } from 'antd';
import type { TableProps } from 'antd';
import { Link } from 'react-router-dom';

import { simulatorApi, type SessionSummary } from '../../api/simulator';
import { formatDate } from '../../utils/format';
import CreateSession from './CreateSession';

/** Session statuses, as the backend names them. */
const STATUS_LABELS: Record<string, string> = {
  active: '进行中',
  paused: '已暂停',
  completed: '已完成',
};

const STATUS_COLORS: Record<string, string> = {
  active: 'processing',
  paused: 'warning',
  completed: 'success',
};

/**
 * The simulator's landing page: the create form and the session history, the
 * same two blocks the standalone app stacked before it was folded into the
 * platform shell.
 */
export default function SessionList() {
  const { message } = AntdApp.useApp();

  const [rows, setRows] = useState<SessionSummary[]>([]);
  // Starts true: the fetch runs from an effect, so a false initial value paints
  // the "无会话" state for a frame on every visit.
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await simulatorApi.listSessions());
      setFailure(null);
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      // Tracked, not just toasted: the empty state below would otherwise read
      // "无会话" for a request that failed.
      setFailure(text);
      message.error(text);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  const remove = (session: SessionSummary) => {
    Modal.confirm({
      title: '删除会话',
      content: `确认删除会话「${session.name}」？该会话的决策记录会一并删除，且不可恢复。`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        setDeleting(session.id);
        try {
          await simulatorApi.deleteSession(session.id);
          setRows(previous => previous.filter(row => row.id !== session.id));
          message.success('会话已删除');
        } catch (e) {
          // The row stays: a delete that failed must not look like one that
          // worked, or the list would disagree with the server.
          message.error(e instanceof Error ? e.message : String(e));
        } finally {
          setDeleting(null);
        }
      },
    });
  };

  const columns: TableProps<SessionSummary>['columns'] = [
    {
      title: '会话',
      dataIndex: 'id',
      key: 'id',
      width: 140,
      render: (id: string) => <Typography.Text code>{id.slice(0, 8)}…</Typography.Text>,
    },
    { title: '名称', dataIndex: 'name', key: 'name', width: 180 },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      // An unrecognised status still gets a label — a blank cell would read as
      // a rendering bug rather than as a status this build does not know.
      render: (status: string) => (
        <Tag color={STATUS_COLORS[status] ?? 'default'}>{STATUS_LABELS[status] ?? status}</Tag>
      ),
    },
    {
      title: '当前日期',
      dataIndex: 'cursor_date',
      key: 'cursor_date',
      width: 130,
      render: (value: string | null) => formatDate(value),
    },
    {
      title: '参考策略',
      dataIndex: 'reference_strategy',
      key: 'reference_strategy',
      width: 160,
      render: (value: string | null) => value || '—',
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      fixed: 'right',
      render: (_, row) => (
        <Flex gap={8}>
          <Link to={`/simulator/${encodeURIComponent(row.id)}`}>查看</Link>
          <Button size="small" danger loading={deleting === row.id} onClick={() => remove(row)}>
            删除
          </Button>
        </Flex>
      ),
    },
  ];

  return (
    <Flex vertical gap={16}>
      {/* Creating navigates to the new session's desk, so there is nothing to
          refresh here; coming back remounts the page and refetches anyway. */}
      <CreateSession />

      <Card title={`会话列表（${rows.length}）`} extra={<Button size="small" onClick={() => void load()} loading={loading}>刷新</Button>}>
        {failure !== null ? (
          <Alert
            type="error"
            showIcon
            message="会话列表加载失败"
            description={failure}
            action={
              <Button size="small" onClick={() => void load()}>
                重试
              </Button>
            }
          />
        ) : !loading && rows.length === 0 ? (
          <Alert type="info" showIcon message="无会话" description="在上面的表单里填写名称与起始日期，点击「创建」后会话会出现在这里。" />
        ) : (
          <Table<SessionSummary>
            rowKey="id"
            columns={columns}
            dataSource={rows}
            loading={loading}
            size="small"
            scroll={{ x: 850 }}
            pagination={false}
          />
        )}
      </Card>
    </Flex>
  );
}
