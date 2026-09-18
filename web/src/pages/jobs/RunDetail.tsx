import { useCallback, useEffect, useRef, useState } from 'react';
import {
  App as AntdApp,
  Alert,
  Button,
  Card,
  Descriptions,
  Flex,
  Progress,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { TableProps } from 'antd';
import { Link, useParams } from 'react-router-dom';

import {
  STATUS_COLORS,
  STATUS_LABELS,
  isTerminal,
  runsApi,
  type Artifact,
  type RunDetail as RunDetailData,
  type RunLogLine,
} from '../../api/runs';
import { formatCount, formatTime } from '../../utils/format';

/** How often to re-check while the run is still pending or running. */
const LIVE_POLL_MS = 2000;

export default function RunDetail() {
  const { runId = '' } = useParams();
  const { message } = AntdApp.useApp();

  const [run, setRun] = useState<RunDetailData | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [cancelling, setCancelling] = useState(false);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const [detail, files] = await Promise.all([runsApi.get(runId), runsApi.artifacts(runId)]);
      setRun(detail);
      setArtifacts(files);
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [message, runId]);

  useEffect(() => {
    void load();
  }, [load]);

  const live = run !== null && !isTerminal(run.status);
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => void load(), LIVE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [live, load]);

  const cancel = async () => {
    setCancelling(true);
    try {
      await runsApi.cancel(runId);
      message.success('已请求取消，任务会在下一个检查点停止');
      await load();
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setCancelling(false);
    }
  };

  if (loading && run === null) {
    return (
      <Flex justify="center" style={{ padding: 48 }}>
        <Spin />
      </Flex>
    );
  }

  if (run === null) {
    return (
      <Alert
        type="error"
        showIcon
        message="运行不存在或已被清理"
        description={runId}
        action={
          <Link to="/jobs">
            <Button size="small">返回任务中心</Button>
          </Link>
        }
      />
    );
  }

  const artifactColumns: TableProps<Artifact>['columns'] = [
    { title: '类型', dataIndex: 'kind', key: 'kind', width: 120 },
    {
      title: '存储',
      dataIndex: 'storage',
      key: 'storage',
      width: 100,
      render: (storage: Artifact['storage']) => <Tag>{storage}</Tag>,
    },
    {
      title: '位置',
      dataIndex: 'ref',
      key: 'ref',
      width: 320,
      render: (ref: string) => <Typography.Text code>{ref}</Typography.Text>,
    },
    {
      title: '行数',
      dataIndex: 'row_count',
      key: 'row_count',
      width: 120,
      render: (rows: number | null) => formatCount(rows),
    },
  ];

  return (
    <Flex vertical gap={16}>
      <Card
        title={
          <Flex gap={12} align="center">
            <span>{run.kind}</span>
            <Tag color={STATUS_COLORS[run.status]}>{STATUS_LABELS[run.status]}</Tag>
          </Flex>
        }
        extra={
          <Flex gap={8}>
            <Link to="/jobs">
              <Button size="small">返回列表</Button>
            </Link>
            {run.status === 'running' && (
              <Button size="small" danger loading={cancelling} onClick={() => void cancel()}>
                取消运行
              </Button>
            )}
          </Flex>
        }
      >
        <Descriptions column={2} size="small" bordered>
          <Descriptions.Item label="运行 ID" span={2}>
            <Typography.Text code copyable>
              {run.run_id}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="进度" span={2}>
            <Progress percent={Math.round(run.progress * 100)} size="small" status={run.status === 'failed' ? 'exception' : undefined} />
            {run.message && <Typography.Text type="secondary">{run.message}</Typography.Text>}
          </Descriptions.Item>
          <Descriptions.Item label="触发方式">{run.trigger}</Descriptions.Item>
          <Descriptions.Item label="提交时间">{formatTime(run.created_at)}</Descriptions.Item>
          <Descriptions.Item label="开始时间">{formatTime(run.started_at)}</Descriptions.Item>
          <Descriptions.Item label="结束时间">{formatTime(run.finished_at)}</Descriptions.Item>
          <Descriptions.Item label="参数" span={2}>
            <Typography.Text code style={{ whiteSpace: 'pre-wrap' }}>
              {JSON.stringify(run.params, null, 2)}
            </Typography.Text>
          </Descriptions.Item>
        </Descriptions>
        {run.error && (
          <Alert style={{ marginTop: 16 }} type="error" showIcon message="运行失败" description={run.error} />
        )}
      </Card>

      <Card title="日志" extra={<Typography.Text type="secondary">{live ? '实时追加中' : '已结束'}</Typography.Text>}>
        <LogPanel runId={run.run_id} onEnded={() => void load()} />
      </Card>

      <Card title={`产物（${artifacts.length}）`}>
        <Table<Artifact>
          rowKey="artifact_id"
          columns={artifactColumns}
          dataSource={artifacts}
          size="small"
          virtual
          scroll={{ x: 660, y: 320 }}
          pagination={false}
          locale={{ emptyText: '该运行未登记产物' }}
        />
      </Card>
    </Flex>
  );
}

const LEVEL_COLORS: Record<string, string> = {
  info: 'inherit',
  warning: '#d46b08',
  error: '#cf1322',
};

/**
 * The run's log, fed by `GET /api/runs/{id}/logs` over SSE.
 *
 * EventSource reconnects on its own, and each reconnect replays the run from
 * the beginning — so lines are keyed by their `seq` and inserted once, which
 * makes a reconnect a no-op rather than a duplicate.
 */
function LogPanel({ runId, onEnded }: { runId: string; onEnded: () => void }) {
  const [lines, setLines] = useState<RunLogLine[]>([]);
  const bottom = useRef<HTMLDivElement>(null);

  // Keep the latest `onEnded` in a ref so the stream is subscribed once per
  // run, rather than being torn down and replayed on every parent re-render.
  const endedRef = useRef(onEnded);
  useEffect(() => {
    endedRef.current = onEnded;
  }, [onEnded]);

  useEffect(() => {
    setLines([]);

    const source = new EventSource(runsApi.logUrl(runId));

    const onLog = (event: MessageEvent<string>) => {
      const line = JSON.parse(event.data) as RunLogLine;
      setLines(previous => (previous.some(entry => entry.seq === line.seq) ? previous : [...previous, line]));
    };
    const onEnd = () => {
      source.close();
      endedRef.current();
    };

    source.addEventListener('log', onLog as EventListener);
    source.addEventListener('end', onEnd);
    // No `onerror` handler: EventSource retries transient failures itself, and
    // a terminal run always arrives through `end`.
    return () => source.close();
  }, [runId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' });
  }, [lines.length]);

  return (
    <div
      style={{
        maxHeight: 420,
        minHeight: 120,
        overflow: 'auto',
        background: '#fafafa',
        border: '1px solid #f0f0f0',
        borderRadius: 6,
        padding: 12,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: 12,
        lineHeight: 1.7,
      }}
    >
      {lines.length === 0 && <Typography.Text type="secondary">暂无日志</Typography.Text>}
      {lines.map(line => (
        <div key={line.seq} style={{ color: LEVEL_COLORS[line.level] ?? 'inherit', whiteSpace: 'pre-wrap' }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {formatTime(line.ts)}
          </Typography.Text>{' '}
          [{line.level}] {line.message}
        </div>
      ))}
      <div ref={bottom} />
    </div>
  );
}
