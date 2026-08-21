import { useState, useCallback, useEffect } from 'react';
import CreateSession from './components/CreateSession';
import SessionList from './components/SessionList';
import SessionDetail from './components/SessionDetail';

export default function App() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [apiOk, setApiOk] = useState(false);

  useEffect(() => {
    const base = import.meta.env.VITE_API_BASE || '/api';
    fetch(`${base}/sessions`)
      .then(() => setApiOk(true))
      .catch(() => setApiOk(false));
  }, []);

  const handleCreated = useCallback((id: string) => {
    setSelectedId(id);
    setRefreshKey(k => k + 1);
  }, []);

  const handleSelect = useCallback((id: string) => setSelectedId(id), []);

  const handleBack = useCallback(() => {
    setSelectedId(null);
    setRefreshKey(k => k + 1);
  }, []);

  return (
    <div>
      <header className="header">
        <h1>Quant Simulator</h1>
        <span className={`badge ${apiOk ? 'badge-ok' : 'badge-err'}`}>
          {apiOk ? 'API 已连接' : 'API 未连接'}
        </span>
      </header>
      <main className="container">
        {selectedId ? (
          <SessionDetail sessionId={selectedId} onBack={handleBack} />
        ) : (
          <>
            <CreateSession onCreated={handleCreated} />
            <SessionList onSelect={handleSelect} refreshKey={refreshKey} />
          </>
        )}
      </main>
    </div>
  );
}
