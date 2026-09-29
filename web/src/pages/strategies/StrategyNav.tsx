import { Tabs } from 'antd';
import { useLocation, useNavigate } from 'react-router-dom';

const PAGES = [
  { key: '/strategies', label: '策略' },
  { key: '/strategies/signals', label: '信号' },
];

/**
 * Navigation between the strategy-domain pages.
 *
 * Same shape as the data, factor, model and backtest sections: the sidebar
 * carries one entry per section, and a section with several pages switches
 * between them here. The signal detail is reached from a history row, so it is
 * deliberately not a tab — its `activeKey` would match nothing and the tab bar
 * would go blank.
 */
export default function StrategyNav() {
  const location = useLocation();
  const navigate = useNavigate();

  // A detail path sits under `/strategies/signals/`, so its tab is the one
  // whose key the path starts with, not the one it equals.
  const active = PAGES.map(page => page.key)
    .filter(key => location.pathname === key || location.pathname.startsWith(`${key}/`))
    .sort((a, b) => b.length - a.length)[0];

  return (
    <Tabs activeKey={active} onChange={key => navigate(key)} items={PAGES.map(page => ({ ...page }))} />
  );
}
