import { useCallback, useEffect, useState } from 'react';
import { App as AntdApp, Button, Card, Col, Flex, Progress, Row, Statistic, Table, Typography } from 'antd';
import type { TableProps } from 'antd';

import { dataApi, type Coverage } from '../../api/data';
import { formatCount, formatPercent } from '../../utils/format';
import DataNav from './DataNav';

interface MissingRow {
  key: string;
  ts_code: string;
}

export default function Universe() {
  const { message } = AntdApp.useApp();
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setCoverage(await dataApi.coverage(pageSize, (page - 1) * pageSize));
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [message, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  const missing: MissingRow[] = (coverage?.missing ?? []).map(code => ({ key: code, ts_code: code }));

  const columns: TableProps<MissingRow>['columns'] = [
    {
      title: '股票代码',
      dataIndex: 'ts_code',
      key: 'ts_code',
      width: 200,
      render: (code: string) => <Typography.Text code>{code}</Typography.Text>,
    },
  ];

  return (
    <Flex vertical gap={16}>
      <DataNav />

      <Row gutter={16}>
        <Col span={8}>
          <Card>
            <Statistic title="股票池总数" value={coverage?.universe_size ?? 0} suffix="只" />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="已同步日线" value={coverage?.covered ?? 0} suffix="只" />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="覆盖率" value={formatPercent(coverage?.ratio)} />
            <Progress
              percent={Math.round((coverage?.ratio ?? 0) * 100)}
              size="small"
              status={coverage && coverage.covered < coverage.universe_size ? 'active' : 'success'}
            />
          </Card>
        </Col>
      </Row>

      <Card
        title={`缺失日线的股票（${formatCount(coverage?.missing_total ?? 0)}）`}
        extra={
          <Flex gap={12} align="center">
            <Typography.Text type="secondary">截至 {coverage?.as_of ?? '—'}</Typography.Text>
            <Button size="small" loading={loading} onClick={() => void load()}>
              刷新
            </Button>
          </Flex>
        }
      >
        <Table<MissingRow>
          rowKey="key"
          columns={columns}
          dataSource={missing}
          loading={loading}
          size="small"
          virtual
          scroll={{ x: 400, y: 400 }}
          pagination={{
            current: page,
            pageSize,
            total: coverage?.missing_total ?? 0,
            showSizeChanger: true,
            pageSizeOptions: [20, 50, 100, 200],
            showTotal: count => `共 ${count} 只`,
            onChange: (nextPage, nextSize) => {
              setPageSize(nextSize);
              setPage(nextSize === pageSize ? nextPage : 1);
            },
          }}
          locale={{ emptyText: '股票池已全部覆盖' }}
        />
      </Card>
    </Flex>
  );
}
