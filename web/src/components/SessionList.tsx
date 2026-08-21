import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { SessionSummary } from '../types';

interface Props {
  onSelect: (id: string) => void;
  refreshKey: number;
}

export default function SessionList({ onSelect, refreshKey }: Props) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    api.listSessions()
      .then(data => { if (!cancelled) setSessions(data); })
      .catch(e => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshKey]);

  const handleDelete = async (id: string) => {
    if (!confirm('确认删除？')) return;
    try {
      await api.deleteSession(id);
      setSessions(prev => prev.filter(s => s.id !== id));
    } catch (e: any) {
      alert('删除失败: ' + e.message);
    }
  };

  if (loading) return <div className="card"><p className="muted">加载中...</p></div>;
  if (error) return <div className="card"><p className="warn">{error}</p></div>;

  const statusTag = (s: string) => {
    const map: Record<string, string> = { active: 'tag-active', paused: 'tag-paused', completed: 'tag-completed' };
    return map[s] || 'tag-active';
  };

  return (
    <div className="card">
      <h2>会话列表 ({sessions.length})</h2>
      {sessions.length === 0 ? (
        <p className="muted">无会话，请创建</p>
      ) : (
        <table>
          <thead>
            <tr><th>ID</th><th>名称</th><th>状态</th><th>当前日期</th><th>策略</th><th>操作</th></tr>
          </thead>
          <tbody>
            {sessions.map(s => (
              <tr key={s.id}>
                <td>{s.id.slice(0, 8)}...</td>
                <td>{s.name}</td>
                <td><span className={`tag ${statusTag(s.status)}`}>{s.status}</span></td>
                <td>{s.cursor_date || '-'}</td>
                <td>{s.reference_strategy || '-'}</td>
                <td>
                  <button className="btn btn-sm btn-primary" onClick={() => onSelect(s.id)}>查看</button>
                  <button className="btn btn-sm btn-danger" onClick={() => handleDelete(s.id)}>删</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
