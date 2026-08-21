import type { StrategySignalItem } from '../types';

export default function StrategySignals({ signals }: { signals: StrategySignalItem[] | null }) {
  if (signals === null) return <p className="muted">无参考策略</p>;
  if (!signals.length) return <p className="muted">本周无策略信号</p>;
  return (
    <table>
      <thead><tr><th>代码</th><th>方向</th><th>目标权重</th><th>理由</th></tr></thead>
      <tbody>
        {signals.map((s, i) => (
          <tr key={i}>
            <td>{s.ts_code}</td>
            <td><span className={`tag ${s.direction === 'BUY' ? 'tag-buy' : 'tag-sell'}`}>{s.direction}</span></td>
            <td>{(s.target_pct * 100).toFixed(1)}%</td>
            <td>{s.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
