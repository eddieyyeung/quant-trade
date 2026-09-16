import type { FactorRankItem } from '../types';

export default function FactorRanking({ items }: { items: FactorRankItem[] }) {
  if (!items.length) return <p className="muted">暂无因子数据</p>;
  return (
    <table>
      <thead><tr><th>排名</th><th>代码</th><th>综合得分</th></tr></thead>
      <tbody>
        {items.map(f => (
          <tr key={f.rank}>
            <td>{f.rank}</td>
            <td>{f.ts_code}</td>
            <td>{f.composite_score.toFixed(2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
