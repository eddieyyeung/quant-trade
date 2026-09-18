import { useEffect, useState } from 'react';
import { Alert, App as AntdApp, Button, Card, DatePicker, Flex, Form, InputNumber, Select, Table, Tag } from 'antd';
import type { TableProps } from 'antd';
import type { Dayjs } from 'dayjs';
import { useNavigate } from 'react-router-dom';

import { runsApi } from '../../api/runs';
import { strategiesApi } from '../../api/strategies';
import StrategyNav from './StrategyNav';

interface FormValues {
  strategy?: string;
  as_of?: Dayjs | null;
  universe?: string[];
  top_n?: number;
}

export default function List() {
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();
  const [form] = Form.useForm<FormValues>();

  const [strategies, setStrategies] = useState<string[]>([]);
  const [defaultStrategy, setDefaultStrategy] = useState<string>('');
  const [submitting, setSubmitting] = useState(false);
  const [registryFailure, setRegistryFailure] = useState<string | null>(null);

  const loadRegistry = () => {
    strategiesApi
      .list()
      .then(body => {
        setStrategies(body.items);
        setDefaultStrategy(body.default);
        setRegistryFailure(null);
        if (!form.getFieldValue('strategy')) form.setFieldValue('strategy', body.default);
      })
      .catch((e: unknown) => {
        // Kept on the page rather than only toasted: without the registry there
        // is nothing to submit, and a vanished toast would leave an empty
        // selector with no explanation.
        const text = e instanceof Error ? e.message : String(e);
        setRegistryFailure(text);
        message.error(text);
      });
  };

  useEffect(loadRegistry, [form, message]);

  const submit = async (values: FormValues) => {
    // An empty list is not the same as "left blank". The service reads a missing
    // universe as "use the default pool", so sending `[]` would ask for a pool
    // of nothing — a run that produces no signals for reasons the user did not
    // intend.
    if (values.universe !== undefined && values.universe.length === 0) {
      message.warning('股票池已填写但为空，请填写代码或留空以使用默认股票池');
      return;
    }

    setSubmitting(true);
    try {
      // Unset fields are null, not empty strings: the service's own defaults
      // decide the signal date, the pool and the holding count.
      const run = await runsApi.submit('strategy_signals', {
        strategy: values.strategy ?? null,
        as_of: values.as_of ? values.as_of.format('YYYY-MM-DD') : null,
        universe: values.universe && values.universe.length > 0 ? values.universe : null,
        top_n: values.top_n ?? null,
      });
      message.success('信号生成任务已提交，正在跳转任务中心');
      navigate(`/jobs/${run.run_id}`);
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const columns: TableProps<string>['columns'] = [
    {
      title: '策略名',
      key: 'name',
      render: (_, name) => (
        <Flex gap={8} align="center">
          <span>{name}</span>
          {name === defaultStrategy && <Tag color="blue">配置默认</Tag>}
        </Flex>
      ),
    },
  ];

  return (
    <Flex vertical gap={16}>
      <StrategyNav />

      {registryFailure !== null && (
        <Alert
          type="error"
          showIcon
          message="策略清单加载失败"
          description={registryFailure}
          action={
            <Button size="small" onClick={loadRegistry}>
              重试
            </Button>
          }
        />
      )}

      <Card title="发起信号生成">
        <Form<FormValues> form={form} layout="inline" onFinish={values => void submit(values)}>
          <Form.Item name="strategy" label="策略">
            <Select
              style={{ width: 180 }}
              placeholder="选择策略"
              options={strategies.map(name => ({ value: name, label: name }))}
            />
          </Form.Item>
          <Form.Item name="as_of" label="信号日期">
            <DatePicker placeholder="留空取最近交易日" />
          </Form.Item>
          <Form.Item name="universe" label="股票池">
            {/* Free entry: a universe is a list of ts_codes, and no endpoint
                enumerates a sensible subset to offer as options. */}
            <Select
              mode="tags"
              style={{ minWidth: 300 }}
              placeholder="留空使用默认股票池"
              tokenSeparators={[',', ' ']}
              open={false}
            />
          </Form.Item>
          <Form.Item name="top_n" label="持仓数">
            <InputNumber min={1} style={{ width: 110 }} placeholder="配置默认" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={submitting}>
              发起信号生成
            </Button>
          </Form.Item>
        </Form>
      </Card>

      <Card size="small" title={`已注册策略（${strategies.length}）`}>
        {strategies.length === 0 ? (
          <Alert
            type="info"
            showIcon
            message="暂无已注册策略"
            description="注册表为空，无法发起信号生成。"
          />
        ) : (
          <Table<string> rowKey={name => name} columns={columns} dataSource={strategies} size="small" pagination={false} />
        )}
      </Card>
    </Flex>
  );
}
