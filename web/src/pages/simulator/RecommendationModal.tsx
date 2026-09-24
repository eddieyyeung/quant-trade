import { Alert, Button, Flex, Modal, Table, Tag, Typography } from 'antd';
import type { TableProps } from 'antd';

import type { OrderRequest } from '../../api/simulator';

const columns: TableProps<OrderRequest>['columns'] = [
  { title: '代码', dataIndex: 'ts_code', key: 'ts_code', width: 140 },
  {
    title: '方向',
    dataIndex: 'direction',
    key: 'direction',
    width: 90,
    render: (direction: string) => <Tag color={direction === 'BUY' ? 'success' : 'error'}>{direction}</Tag>,
  },
  {
    title: '目标权重',
    dataIndex: 'target_pct',
    key: 'target_pct',
    width: 110,
    render: (value: number) => `${(value * 100).toFixed(2)}%`,
  },
  { title: '理由', dataIndex: 'reason', key: 'reason', render: (reason: string) => reason || '—' },
];

/**
 * What following the reference strategy would submit, before anything is sent.
 *
 * Adoption never skips this step. The heaviest weeks are exactly the ones worth
 * seeing first — a full portfolio rotation is not something to discover from
 * the receipt afterwards.
 *
 * Two ways out, and the second one is not decoration: filling the form hands
 * the orders back as editable text, so the user can keep nine names out of
 * fifteen and record why. Executing submits them untouched.
 */
export default function RecommendationModal({
  open,
  orders,
  source,
  submitting,
  onExecute,
  onPrefill,
  onClose,
}: {
  open: boolean;
  orders: OrderRequest[];
  source: string | null;
  submitting: boolean;
  onExecute: () => void;
  onPrefill: () => void;
  onClose: () => void;
}) {
  const buys = orders.filter(order => order.direction === 'BUY').length;
  const sells = orders.length - buys;

  return (
    <Modal
      open={open}
      title={source ? `按 ${source} 推荐方案决策` : '按推荐方案决策'}
      onCancel={onClose}
      width={720}
      footer={
        <Flex justify="flex-end" gap={8}>
          <Button onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button onClick={onPrefill} disabled={submitting}>
            回填到表单
          </Button>
          <Button type="primary" onClick={onExecute} loading={submitting} disabled={orders.length === 0}>
            执行
          </Button>
        </Flex>
      }
    >
      <Flex vertical gap={12}>
        <Typography.Text type="secondary">
          共 {orders.length} 笔：买入 {buys} 笔，卖出 {sells} 笔。执行将按以下订单提交本周决策。
        </Typography.Text>

        <Table<OrderRequest>
          rowKey={(row, index) => `${row.ts_code}-${row.direction}-${index}`}
          columns={columns}
          dataSource={orders}
          size="small"
          pagination={false}
          scroll={{ x: 560, y: 320 }}
        />

        <Alert
          type="info"
          showIcon
          message="只想采纳一部分？"
          description="选择「回填到表单」，订单会写入买入与卖出输入框，删掉不想跟的标的后再提交。"
        />
      </Flex>
    </Modal>
  );
}
