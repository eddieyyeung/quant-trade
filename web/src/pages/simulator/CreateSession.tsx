import { useEffect, useState } from 'react';
import { Alert, App as AntdApp, Button, Card, DatePicker, Form, Input, InputNumber, Select } from 'antd';
import type { Dayjs } from 'dayjs';
import { useNavigate } from 'react-router-dom';

import { backtestsApi } from '../../api/backtests';
import { simulatorApi } from '../../api/simulator';

/** The value meaning "no reference strategy", distinct from "not chosen yet". */
const NO_STRATEGY = '';

interface FormValues {
  name: string;
  start_date: Dayjs;
  end_date?: Dayjs | null;
  capital?: number;
  ref?: string;
}

export default function CreateSession() {
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();
  const [form] = Form.useForm<FormValues>();

  const [strategies, setStrategies] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  /**
   * Held in state as well as toasted. A toast is gone in seconds, and a failed
   * create is exactly the kind of error a reader comes back to re-read while
   * fixing the form.
   */
  const [failure, setFailure] = useState<string | null>(null);

  // The strategy list comes from the registry rather than a hardcoded pair, so
  // a newly registered strategy is selectable here without a frontend change.
  useEffect(() => {
    backtestsApi
      .strategies()
      .then(body => setStrategies(body.items))
      .catch((e: unknown) => message.error(e instanceof Error ? e.message : String(e)));
  }, [message]);

  const submit = async (values: FormValues) => {
    setCreating(true);
    setFailure(null);
    try {
      // Unset optional fields are omitted rather than sent empty, so the
      // backend decides their defaults instead of receiving a blank string.
      const created = await simulatorApi.createSession({
        name: values.name,
        start_date: values.start_date.format('YYYY-MM-DD'),
        end_date: values.end_date ? values.end_date.format('YYYY-MM-DD') : undefined,
        capital: values.capital ?? undefined,
        ref: values.ref ? values.ref : undefined,
      });
      message.success('会话已创建');
      navigate(`/simulator/${encodeURIComponent(created.session_id)}`);
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      setFailure(text);
      message.error(text);
    } finally {
      setCreating(false);
    }
  };

  return (
    <Card title="创建会话">
      <Form<FormValues>
        form={form}
        layout="inline"
        initialValues={{ name: '复盘测试', capital: 100000, ref: 'factor_ranking' }}
        onFinish={values => void submit(values)}
      >
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写会话名称' }]}>
          <Input style={{ width: 180 }} disabled={creating} />
        </Form.Item>
        <Form.Item name="start_date" label="起始日期" rules={[{ required: true, message: '请选择起始日期' }]}>
          <DatePicker disabled={creating} />
        </Form.Item>
        <Form.Item
          name="end_date"
          label="结束日期"
          rules={[
            {
              validator: (_rule, value: Dayjs | null | undefined) => {
                const start = form.getFieldValue('start_date') as Dayjs | undefined;
                if (start && value && value.isBefore(start)) {
                  return Promise.reject(new Error('结束日期不能早于起始日期'));
                }
                return Promise.resolve();
              },
            },
          ]}
        >
          <DatePicker placeholder="不填则用默认" disabled={creating} />
        </Form.Item>
        <Form.Item name="capital" label="初始资金">
          <InputNumber min={1000} step={10000} style={{ width: 140 }} disabled={creating} />
        </Form.Item>
        <Form.Item name="ref" label="参考策略">
          <Select
            style={{ width: 180 }}
            disabled={creating}
            options={[
              { value: NO_STRATEGY, label: '无' },
              ...strategies.map(name => ({ value: name, label: name })),
            ]}
          />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={creating}>
            创建
          </Button>
        </Form.Item>
      </Form>

      {failure !== null && (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 8 }}
          message="会话创建失败"
          description={failure}
        />
      )}
    </Card>
  );
}
