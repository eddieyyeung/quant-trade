import { useCallback, useEffect, useState } from 'react';
import { App as AntdApp, Button, Card, Col, Flex, Row, Statistic, Table, Tag, Typography } from 'antd';
import type { TableProps } from 'antd';
import { Link } from 'react-router-dom';

import { dataApi, type DataStatus, type TableStat } from '../../api/data';
import { formatCount } from '../../utils/format';
import DataNav from './DataNav';

export default function Overview() {
  const { message } = AntdApp.useApp();
  const [status, setStatus] = useState<DataStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await dataApi.status());
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  const rows = status?.tables ?? [];
  const totalRows = rows.reduce((sum, table) => sum + table.rows, 0);
  const emptyTables = rows.filter(table => table.rows === 0).length;

  const columns: TableProps<TableStat>['columns'] = [
    { title: '表名', dataIndex: 'table', key: 'table', width: 200 },
    {
      title: '行数',
      dataIndex: 'rows',
      key: 'rows',
      width: 160,
      render: (count: number) =>
        count === 0 ? <Tag color="warning">无数据</Tag> : <span>{formatCount(count)}</span>,
    },
    {
      title: '最早日期',
      dataIndex: 'earliest',
      key: 'earliest',
      width: 180,
      render: (value: string | null) => value ?? '—',
    },
    {
      title: '最晚日期',
      dataIndex: 'latest',
      key: 'latest',
      width: 180,
      render: (value: string | null) => value ?? '—',
    },
  ];

  return (
    <Flex vertical gap={16}>
      <DataNav />

      <Row gutter={16}>
        <Col span={6}>
          <Card>
            <Statistic
              title="最新交易日"
              value={status?.latest_trade_date ?? '—'}
              loading={loading && status === null}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="股票池规模" value={status?.universe_size ?? 0} suffix="只" />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="数据库总行数" value={totalRows} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="空表数量"
              value={emptyTables}
              suffix={`/ ${rows.length}`}
              valueStyle={emptyTables > 0 ? { color: '#d46b08' } : undefined}
            />
          </Card>
        </Col>
      </Row>

      <Card
        title="数据表统计"
        extra={
          <Flex gap={12} align="center">
            <Typography.Text type="secondary" ellipsis={{ tooltip: status?.db_path }}>
              {status?.db_path}
            </Typography.Text>
            <Button size="small" loading={loading} onClick={() => void load()}>
              刷新
            </Button>
          </Flex>
        }
      >
        <Table<TableStat>
          rowKey="table"
          columns={columns}
          dataSource={rows}
          loading={loading}
          size="small"
          virtual
          scroll={{ x: 720, y: 480 }}
          pagination={false}
          locale={{
            emptyText: (
              <Flex vertical gap={8} align="center">
                <span>暂无数据</span>
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
    </Flex>
  );
}
