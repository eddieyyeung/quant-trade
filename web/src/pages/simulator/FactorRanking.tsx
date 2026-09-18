import { Alert, Table } from 'antd';
import type { TableProps } from 'antd';

import type { FactorRankItem } from '../../api/simulator';

/**
 * The composite factor ranking for the week.
 *
 * How many rows arrive is the backend's decision — it caps the list — so this
 * renders whatever it is given rather than assuming a fixed length.
 */
const columns: TableProps<FactorRankItem>['columns'] = [
  { title: '排名', dataIndex: 'rank', key: 'rank', width: 80 },
  { title: '代码', dataIndex: 'ts_code', key: 'ts_code', width: 140 },
  {
    title: '综合得分',
    dataIndex: 'composite_score',
    key: 'composite_score',
    width: 120,
    render: (value: number) => value.toFixed(2),
  },
];

export default function FactorRanking({ items }: { items: FactorRankItem[] }) {
  if (items.length === 0) {
    return <Alert type="info" showIcon message="暂无因子数据" description="该周的因子排名尚未计算，或股票池为空。" />;
  }
  return (
    <Table<FactorRankItem>
      rowKey="rank"
      columns={columns}
      dataSource={items}
      size="small"
      pagination={false}
      scroll={{ x: 340 }}
    />
  );
}
