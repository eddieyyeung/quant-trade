import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { SessionDetail as SD, Snapshot } from '../types';
import PortfolioTable from './PortfolioTable';
import FactorRanking from './FactorRanking';
import StrategySignals from './StrategySignals';
import DecisionForm from './DecisionForm';
import ComparisonView from './ComparisonView';

interface Props {
  sessionId: string;
  onBack: () => void;
}

export default function SessionDetail({ sessionId, onBack }: Props) {
  const [data, setData] = useState<SD | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [view, setView] = useState<'detail' | 'compare'>('detail');

  const load = () => {
    setLoading(true);
    api.getSession(sessionId)
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, [sessionId]);

  if (loading) return <div className="card"><p className="muted">加载中...</p></div>;
  if (error) return <div className="card"><p className="warn">{error}</p><button className="btn btn-sm" onClick={onBack}>返回</button></div>;
  if (!data) return null;

  const snap: Snapshot = data.snapshot;
  const m = snap.market;

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h2>会话 {sessionId.slice(0, 8)}... | 第 {data.week_number}/{data.total_weeks} 周 | {snap.signal_date}</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-sm" onClick={() => setView(view === 'detail' ? 'compare' : 'detail')}>
            {view === 'detail' ? '对比报告' : '返回决策'}
          </button>
          <button className="btn btn-sm btn-gray" onClick={onBack}>返回列表</button>
        </div>
      </div>

      {view === 'compare' ? (
        <ComparisonView sessionId={sessionId} />
      ) : (
        <>
          {/* Market overview */}
          <div className="metrics-grid">
            {m && <div className="metric-card"><div className="val">{m.benchmark_close?.toFixed(0)}</div><div className="lbl">沪深300</div></div>}
            {m && <div className="metric-card"><div className={`val ${m.benchmark_weekly_return >= 0 ? 'pos' : 'neg'}`}>{(m.benchmark_weekly_return * 100).toFixed(2)}%</div><div className="lbl">周涨跌</div></div>}
            <div className="metric-card"><div className="val">¥{snap.total_value.toLocaleString()}</div><div className="lbl">组合市值</div></div>
            <div className="metric-card"><div className="val">¥{snap.cash.toLocaleString()}</div><div className="lbl">现金</div></div>
            <div className="metric-card"><div className="val">{data.previous_decisions}</div><div className="lbl">已决策次数</div></div>
          </div>

          {/* Warnings */}
          {snap.data_warnings.length > 0 && (
            <div style={{ margin: '12px 0' }}>
              {snap.data_warnings.map((w, i) => <div key={i} className="warn">{w}</div>)}
            </div>
          )}

          <div className="section-title">持仓</div>
          <PortfolioTable items={snap.portfolio} />

          <div className="section-title">因子排名 Top 15</div>
          <FactorRanking items={snap.factor_ranking} />

          <div className="section-title">策略建议</div>
          <StrategySignals signals={snap.strategy_signals} />

          <DecisionForm sessionId={sessionId} onExecuted={load} />
        </>
      )}
    </div>
  );
}
