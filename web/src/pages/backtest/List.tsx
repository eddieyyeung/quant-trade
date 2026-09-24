import { useCallback, useEffect, useRef, useState } from 'react';
import {
  App as AntdApp,
  Alert,
  Button,
  Card,
  DatePicker,
  Flex,
  Form,
  Input,
  InputNumber,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { TableProps } from 'antd';
import type { Dayjs } from 'dayjs';
import { Link, useNavigate } from 'react-router-dom';

import { backtestsApi, type BacktestRunSummary } from '../../api/backtests';
import { STATUS_COLORS, STATUS_LABELS, isTerminal, runsApi, type RunStatus } from '../../api/runs';
import { formatDate, formatPercent, formatTime } from '../../utils/format';
import BacktestNav from './BacktestNav';

const { RangePicker } = DatePicker;

const MAX_COMPARISON_RUNS = 8;
const PAGE_SIZE_OPTIONS = [20, 50, 100, 200];
/** How often to re-check while a submitted run is still going. */
const LIVE_POLL_MS = 3000;

interface FormValues {
  window?: [Dayjs | null, Dayjs | null] | null;
  strategy?: string;
  initial_capital?: number;
  benchmark?: string;
  top_n?: number;
}

function statusTag(status: string) {
  const key = status as RunStatus;
  return <Tag color={STATUS_COLORS[key] ?? 'default'}>{STATUS_LABELS[key] ?? status}</Tag>;
}

export default function List() {
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();
  const [form] = Form.useForm<FormValues>();

  const [strategies, setStrategies] = useState<string[]>([]);
  const [rows, setRows] = useState<BacktestRunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  // Starts true: the fetch is issued from an effect, so a false initial value
  // paints the "no backtests yet" state for a frame on every visit.
  const [loading, setLoading] = useState(true);
  const [listFailure, setListFailure] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const /** True while a refresh is already queued, so polls cannot pile up. */
    inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const result = await backtestsApi.list({ limit: pageSize, offset: (page - 1) * pageSize });
      setRows(result.items);
      setTotal(result.total);
      setListFailure(null);
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      // Tracked, not just toasted: the empty state below would otherwise claim
      // "尚无回测" for a request that failed.
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

  useEffect(() => {
    backtestsApi
      .strategies()
      .then(body => {
        setStrategies(body.items);
        form.setFieldValue('strategy', body.default);
      })
      .catch((e: unknown) => message.error(e instanceof Error ? e.message : String(e)));
  }, [form, message]);

  // Only poll while a run can still change; a settled history page is static.
  const live = rows.some(row => !isTerminal(row.status as RunStatus));
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => void load(), LIVE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [live, load]);

  const submit = async (values: FormValues) => {
    const [start, end] = values.window ?? [null, null];
    setSubmitting(true);
    try {
      const run = await runsApi.submit('backtest', {
        start: start ? start.format('YYYY-MM-DD') : null,
        end: end ? end.format('YYYY-MM-DD') : null,
        strategy: values.strategy ?? null,
        initial_capital: values.initial_capital ?? null,
        benchmark: values.benchmark || null,
        top_n: values.top_n ?? null,
      });
      message.success('回测任务已提交，正在跳转任务中心');
      navigate(`/jobs/${run.run_id}`);
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const goToComparison = () => {
    if (selected.length === 0) {
      message.warning('请先勾选至少一个回测运行');
      return;
    }
    if (selected.length > MAX_COMPARISON_RUNS) {
      message.warning(`最多对比 ${MAX_COMPARISON_RUNS} 个运行，当前选中 ${selected.length} 个`);
      return;
    }
    navigate(`/backtest/compare?runs=${selected.map(encodeURIComponent).join(',')}`);
  };

  const columns: TableProps<BacktestRunSummary>['columns'] = [
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => statusTag(status),
    },
    {
      title: '策略',
      dataIndex: 'strategy',
      key: 'strategy',
      width: 160,
      render: (value: string | null) => value || '—',
    },
    {
      title: '实际区间',
      key: 'window',
      width: 200,
      render: (_, row) => `${formatDate(row.start)} ~ ${formatDate(row.end)}`,
    },
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
    {
      title: '净值点',
      dataIndex: 'nav_points',
      key: 'nav_points',
      width: 90,
    },
    {
      title: '年化收益',
      key: 'annual_return',
      width: 110,
      render: (_, row) => formatPercent(row.metrics.annual_return),
    },
    {
      title: '最大回撤',
      key: 'max_drawdown',
      width: 110,
      render: (_, row) => formatPercent(row.metrics.max_drawdown),
    },
    {
      title: '夏普',
      key: 'sharpe_ratio',
      width: 90,
      render: (_, row) =>
        row.metrics.sharpe_ratio === undefined ? '—' : row.metrics.sharpe_ratio.toFixed(2),
    },
    {
      title: '提交时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: formatTime,
    },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      fixed: 'right',
      render: (_, row) => <Link to={`/backtest/${row.run_id}`}>详情</Link>,
    },
  ];

  return (
    <Flex vertical gap={16}>
      <BacktestNav />

      <Card title="发起回测">
        <Form<FormValues>
          form={form}
          layout="inline"
          initialValues={{ window: null, benchmark: '000300.SH' }}
          onFinish={values => void submit(values)}
        >
          <Form.Item
            name="window"
            label="区间"
            rules={[
              {
                validator: (_rule, value: [Dayjs | null, Dayjs | null] | null) => {
                  const [start, end] = value ?? [null, null];
                  if (start && end && start.isAfter(end)) {
                    return Promise.reject(new Error('起始日期不能晚于结束日期'));
                  }
                  return Promise.resolve();
                },
              },
            ]}
          >
            <RangePicker allowEmpty={[true, true]} />
          </Form.Item>
          <Form.Item name="strategy" label="策略">
            <Select
              style={{ width: 180 }}
              placeholder="选择策略"
              options={strategies.map(name => ({ value: name, label: name }))}
            />
          </Form.Item>
          <Form.Item name="initial_capital" label="初始资金">
            <InputNumber min={1000} step={10000} style={{ width: 140 }} placeholder="配置默认" />
          </Form.Item>
          <Form.Item name="benchmark" label="基准">
            <Input style={{ width: 130 }} placeholder="000300.SH" />
          </Form.Item>
          <Form.Item name="top_n" label="持仓数">
            <InputNumber min={1} style={{ width: 100 }} placeholder="配置默认" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={submitting}>
              发起回测
            </Button>
          </Form.Item>
        </Form>
      </Card>

      <Card
        title="回测历史"
        extra={
          <Space>
            <Typography.Text type="secondary">已选 {selected.length} 个</Typography.Text>
            <Button size="small" onClick={goToComparison}>
              对比选中
            </Button>
            <Button size="small" onClick={() => void load()} loading={loading}>
              刷新
            </Button>
          </Space>
        }
      >
        {/* An empty table is a header row with nothing under it. When there is
            nothing to show at all, show the explanation instead — and a failed
            request is an error, not an empty database. */}
        {listFailure !== null ? (
          <Alert
            type="error"
            showIcon
            message="回测列表加载失败"
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
            message="尚无回测"
            description="在上面的表单里选择区间与策略，点击「发起回测」后结果会出现在这里。"
          />
        ) : (
          <Table<BacktestRunSummary>
            rowKey="run_id"
            columns={columns}
            dataSource={rows}
            loading={loading}
            size="small"
            // Virtual scrolling keeps a 200-row page responsive, and needs a fixed
            // body height plus an explicit width on every column.
            virtual
            scroll={{ x: 1330, y: 480 }}
            rowSelection={{
              selectedRowKeys: selected,
              onChange: keys => setSelected(keys.map(String)),
            }}
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
        )}
      </Card>
    </Flex>
  );
}
