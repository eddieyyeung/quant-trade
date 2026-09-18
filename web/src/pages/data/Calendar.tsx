import { useCallback, useEffect, useMemo, useState } from 'react';
import { App as AntdApp, Calendar as AntdCalendar, Card, Col, Flex, Row, Select, Statistic, Tag, Typography } from 'antd';
import dayjs from 'dayjs';

import { dataApi, type TradeCalendarView } from '../../api/data';
import DataNav from './DataNav';

export default function Calendar() {
  const { message } = AntdApp.useApp();
  const [year, setYear] = useState(dayjs().year());
  const [availableYears, setAvailableYears] = useState<number[]>([dayjs().year()]);
  const [view, setView] = useState<TradeCalendarView | null>(null);
  const [loading, setLoading] = useState(true);

  // The year picker is bounded by what the calendar table actually holds.
  useEffect(() => {
    const load = async () => {
      try {
        const status = await dataApi.status();
        const bounds = status.tables.find(table => table.table === 'trade_calendar');
        if (!bounds?.earliest || !bounds.latest) return;
        const first = dayjs(bounds.earliest).year();
        const last = dayjs(bounds.latest).year();
        setAvailableYears(Array.from({ length: last - first + 1 }, (_, i) => first + i));
      } catch {
        // A missing bound only narrows the picker; the calendar still loads.
      }
    };
    void load();
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setView(await dataApi.calendar(`${year}-01-01`, `${year}-12-31`));
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [message, year]);

  useEffect(() => {
    void load();
  }, [load]);

  const openDays = useMemo(() => {
    const map = new Map<string, boolean>();
    for (const day of view?.days ?? []) map.set(day.trade_date, day.is_open);
    return map;
  }, [view]);

  return (
    <Flex vertical gap={16}>
      <DataNav />

      <Row gutter={16}>
        <Col span={8}>
          <Card>
            <Statistic title="交易日" value={view?.open_days ?? 0} suffix="天" loading={loading} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="非交易日" value={view?.closed_days ?? 0} suffix="天" loading={loading} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Flex vertical gap={8}>
              <Typography.Text type="secondary">年份</Typography.Text>
              <Select
                value={year}
                style={{ width: 140 }}
                onChange={setYear}
                options={availableYears.map(value => ({ value, label: `${value} 年` }))}
              />
            </Flex>
          </Card>
        </Col>
      </Row>

      <Card
        title="交易日历"
        extra={
          <Flex gap={12} align="center">
            <Tag color="success">交易日</Tag>
            <Tag>非交易日</Tag>
            <Typography.Text type="secondary">无标记 = 日历中未收录</Typography.Text>
          </Flex>
        }
      >
        <AntdCalendar
          value={dayjs(`${year}-01-01`)}
          validRange={[dayjs(`${year}-01-01`), dayjs(`${year}-12-31`)]}
          cellRender={(value, info) => {
            if (info.type !== 'date') return info.originNode;
            const open = openDays.get(value.format('YYYY-MM-DD'));
            // Days outside the stored calendar stay unmarked rather than being
            // asserted to be closed — a missing row is not a closed market.
            if (open === undefined) return null;
            return (
              <div style={{ textAlign: 'center' }}>
                <Tag color={open ? 'success' : 'default'} style={{ marginInlineEnd: 0 }}>
                  {open ? '交易' : '休市'}
                </Tag>
              </div>
            );
          }}
        />
      </Card>
    </Flex>
  );
}
