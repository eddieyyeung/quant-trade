import { useState } from 'react';
import { App as AntdApp, Button, Flex, Form, Input, Tooltip, Typography } from 'antd';

import { simulatorApi, type OrderRequest } from '../../api/simulator';
import { parseOrders } from './orders';
import RecommendationModal from './RecommendationModal';
import { toFormInputs } from './recommendation';

interface FormValues {
  buy?: string;
  sell?: string;
  notes?: string;
}

export default function DecisionForm({
  sessionId,
  recommendedOrders,
  recommendationSource,
  onExecuted,
}: {
  sessionId: string;
  /**
   * `null` means the session has no reference strategy. `[]` means it has one
   * that was quiet this week — the entry point is still shown, disabled, so the
   * path is visible rather than silently absent.
   */
  recommendedOrders: OrderRequest[] | null;
  recommendationSource: string | null;
  onExecuted: () => void;
}) {
  const { message, modal, notification } = AntdApp.useApp();
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  /** Labels an adoption in the notes, so the record says where the week came from. */
  const followNotes = recommendationSource ? `按 ${recommendationSource} 推荐方案` : '按推荐方案';

  const execute = async (orders: OrderRequest[], notes: string) => {
    setSubmitting(true);
    try {
      const result = await simulatorApi.step(sessionId, orders, notes);

      // A filled order is a multi-line fact — a count plus one line per fill.
      // `message` is a single line and collapses every line onto one, so the
      // receipt goes through `notification`, which keeps the shape.
      const fills = result.executed_orders.map(
        order =>
          `${order.direction} ${order.ts_code} ${order.shares}股 @ ¥${order.price}` +
          // Recommended orders carry the strategy's rationale; showing it is
          // the point of carrying it, but a hand-typed order has none.
          (order.reason ? ` · ${order.reason}` : ''),
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

  /** Submit the recommendation as-is. Weights are never recomputed here. */
  const executeRecommendation = async () => {
    setPreviewing(false);
    await execute(recommendedOrders ?? [], followNotes);
  };

  /**
   * Hand the orders to the form as editable text rather than submitting them.
   * Partial adoption is the interesting case, so it has to be as cheap as
   * adoption in full.
   */
  const prefillFromRecommendation = () => {
    const { buy, sell } = toFormInputs(recommendedOrders ?? []);
    form.setFieldsValue({ buy, sell, notes: `${followNotes}（已手工调整）` });
    setPreviewing(false);
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
        {/* No reference strategy means no recommendation, and the "无参考策略"
            notice above already says why — an entry point here would only
            restate it. */}
        {recommendedOrders !== null && (
          // A disabled button fires no pointer events, so the hover target has
          // to be this wrapper — tooltips on the button itself never open, and
          // the quiet-week case exists precisely to explain itself.
          <Tooltip title={recommendedOrders.length === 0 ? '参考策略本周没有给出信号' : undefined}>
            <span style={{ display: 'inline-block' }}>
              <Button onClick={() => setPreviewing(true)} disabled={submitting || recommendedOrders.length === 0}>
                按推荐方案
              </Button>
            </span>
          </Tooltip>
        )}
        <Button onClick={handleSkip} disabled={submitting}>
          跳过本周
        </Button>
      </Flex>

      <RecommendationModal
        open={previewing}
        orders={recommendedOrders ?? []}
        source={recommendationSource}
        submitting={submitting}
        onExecute={() => void executeRecommendation()}
        onPrefill={prefillFromRecommendation}
        onClose={() => setPreviewing(false)}
      />
    </Form>
  );
}
