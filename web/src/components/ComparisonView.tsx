import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { ComparisonResult } from '../types';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

const LINE_LABELS: Record<string, string> = { manual: '手动', strategy: '策略', benchmark: '基准' };
const METRIC_LABELS: Record<string, string> = {
  total_return: '累计收益', annual_return: '年化收益', sharpe_ratio: '夏普比率',
  max_drawdown: '最大回撤', win_rate: '周胜率',
};

export default function ComparisonView({ sessionId }: { sessionId: string }) {
  const [data, setData] = useState<ComparisonResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    api.compare(sessionId)
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [sessionId]);

  if (loading) return <p className="muted">加载对比报告...</p>;
  if (error) return <p className="warn">{error}</p>;
  if (!data) return null;

  // Merge NAV data for chart
  const navMap = new Map<string, Record<string, number>>();
  for (const [line, label] of Object.entries(LINE_LABELS)) {
    const arr = data[`nav_${line}` as keyof typeof data] as { trade_date: string; nav: number }[] | null | undefined;
    if (!arr) continue;
    for (const pt of arr) {
      if (!navMap.has(pt.trade_date)) navMap.set(pt.trade_date, {});
      navMap.get(pt.trade_date)![label] = pt.nav;
    }
  }
  const chartData = Array.from(navMap.entries())
    .map(([date, vals]) => ({ date, ...vals }))
    .sort((a, b) => a.date.localeCompare(b.date));

  return (
    <div>
      {/* NAV Chart */}
      <div className="section-title">净值曲线</div>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} />
          <YAxis domain={['auto', 'auto']} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          <Line type="monotone" dataKey="手动" stroke="#3b82f6" strokeWidth={2} dot={false} />
          {data.nav_strategy && <Line type="monotone" dataKey="策略" stroke="#10b981" strokeWidth={1.5} strokeDasharray="6 2" dot={false} />}
          {data.nav_benchmark && <Line type="monotone" dataKey="基准" stroke="#9ca3af" strokeWidth={1} strokeDasharray="3 3" dot={false} />}
        </LineChart>
      </ResponsiveContainer>

      {/* Metrics */}
      <div className="section-title">关键指标</div>
      <div className="metrics-grid">
        {Object.entries(data.metrics).map(([line, m]) => (
          <div key={line} className="card" style={{ padding: 12, flex: 1, minWidth: 180 }}>
            <h3 style={{ fontSize: 14, marginBottom: 8 }}>{LINE_LABELS[line] || line}</h3>
            {Object.entries(METRIC_LABELS).map(([key, label]) => {
              const v = (m as any)[key];
              const isPct = ['total_return', 'annual_return', 'max_drawdown', 'win_rate'].includes(key);
              const cls = typeof v === 'number' && key !== 'max_drawdown' ? (v >= 0 ? 'pos' : 'neg') : '';
              return (
                <div key={key} style={{ fontSize: 13, margin: '4px 0', display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#64748b' }}>{label}</span>
                  <span className={cls} style={{ fontWeight: 600 }}>
                    {typeof v === 'number' ? (isPct ? (v * 100).toFixed(2) + '%' : v.toFixed(2)) : '-'}
                  </span>
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {/* Weekly Diff */}
      <div className="section-title">逐周决策差异</div>
      <table>
        <thead><tr><th>周</th><th>日期</th><th>你独有</th><th>策略独有</th><th>共同</th><th>警告</th></tr></thead>
        <tbody>
          {data.weekly_diffs.map(d => (
            <tr key={d.week_number}>
              <td>{d.week_number}</td>
              <td>{d.cursor_date}</td>
              <td>{d.user_only.join(', ') || '-'}</td>
              <td>{d.strategy_only.join(', ') || '-'}</td>
              <td>{d.common.join(', ') || '-'}</td>
              <td>{d.drawdown_warning ? <span className="warn">{d.drawdown_warning}</span> : '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {data.html_path && (
        <div style={{ marginTop: 12, fontSize: 13, color: '#64748b' }}>
          HTML 报告: <code>{data.html_path}</code>
        </div>
      )}
    </div>
  );
}
