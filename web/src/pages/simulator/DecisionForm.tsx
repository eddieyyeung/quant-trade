import { useState } from 'react';
import { App as AntdApp, Button, Flex, Form, Input, Typography } from 'antd';

import { simulatorApi, type OrderRequest } from '../../api/simulator';
import { parseOrders } from './orders';

interface FormValues {
  buy?: string;
  sell?: string;
  notes?: string;
}

export default function DecisionForm({
  sessionId,
  onExecuted,
}: {
  sessionId: string;
  onExecuted: () => void;
}) {
  const { message, modal, notification } = AntdApp.useApp();
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);

  const execute = async (orders: OrderRequest[], notes: string) => {
    setSubmitting(true);
    try {
      const result = await simulatorApi.step(sessionId, orders, notes);

      // A filled order is a multi-line fact — a count plus one line per fill.
      // `message` is a single line and collapses every line onto one, so the
      // receipt goes through `notification`, which keeps the shape.
      const fills = result.executed_orders.map(
        order => `${order.direction} ${order.ts_code} ${order.shares}股 @ ¥${order.price}`,
      );
      notification.info({
        message: `成交 ${result.executed_orders.length} 笔`,
        description: (
          <Flex vertical gap={2}>
            {fills.length > 0 ? fills.map(line => <Typography.Text key={line}>{line}</Typography.Text>) : null}
            {result.warnings.length > 0 ? (
              <Typography.Text type="warning">⚠ {result.warnings.join('；')}</Typography.Text>
            ) : null}
            {fills.length === 0 && result.warnings.length === 0 ? (
              <Typography.Text type="secondary">本周没有成交</Typography.Text>
            ) : null}
          </Flex>
        ),
        duration: 8,
      });

      form.resetFields();
      onExecuted();
    } catch (e) {
      // The inputs are left as typed: a rejected order usually needs one
      // character changed, not retyping.
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const handleStep = async (values: FormValues) => {
    const orders = parseOrders(values.buy ?? '', values.sell ?? '');
    const notes = (values.notes ?? '').trim();

    if (orders.length === 0 && !notes) {
      modal.confirm({
        title: '提交空操作',
        content: '既没有订单也没有备注。确认提交一次空操作，仅推进到下一周？',
        okText: '提交',
        cancelText: '取消',
        onOk: () => execute(orders, notes),
      });
      return;
    }
    await execute(orders, notes);
  };

  const handleSkip = () => {
    modal.confirm({
      title: '跳过本周',
      content: '确认跳过本周调仓？本周不会产生任何成交，游标直接推进一周。',
      okText: '跳过',
      cancelText: '取消',
      onOk: async () => {
        setSubmitting(true);
        try {
          await simulatorApi.skip(sessionId);
          message.success('已跳过本周');
          onExecuted();
        } catch (e) {
          message.error(e instanceof Error ? e.message : String(e));
        } finally {
          setSubmitting(false);
        }
      },
    });
  };

  return (
    <Form<FormValues> form={form} layout="vertical" onFinish={values => void handleStep(values)}>
      <Flex gap={16} wrap>
        <Form.Item
          name="buy"
          label="买入（代码:百分比，逗号分隔）"
          style={{ flex: 2, minWidth: 280, marginBottom: 8 }}
        >
          <Input placeholder="600519.SH:10, 002594.SZ:8" disabled={submitting} />
        </Form.Item>
        <Form.Item name="sell" label="卖出（代码，逗号分隔）" style={{ flex: 1, minWidth: 200, marginBottom: 8 }}>
          <Input placeholder="300750.SZ" disabled={submitting} />
        </Form.Item>
        <Form.Item name="notes" label="备注" style={{ flex: 2, minWidth: 240, marginBottom: 8 }}>
          <Input placeholder="看好消费" disabled={submitting} />
        </Form.Item>
      </Flex>

      <Flex gap={8}>
        <Button type="primary" htmlType="submit" loading={submitting}>
          提交决策
        </Button>
        <Button onClick={handleSkip} disabled={submitting}>
          跳过本周
        </Button>
      </Flex>
    </Form>
  );
}
