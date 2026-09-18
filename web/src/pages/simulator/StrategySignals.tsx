import { Alert, Table, Tag } from 'antd';
import type { TableProps } from 'antd';

import type { StrategySignalItem } from '../../api/simulator';

const columns: TableProps<StrategySignalItem>['columns'] = [
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
    render: (value: number) => `${(value * 100).toFixed(1)}%`,
  },
  { title: '理由', dataIndex: 'reason', key: 'reason' },
];

/**
 * What the reference strategy would do this week.
 *
 * `null` and `[]` are different facts and are kept apart here. `null` means no
 * reference strategy was configured, so there is nothing to compare against;
 * `[]` means one was configured and it had nothing to say this week. Collapsing
 * them into one empty state would hide the difference between "not set up" and
 * "set up, and quiet".
 */
export default function StrategySignals({ signals }: { signals: StrategySignalItem[] | null }) {
  if (signals === null) {
    return <Alert type="info" showIcon message="无参考策略" description="该会话创建时未指定参考策略，因此没有可对照的信号。" />;
  }
  if (signals.length === 0) {
    return <Alert type="info" showIcon message="本周无策略信号" description="参考策略本周没有给出买入或卖出信号。" />;
  }
  return (
    <Table<StrategySignalItem>
      rowKey={(row, index) => `${row.ts_code}-${index}`}
      columns={columns}
      dataSource={signals}
      size="small"
      pagination={false}
      scroll={{ x: 560 }}
    />
  );
}
