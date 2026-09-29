import { Tabs } from 'antd';
import { useLocation, useNavigate } from 'react-router-dom';

const PAGES = [
  { key: '/backtest', label: '回测' },
  { key: '/backtest/compare', label: '对比' },
];

/**
 * Navigation between the backtest-domain pages.
 *
 * Same shape as the data and factor sections: the sidebar carries one entry per
 * section, and a section with several pages switches between them here. The
 * detail page is reached from a run row, so it is deliberately not a tab — its
 * `activeKey` would match nothing and the tab bar would go blank.
 */
export default function BacktestNav() {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <Tabs activeKey={location.pathname} onChange={key => navigate(key)} items={PAGES.map(page => ({ ...page }))} />
  );
}
