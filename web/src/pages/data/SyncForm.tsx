import { useState } from 'react';
import { App as AntdApp, Alert, Button, Card, DatePicker, Flex, Form, Switch, Typography } from 'antd';
import type { Dayjs } from 'dayjs';
import { useNavigate } from 'react-router-dom';

import { runsApi } from '../../api/runs';
import DataNav from './DataNav';

const { RangePicker } = DatePicker;

interface SyncFormValues {
  /** Absent means "full history on a fresh database, a short window otherwise". */
  window?: [Dayjs | null, Dayjs | null] | null;
  include_financials: boolean;
}

export default function SyncForm() {
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();
  const [form] = Form.useForm<SyncFormValues>();
  const [submitting, setSubmitting] = useState(false);

  const submit = async (values: SyncFormValues) => {
    const [start, end] = values.window ?? [null, null];
    setSubmitting(true);
    try {
      const run = await runsApi.submit('data_sync', {
        include_financials: values.include_financials,
        start_date: start ? start.format('YYYY-MM-DD') : null,
        end_date: end ? end.format('YYYY-MM-DD') : null,
      });
      message.success('同步任务已提交，正在跳转任务中心');
      navigate(`/jobs/${run.run_id}`);
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Flex vertical gap={16}>
      <DataNav />

      <Card title="发起数据同步">
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="全市场同步是小时级任务"
          description="任务在后台串行执行，提交后可在任务中心查看进度与实时日志。同一时间只会运行一个任务。"
        />

        <Form<SyncFormValues>
          form={form}
          layout="vertical"
          initialValues={{ window: null, include_financials: false }}
          onFinish={values => void submit(values)}
          style={{ maxWidth: 560 }}
        >
          <Form.Item
            name="window"
            label="同步区间"
            // RangePicker cannot itself produce an inverted range, so this
            // guards the other way in: params restored from a previous run.
            rules={[
              {
                validator: (_, value: SyncFormValues['window']) => {
                  const [start, end] = value ?? [null, null];
                  if (start && end && start.isAfter(end)) {
                    return Promise.reject(new Error('起始日期不能晚于结束日期'));
                  }
                  return Promise.resolve();
                },
              },
            ]}
            extra="不填表示增量同步：空库拉全部历史，已有数据则补最近几个交易日。"
          >
            <RangePicker style={{ width: '100%' }} allowEmpty={[true, true]} />
          </Form.Item>

          <Form.Item name="include_financials" label="同时拉取财务数据" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0 }}>
            <Flex gap={8}>
              <Button type="primary" htmlType="submit" loading={submitting}>
                提交同步任务
              </Button>
              <Button onClick={() => form.resetFields()} disabled={submitting}>
                重置
              </Button>
            </Flex>
          </Form.Item>
        </Form>

        <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
          任务类型为 <Typography.Text code>data_sync</Typography.Text>，由后端注册表分发，前端不为此新增路由。
        </Typography.Paragraph>
      </Card>
    </Flex>
  );
}
