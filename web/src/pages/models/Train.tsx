import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  App as AntdApp,
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  Progress,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { TableProps } from 'antd';
import type { Dayjs } from 'dayjs';
import { Link, useNavigate } from 'react-router-dom';

import { factorsApi } from '../../api/factors';
import { modelsApi, type ModelRunSummary } from '../../api/models';
import { STATUS_COLORS, STATUS_LABELS, isTerminal, runsApi } from '../../api/runs';
import { formatCount, formatDate, formatPercent, formatTime } from '../../utils/format';
import ModelNav from './ModelNav';

const { RangePicker } = DatePicker;

const PAGE_SIZES = [20, 50, 100, 200];
/** How often to re-check while a submitted run is still going. */
const LIVE_POLL_MS = 3000;

interface FormValues {
  window?: [Dayjs | null, Dayjs | null] | null;
  factors?: string[];
  universe?: string[];
  output_path?: string;
  train_years?: number;
  valid_years?: number;
  predict_months?: number;
  early_stopping?: number;
  num_boost_round?: number;
  lgb_params?: {
    num_leaves?: number;
    min_data_in_leaf?: number;
    learning_rate?: number;
    feature_fraction?: number;
    bagging_fraction?: number;
    bagging_freq?: number;
  };
}

/** Drop the fields the user left blank, so the server keeps its own default. */
function compact(entries: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(entries).filter(([, value]) => value !== undefined && value !== null && value !== ''),
  );
}

/**
 * The `model_train` params the form describes.
 *
 * A blank field is left out rather than sent as null: the window lengths, the
 * hyperparameters and even the output path have server-side defaults, and the
 * page does not get to decide what those are.
 */
function buildParams(values: FormValues): Record<string, unknown> {
  const [start, end] = values.window ?? [null, null];
  const lgb_params = compact({ ...(values.lgb_params ?? {}) });
  return compact({
    start: start ? start.format('YYYY-MM-DD') : undefined,
    end: end ? end.format('YYYY-MM-DD') : undefined,
    factors: values.factors?.length ? values.factors : undefined,
    universe: values.universe?.length ? values.universe : undefined,
    output_path: values.output_path?.trim() || undefined,
    train_years: values.train_years,
    valid_years: values.valid_years,
    predict_months: values.predict_months,
    early_stopping: values.early_stopping,
    num_boost_round: values.num_boost_round,
    lgb_params: Object.keys(lgb_params).length > 0 ? lgb_params : undefined,
  });
}

/** A metric that may be absent — a run with no rankable day has no IC at all. */
function metric(value: number | null, digits: number): string {
  return value === null ? '—' : value.toFixed(digits);
}

