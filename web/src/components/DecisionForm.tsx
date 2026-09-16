import { useState, type FormEvent } from 'react';
import { api } from '../api/client';
import type { OrderRequest } from '../types';

interface Props {
  sessionId: string;
  onExecuted: () => void;
}

export default function DecisionForm({ sessionId, onExecuted }: Props) {
  const [buyInput, setBuyInput] = useState('');
  const [sellInput, setSellInput] = useState('');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const parseOrders = (): OrderRequest[] => {
    const orders: OrderRequest[] = [];
    if (buyInput.trim()) {
      buyInput.split(',').forEach(p => {
        const [code, pct] = p.trim().split(':');
        if (code && pct) orders.push({ ts_code: code.trim(), target_pct: Number(pct) / 100, direction: 'BUY' });
      });
    }
    if (sellInput.trim()) {
      sellInput.split(',').forEach(c => {
        const code = c.trim();
        if (code) orders.push({ ts_code: code, target_pct: 0, direction: 'SELL' });
      });
    }
    return orders;
  };

  const handleStep = async (e: FormEvent) => {
    e.preventDefault();
    const orders = parseOrders();
    if (!orders.length && !notes.trim()) {
      if (!confirm('无订单无备注，确认提交空操作？')) return;
    }
    setSubmitting(true);
    try {
      const r = await api.step(sessionId, orders, notes);
      const msgs: string[] = [`成交 ${r.executed_orders.length} 笔`];
      r.executed_orders.forEach(o => msgs.push(`${o.direction} ${o.ts_code} ${o.shares}股 @ ¥${o.price}`));
      if (r.warnings.length) msgs.push('⚠ ' + r.warnings.join(', '));
      alert(msgs.join('\n'));
      setBuyInput(''); setSellInput(''); setNotes('');
      onExecuted();
    } catch (e: any) {
      alert('执行失败: ' + e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleSkip = async () => {
    if (!confirm('确认跳过本周调仓？')) return;
    setSubmitting(true);
    try {
      await api.skip(sessionId);
      onExecuted();
    } catch (e: any) {
      alert('跳过失败: ' + e.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleStep}>
      <div className="section-title">操作</div>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <label className="field" style={{ flex: 2 }}>
          <span>买入 (代码:百分比)</span>
          <input value={buyInput} onChange={e => setBuyInput(e.target.value)} placeholder="600519.SH:10, 002594.SZ:8" />
        </label>
        <label className="field" style={{ flex: 1 }}>
          <span>卖出 (代码)</span>
          <input value={sellInput} onChange={e => setSellInput(e.target.value)} placeholder="300750.SZ" />
        </label>
        <label className="field" style={{ flex: 2 }}>
          <span>备注</span>
          <input value={notes} onChange={e => setNotes(e.target.value)} placeholder="看好消费" />
        </label>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting ? '执行中...' : '提交决策'}
        </button>
        <button type="button" className="btn btn-success" onClick={handleSkip} disabled={submitting}>
          跳过本周
        </button>
      </div>
    </form>
  );
}
