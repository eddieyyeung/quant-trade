import { Tabs } from 'antd';
import { useLocation, useNavigate } from 'react-router-dom';

const PAGES = [
  { key: '/factors', label: '因子库' },
  { key: '/factors/ic', label: 'IC 分析' },
  { key: '/factors/quantile', label: '分层回测' },
  { key: '/factors/correlation', label: '相关性' },
];

/**
 * Navigation between the factor-domain pages.
 *
 * Same shape as the data section: the sidebar carries one entry per section,
 * and a section with several pages switches between them here.
 */
export default function FactorNav() {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <Tabs
      activeKey={location.pathname}
      onChange={key => navigate(key)}
      items={PAGES.map(page => ({ key: page.key, label: page.label }))}
    />
  );
}
