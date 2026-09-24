import { useCallback, useEffect, useState } from 'react';
import { App as AntdApp, Alert, Button, Card, Col, Flex, Row, Segmented, Statistic, Tag, Typography } from 'antd';
import { Link, useParams } from 'react-router-dom';

import { simulatorApi, type SessionDetail as SessionDetailData } from '../../api/simulator';
import { formatDate, formatPercent } from '../../utils/format';
import ComparisonView from './ComparisonView';
import DecisionForm from './DecisionForm';
import FactorRanking from './FactorRanking';
import PortfolioTable from './PortfolioTable';
import StrategySignals from './StrategySignals';

type View = 'decision' | 'compare';

export default function SessionDetail() {
  const { sessionId = '' } = useParams();
  const { message } = AntdApp.useApp();

  const [detail, setDetail] = useState<SessionDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [missing, setMissing] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  /**
   * The decision/compare switch lives in page state rather than in the route.
   * Comparing is a way of looking at the session you are already on, so
   * switching must not remount this component and refetch the snapshot.
   */
  const [view, setView] = useState<View>('decision');

  // React Router reuses this instance when only `:sessionId` changes, so a
  // stale session's holdings would otherwise sit under the next session's URL.
  useEffect(() => {
    setDetail(null);
    setMissing(false);
    setFailure(null);
    setLoading(true);
    setView('decision');
  }, [sessionId]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const body = await simulatorApi.getSession(sessionId);
      setDetail(body);
      setMissing(false);
      setFailure(null);
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      if ((e as { status?: number }).status === 404) {
        setMissing(true);
      } else {
        // A toast disappears; without this the page would render nothing at
        // all, which reads as broken rather than as a failed request.
        setFailure(text);
        message.error(text);
      }
    } finally {
      setLoading(false);
    }
  }, [message, sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (missing) {
    return (
      <Alert
        type="error"
        showIcon
        message={`会话 ${sessionId} 不存在`}
        description="该会话可能已被删除。"
        action={
          <Link to="/simulator">
            <Button size="small">返回会话列表</Button>
          </Link>
        }
      />
    );
  }

  if (loading && detail === null) {
    return <Card loading />;
  }

  if (failure !== null && detail === null) {
    return (
      <Alert
        type="error"
        showIcon
        message="会话加载失败"
        description={failure}
        action={
          <Flex gap={8}>
            <Button size="small" onClick={() => void load()}>
              重试
            </Button>
            <Link to="/simulator">
              <Button size="small">返回列表</Button>
            </Link>
          </Flex>
        }
      />
    );
  }

  if (detail === null) return null;

  const snapshot = detail.snapshot;
  const market = snapshot.market;

  return (
    <Flex vertical gap={16}>
      <Card
        title={
          <Flex gap={12} align="center" wrap>
            <span>会话 {detail.session_id.slice(0, 8)}…</span>
            <Tag>
              第 {detail.week_number}/{detail.total_weeks} 周
            </Tag>
            <Typography.Text type="secondary">信号日 {formatDate(snapshot.signal_date)}</Typography.Text>
          </Flex>
        }
        extra={
          <Flex gap={8} align="center">
            <Segmented<View>
              value={view}
              onChange={value => setView(value)}
              options={[
                { value: 'decision', label: '决策' },
                { value: 'compare', label: '对比' },
              ]}
            />
            <Link to="/simulator">
              <Button size="small">返回列表</Button>
            </Link>
          </Flex>
        }
      >
        <Row gutter={[16, 16]}>
          {/* Benchmark figures are rendered only when there is a market block.
              The backend leaves it null when the index has no synced rows, and
              a 0 here would read as "the index closed at zero". */}
          {market && (
            <Col xs={12} md={8} lg={4}>
              <Statistic title="沪深300" value={market.benchmark_close} precision={2} />
            </Col>
          )}
          {market && (
            <Col xs={12} md={8} lg={4}>
              <Statistic
                title="基准周涨跌"
                value={formatPercent(market.benchmark_weekly_return, 2)}
                valueStyle={{ color: market.benchmark_weekly_return >= 0 ? '#3f8600' : '#cf1322' }}
              />
            </Col>
          )}
          <Col xs={12} md={8} lg={4}>
            <Statistic title="组合市值" value={snapshot.total_value} precision={2} prefix="¥" />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <Statistic title="现金" value={snapshot.cash} precision={2} prefix="¥" />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <Statistic title="已决策次数" value={detail.previous_decisions} precision={0} />
          </Col>
        </Row>

        {snapshot.data_warnings.length > 0 && (
          <Flex vertical gap={8} style={{ marginTop: 16 }}>
            {snapshot.data_warnings.map(warning => (
              <Alert key={warning} type="warning" showIcon message={warning} />
            ))}
          </Flex>
        )}
      </Card>

      {view === 'compare' ? (
        <ComparisonView sessionId={detail.session_id} />
      ) : (
        <>
          <Card size="small" title="当前持仓">
            <PortfolioTable items={snapshot.portfolio} />
          </Card>

          <Card size="small" title="因子排名">
            <FactorRanking items={snapshot.factor_ranking} />
          </Card>

          <Card size="small" title="参考策略信号">
            <StrategySignals signals={snapshot.strategy_signals} />
          </Card>

          <Card size="small" title="本周决策">
            <DecisionForm
              sessionId={detail.session_id}
              recommendedOrders={snapshot.recommended_orders}
              recommendationSource={snapshot.recommendation_source}
              onExecuted={() => void load()}
            />
          </Card>
        </>
      )}
    </Flex>
  );
}