export default function Train() {
  const { message, modal } = AntdApp.useApp();
  const navigate = useNavigate();
  const [form] = Form.useForm<FormValues>();
  const formAnchor = useRef<HTMLDivElement>(null);

  const [factorOptions, setFactorOptions] = useState<string[]>([]);
  const [rows, setRows] = useState<ModelRunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const /** True while a refresh is already queued, so polls cannot pile up. */
    inFlight = useRef(false);

  // The catalogue supplies the options; only persisted factors can be trained on.
  useEffect(() => {
    factorsApi
      .list({ limit: 200, offset: 0 })
      .then(body => setFactorOptions(body.items.filter(row => row.persisted).map(row => row.name)))
      .catch((e: unknown) => message.error(e instanceof Error ? e.message : String(e)));
  }, [message]);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const body = await modelsApi.listRuns({ limit: pageSize, offset: (page - 1) * pageSize });
      setRows(body.items);
      setTotal(body.total);
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

  // Only poll while a run can still change; a settled history page is static.
  const live = rows.some(row => !isTerminal(row.status));
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => void load(), LIVE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [live, load]);

  const submit = async (values: FormValues) => {
    const [start, end] = values.window ?? [null, null];
    // The range picker already rejects an inverted window; this keeps the
    // promise that no request is issued independent of the form's wiring.
    if (start && end && start.isAfter(end)) {
      message.error('起始日期不能晚于结束日期');
      return;
    }
    setSubmitting(true);
    try {
      const run = await runsApi.submit('model_train', buildParams(values));
      message.success('训练任务已提交，正在跳转任务中心');
      navigate(`/jobs/${run.run_id}`);
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const submitDefaults = () => {
    modal.confirm({
      title: '用服务端默认参数发起训练',
      content: '区间、walk-forward 窗口与 LightGBM 超参数全部由服务端决定。训练是长任务，提交后请到任务中心查看进度与日志。',
      okText: '发起训练',
      cancelText: '取消',
      onOk: () => submit({}),
    });
  };

  const focusForm = () => formAnchor.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const columns: TableProps<ModelRunSummary>['columns'] = useMemo(
    () => [
      {
        title: '状态',
        dataIndex: 'status',
        key: 'status',
        width: 150,
        render: (status: ModelRunSummary['status'], row: ModelRunSummary) => (
          <Space size={4}>
            <Tag color={STATUS_COLORS[status] ?? 'default'}>{STATUS_LABELS[status] ?? status}</Tag>
            {/* A terminal run with no predictions produced nothing to evaluate;
                a running one has simply not written its rows yet. */}
            {isTerminal(status) && row.prediction_rows === 0 && <Tag color="warning">无产出</Tag>}
          </Space>
        ),
      },
      {
        title: '进度',
        dataIndex: 'windows_trained',
        key: 'windows_trained',
        width: 190,
        // While a run is going, every other column on the row is still empty —
        // the results are only written at the end. The run's own progress is
        // the one thing that moves, so the column shows that instead.
        render: (trained: number, row: ModelRunSummary) =>
          isTerminal(row.status) ? (
            <Typography.Text>{formatCount(trained)} 个窗口</Typography.Text>
          ) : (
            <Space direction="vertical" size={0}>
              <Progress percent={Math.round(row.progress * 100)} size="small" />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {row.message || '进行中'}
              </Typography.Text>
            </Space>
          ),
      },
      {
        title: '实际区间',
        key: 'window',
        width: 210,
        render: (_, row) => `${formatDate(row.start)} ~ ${formatDate(row.end)}`,
      },
      {
        title: '有效天数',
        dataIndex: 'ic_days',
        key: 'ic_days',
        width: 110,
        render: formatCount,
      },
      {
        title: '预测行数',
        dataIndex: 'prediction_rows',
        key: 'prediction_rows',
        width: 110,
        render: formatCount,
      },
      {
        title: 'IC 均值',
        dataIndex: 'ic_mean',
        key: 'ic_mean',
        width: 110,
        render: (value: number | null) => metric(value, 4),
      },
      {
        title: 'IC_IR',
        dataIndex: 'ic_ir',
        key: 'ic_ir',
        width: 100,
        render: (value: number | null) => metric(value, 3),
      },
      {
        title: '正 IC 占比',
        dataIndex: 'ic_positive_ratio',
        key: 'ic_positive_ratio',
        width: 120,
        render: (value: number | null) => formatPercent(value),
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
        width: 90,
        fixed: 'right',
        render: (_, row) => <Link to={`/models/evaluate/${encodeURIComponent(row.run_id)}`}>评估</Link>,
      },
    ],
    [],
  );

  return (
    <Flex vertical gap={16}>
      <ModelNav />

      <div ref={formAnchor}>
        <Card title="发起训练">
          <Typography.Paragraph type="secondary">
            训练是长任务：提交后跳转到任务中心，可在那里看到按窗口推进的进度与实时日志。未填写的字段不会随请求发出，由服务端决定取值。
          </Typography.Paragraph>

          <Form<FormValues> form={form} layout="vertical" onFinish={values => void submit(values)}>
            <Card size="small" title="数据与因子">
              <Row gutter={16}>
                <Col xs={24} md={12} lg={8}>
                  <Form.Item
                    name="window"
                    label="信号区间"
                    extra="不填表示从服务端默认起始日到最新交易日。"
                    rules={[
                      {
                        validator: (_rule, value: FormValues['window']) => {
                          const [start, end] = value ?? [null, null];
                          if (start && end && start.isAfter(end)) {
                            return Promise.reject(new Error('起始日期不能晚于结束日期'));
                          }
                          return Promise.resolve();
                        },
                      },
                    ]}
                  >
                    <RangePicker style={{ width: '100%' }} allowEmpty={[true, true]} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12} lg={8}>
                  <Form.Item name="factors" label="因子子集" extra="不选表示使用特征构建器的默认因子集。">
                    <Select
                      mode="multiple"
                      showSearch
                      allowClear
                      maxTagCount="responsive"
                      placeholder="全部默认因子"
                      options={factorOptions.map(name => ({ value: name, label: name }))}
                    />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12} lg={8}>
                  <Form.Item
                    name="universe"
                    label="股票池"
                    extra="输入股票代码后回车添加；留空表示使用默认股票池（指数成分股）。"
                  >
                    <Select mode="tags" allowClear placeholder="默认股票池" tokenSeparators={[',', ' ']} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12} lg={8}>
                  <Form.Item
                    name="output_path"
                    label="预测输出路径"
                    extra="留空表示使用服务端默认路径；两次训练写同一路径时后者覆盖前者。"
                  >
                    <Input placeholder="服务端默认" />
                  </Form.Item>
                </Col>
              </Row>
            </Card>

            <Card size="small" title="Walk-forward 窗口" style={{ marginTop: 16 }}>
              <Row gutter={16}>
                <Col xs={12} md={8} lg={4}>
                  <Form.Item name="train_years" label="训练窗口（年）">
                    <InputNumber min={0.5} max={30} step={0.5} style={{ width: '100%' }} placeholder="服务端默认" />
                  </Form.Item>
                </Col>
                <Col xs={12} md={8} lg={4}>
                  <Form.Item name="valid_years" label="验证窗口（年）">
                    <InputNumber min={0.25} max={10} step={0.25} style={{ width: '100%' }} placeholder="服务端默认" />
                  </Form.Item>
                </Col>
                <Col xs={12} md={8} lg={4}>
                  <Form.Item name="predict_months" label="预测跨度（月）">
                    <InputNumber min={1} max={36} style={{ width: '100%' }} placeholder="服务端默认" />
                  </Form.Item>
                </Col>
                <Col xs={12} md={8} lg={4}>
                  <Form.Item name="early_stopping" label="早停轮数">
                    <InputNumber min={1} style={{ width: '100%' }} placeholder="服务端默认" />
                  </Form.Item>
                </Col>
                <Col xs={12} md={8} lg={4}>
                  <Form.Item name="num_boost_round" label="最大提升轮数">
                    <InputNumber min={1} style={{ width: '100%' }} placeholder="服务端默认" />
                  </Form.Item>
                </Col>
              </Row>
            </Card>

            <Card size="small" title="LightGBM 超参数" style={{ marginTop: 16 }}>
              <Row gutter={16}>
                <Col xs={12} md={8} lg={4}>
                  <Form.Item name={['lgb_params', 'num_leaves']} label="叶子数">
                    <InputNumber min={2} max={4096} style={{ width: '100%' }} placeholder="服务端默认" />
                  </Form.Item>
                </Col>
                <Col xs={12} md={8} lg={4}>
                  <Form.Item name={['lgb_params', 'min_data_in_leaf']} label="叶子最小样本数">
                    <InputNumber min={1} style={{ width: '100%' }} placeholder="服务端默认" />
                  </Form.Item>
                </Col>
                <Col xs={12} md={8} lg={4}>
                  <Form.Item name={['lgb_params', 'learning_rate']} label="学习率">
                    <InputNumber min={0.001} max={1} step={0.01} style={{ width: '100%' }} placeholder="服务端默认" />
                  </Form.Item>
                </Col>
                <Col xs={12} md={8} lg={4}>
                  <Form.Item name={['lgb_params', 'feature_fraction']} label="特征采样比例">
                    <InputNumber min={0.01} max={1} step={0.05} style={{ width: '100%' }} placeholder="服务端默认" />
                  </Form.Item>
                </Col>
                <Col xs={12} md={8} lg={4}>
                  <Form.Item name={['lgb_params', 'bagging_fraction']} label="行采样比例">
                    <InputNumber min={0.01} max={1} step={0.05} style={{ width: '100%' }} placeholder="服务端默认" />
                  </Form.Item>
                </Col>
                <Col xs={12} md={8} lg={4}>
                  <Form.Item name={['lgb_params', 'bagging_freq']} label="采样频率">
                    <InputNumber min={0} style={{ width: '100%' }} placeholder="服务端默认" />
                  </Form.Item>
                </Col>
              </Row>
            </Card>

            <Form.Item style={{ marginTop: 16, marginBottom: 0 }}>
              <Button type="primary" htmlType="submit" loading={submitting}>
                发起训练
              </Button>
            </Form.Item>
          </Form>
        </Card>
      </div>

      <Card
        title="训练历史"
        extra={
          <Space>
            <Button size="small" loading={loading} onClick={() => void load()}>
              刷新
            </Button>
          </Space>
        }
      >
        {/* An empty table is a header row with nothing under it. When there is
            nothing to show at all, show the explanation and a way in instead. */}
        {!loading && total === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Flex vertical gap={4}>
                <Typography.Text strong>尚无训练</Typography.Text>
                <Typography.Text type="secondary">
                  提交一次训练后，运行会按提交时间倒序列在这里。
                </Typography.Text>
              </Flex>
            }
          >
            <Space>
              <Button type="primary" loading={submitting} onClick={submitDefaults}>
                用默认参数发起训练
              </Button>
              <Button onClick={focusForm}>填写参数</Button>
            </Space>
          </Empty>
        ) : (
          <>
            {/* A row cannot show a percentage: a run's rows are written when the
                training ends, so an in-flight one has no numbers yet. Say so
                while one is in flight rather than leaving a blank where a
                progress bar is expected. */}
            {live && (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message="训练进行中，本页会自动刷新"
                description="窗口级的进度与实时日志在任务详情页；这里的完成窗口数与指标在训练结束时一次写入。"
              />
            )}
            <Table<ModelRunSummary>
              rowKey="run_id"
              columns={columns}
              dataSource={rows}
              loading={loading}
              size="small"
              // Virtual scrolling keeps a 200-row page responsive, and needs a
              // fixed body height plus an explicit width on every column.
              virtual
              scroll={{ x: 1280, y: 480 }}
              pagination={{
                current: page,
                pageSize,
                total,
                showSizeChanger: true,
                pageSizeOptions: PAGE_SIZES.map(String),
                showTotal: count => `共 ${count} 次训练`,
                onChange: (nextPage, nextSize) => {
                  // Changing page size restarts from the first page.
                  setPage(nextSize === pageSize ? nextPage : 1);
                  setPageSize(nextSize);
                },
              }}
            />
          </>
        )}
      </Card>
    </Flex>
  );
}
