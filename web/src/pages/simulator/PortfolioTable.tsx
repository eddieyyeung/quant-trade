import { Alert, Table, Typography } from 'antd';
import type { TableProps } from 'antd';

import type { PortfolioItem } from '../../api/simulator';

/**
 * Percent from a fraction.
 *
 * No absent-value branch: the caller only reaches this with a number. The one
 * column that can genuinely be missing (`current_price`) has its own guard
 * below.
 */
function pct(value: number, digits: number): string {
  return `${(value * 100).toFixed(digits)}%`;
}

const columns: TableProps<PortfolioItem>['columns'] = [
  { title: '代码', dataIndex: 'ts_code', key: 'ts_code', width: 140 },
  {
    title: '股数',
    dataIndex: 'shares',
    key: 'shares',
    width: 100,
    render: (shares: number) => shares.toLocaleString('zh-CN'),
  },
  {
    title: '均价',
    dataIndex: 'avg_cost',
    key: 'avg_cost',
    width: 110,
    render: (value: number) => `¥${value.toFixed(2)}`,
  },
  {
    title: '现价',
    dataIndex: 'current_price',
    key: 'current_price',
    width: 110,
    // A suspended stock has no price for the day. Showing 0 would read as
    // "this position became worthless", which is a different statement.
    render: (value: number | null) => (value ? `¥${value.toFixed(2)}` : '—'),
  },
  {
    title: '盈亏',
    dataIndex: 'pnl_pct',
    key: 'pnl_pct',
    width: 110,
    render: (value: number) => (
      <Typography.Text type={value >= 0 ? 'success' : 'danger'}>{pct(value, 2)}</Typography.Text>
    ),
  },
  {
    title: '权重',
    dataIndex: 'weight_pct',
    key: 'weight_pct',
    width: 100,
    render: (value: number) => pct(value, 1),
  },
];

export default function PortfolioTable({ items }: { items: PortfolioItem[] }) {
  if (items.length === 0) {
    return <Alert type="info" showIcon message="空仓" description="该会话当前没有持有任何股票。" />;
  }
  return (
    <Table<PortfolioItem>
      rowKey="ts_code"
      columns={columns}
      dataSource={items}
      size="small"
      pagination={false}
      scroll={{ x: 670 }}
    />
  );
}
