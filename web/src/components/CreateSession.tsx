import { useState, type FormEvent } from 'react';
import { api } from '../api/client';

interface Props {
  onCreated: (id: string) => void;
}

export default function CreateSession({ onCreated }: Props) {
  const [name, setName] = useState('复盘测试');
  const [startDate, setStartDate] = useState('2023-06-01');
  const [endDate, setEndDate] = useState('');
  const [capital, setCapital] = useState('100000');
  const [ref, setRef] = useState('factor_ranking');
  const [creating, setCreating] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setCreating(true);
    setStatus('正在创建会话...');

    const params = {
      name,
      start_date: startDate,
      end_date: endDate || undefined,
      capital: Number(capital),
      ref: ref || undefined,
    };
    console.log('[CreateSession] 提交参数:', params);

    try {
      setStatus('正在调用 API...');
      const r = await api.createSession(params);
      console.log('[CreateSession] 创建成功:', r);
      setStatus('会话已创建！加载详情...');
      onCreated(r.session_id);
    } catch (err: any) {
      const msg = err.message || String(err);
      console.error('[CreateSession] 创建失败:', msg, err);
      setError(msg);
      setStatus('');
    } finally {
      setCreating(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="card">
      <h2>创建会话</h2>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <label className="field">
          <span>名称</span>
          <input value={name} onChange={e => setName(e.target.value)} disabled={creating} />
        </label>
        <label className="field">
          <span>起始日期</span>
          <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} disabled={creating} />
        </label>
        <label className="field">
          <span>结束日期 (可选)</span>
          <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} disabled={creating} />
        </label>
        <label className="field">
          <span>初始资金</span>
          <input type="number" value={capital} onChange={e => setCapital(e.target.value)} disabled={creating} />
        </label>
        <label className="field">
          <span>参考策略</span>
          <select value={ref} onChange={e => setRef(e.target.value)} disabled={creating}>
            <option value="">无</option>
            <option value="factor_ranking">因子排名</option>
          </select>
        </label>
      </div>

      {/* Progress */}
      {creating && (
        <div className="progress-bar" style={{ marginBottom: 12 }}>
          <div className="progress-bar-inner" />
          <span style={{ marginLeft: 12, fontSize: 13, color: '#64748b' }}>{status}</span>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="warn" style={{ marginBottom: 12, display: 'block' }}>
          创建失败: {error}
        </div>
      )}

      <button type="submit" className="btn btn-primary" disabled={creating}>
        {creating ? '创建中...' : '创建'}
      </button>
    </form>
  );
}
