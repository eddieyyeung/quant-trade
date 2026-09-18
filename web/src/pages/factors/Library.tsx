import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  App as AntdApp,
  Alert,
  Button,
  Card,
  DatePicker,
  Flex,
  Form,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { TableProps } from 'antd';
import type { Dayjs } from 'dayjs';
import { Link, useNavigate } from 'react-router-dom';

import { factorsApi, type FactorRow } from '../../api/factors';
import { runsApi } from '../../api/runs';
import { formatCount, formatDate } from '../../utils/format';
import FactorNav from './FactorNav';
import { readSelectedFactors, writeSelectedFactors } from './selection';

const { RangePicker } = DatePicker;

const CATEGORIES = [
  { value: 'alpha158', label: 'alpha158' },
  { value: 'momentum', label: '动量' },
  { value: 'value', label: '价值' },
  { value: 'quality', label: '质量' },
  { value: 'technical', label: '技术' },
];

const PAGE_SIZES = [20, 50, 100, 200];

interface ComputeValues {
  window?: [Dayjs | null, Dayjs | null] | null;
}

export default function Library() {
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();
  const [form] = Form.useForm<ComputeValues>();

  const [rows, setRows] = useState<FactorRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [category, setCategory] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [computeOpen, setComputeOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>(() => readSelectedFactors());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const body = await factorsApi.list({ category, limit: pageSize, offset: (page - 1) * pageSize });
      setRows(body.items);
      setTotal(body.total);
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [category, message, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  const onSelectionChange = (keys: string[]) => {
    setSelected(keys);
    writeSelectedFactors(keys);
  };

  const submitCompute = async (values: ComputeValues) => {
    const [start, end] = values.window ?? [null, null];
    setSubmitting(true);
    try {
      const run = await runsApi.submit('factor_compute', {
        start_date: start ? start.format('YYYY-MM-DD') : null,
        end_date: end ? end.format('YYYY-MM-DD') : null,
      });
      message.success('因子计算任务已提交，正在跳转任务中心');
      navigate(`/jobs/${run.run_id}`);
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
      setComputeOpen(false);
    }
  };

  const columns: TableProps<FactorRow>['columns'] = useMemo(
    () => [
      { title: '因子名', dataIndex: 'name', key: 'name', width: 200 },
      {
        title: '分类',
        dataIndex: 'category',
        key: 'category',
        width: 120,
        render: (value: string) => <Tag>{value}</Tag>,
      },
      {
        title: '持久化',
        dataIndex: 'persisted',
        key: 'persisted',
        width: 140,
        render: (persisted: boolean) =>
          persisted ? <Tag color="success">已落库</Tag> : <Tag color="warning">未落库</Tag>,
      },
      {
        title: '记录数',
        dataIndex: 'rows',
        key: 'rows',
        width: 140,
        render: (count: number, row: FactorRow) =>
          row.persisted ? <span>{formatCount(count)}</span> : <Typography.Text type="secondary">—</Typography.Text>,
      },
      {
        title: '最早日期',
        dataIndex: 'earliest',
        key: 'earliest',
        width: 140,
        render: (value: string | null) => formatDate(value),
      },
      {
        title: '最晚日期',
        dataIndex: 'latest',
        key: 'latest',
        width: 140,
        render: (value: string | null) => formatDate(value),
      },
    ],
    [],
  );

  return (
    <Flex vertical gap={16}>
      <FactorNav />

      <Card
        title="因子库"
        extra={
          <Space>
            <Select
              allowClear
              placeholder="全部分类"
              style={{ width: 160 }}
              value={category}
              options={CATEGORIES}
              onChange={value => {
                setPage(1);
                setCategory(value);
              }}
            />
            <Button size="small" loading={loading} onClick={() => void load()}>
              刷新
            </Button>
            <Button type="primary" size="small" onClick={() => setComputeOpen(true)}>
              计算 Alpha158 因子
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph type="secondary">
          勾选的因子会作为 IC 分析、分层回测与相关性三页选择器的默认值，仅保存在本机浏览器中。
        </Typography.Paragraph>

        <Table<FactorRow>
          rowKey="name"
          columns={columns}
          dataSource={rows}
          loading={loading}
          size="small"
          virtual
          scroll={{ x: 880, y: 480 }}
          rowSelection={{ selectedRowKeys: selected, onChange: keys => onSelectionChange(keys as string[]) }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: PAGE_SIZES.map(String),
            showTotal: count => `共 ${count} 个因子`,
            onChange: (nextPage, nextSize) => {
              // Changing page size restarts from the first page.
              setPage(nextSize === pageSize ? nextPage : 1);
              setPageSize(nextSize);
            },
          }}
          locale={{
            emptyText: (
              <Flex vertical gap={8} align="center">
                <span>暂无因子</span>
                <Link to="/data/sync">
                  <Button type="primary" size="small">
                    去同步数据
                  </Button>
                </Link>
              </Flex>
            ),
          }}
        />
      </Card>

      <Modal
        title="计算 Alpha158 因子"
        open={computeOpen}
        onCancel={() => setComputeOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={submitting}
        okText="提交任务"
        cancelText="取消"
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="全量因子计算是长任务"
          description="158 个因子 × 全市场 × 多年数据，提交后请到任务中心查看进度与日志。"
        />
        <Form<ComputeValues>
          form={form}
          layout="vertical"
          initialValues={{ window: null }}
          onFinish={values => void submitCompute(values)}
        >
          <Form.Item
            name="window"
            label="计算区间"
            extra="不填表示从默认起始日到现在。"
            rules={[
              {
                validator: (_, value: ComputeValues['window']) => {
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
        </Form>
      </Modal>
    </Flex>
  );
}
