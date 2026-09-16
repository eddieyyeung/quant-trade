import type { PortfolioItem } from '../types';

export default function PortfolioTable({ items }: { items: PortfolioItem[] }) {
  if (!items.length) return <p className="muted">空仓</p>;
  return (
    <table>
      <thead>
        <tr><th>代码</th><th>股数</th><th>均价</th><th>现价</th><th>盈亏</th><th>权重</th></tr>
      </thead>
      <tbody>
        {items.map(p => (
          <tr key={p.ts_code}>
            <td>{p.ts_code}</td>
            <td>{p.shares.toLocaleString()}</td>
            <td>¥{p.avg_cost.toFixed(2)}</td>
            <td>¥{p.current_price?.toFixed(2) || '-'}</td>
            <td className={p.pnl_pct >= 0 ? 'pos' : 'neg'}>{(p.pnl_pct * 100).toFixed(2)}%</td>
            <td>{(p.weight_pct * 100).toFixed(1)}%</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
