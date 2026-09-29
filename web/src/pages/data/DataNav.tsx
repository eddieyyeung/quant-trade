import { Tabs } from 'antd';
import { useLocation, useNavigate } from 'react-router-dom';

const PAGES = [
  { key: '/data', label: '总览' },
  { key: '/data/sync', label: '同步任务' },
  { key: '/data/universe', label: '股票池' },
  { key: '/data/calendar', label: '交易日历' },
];

/**
 * Navigation between the data-domain pages.
 *
 * The sidebar carries one entry per section; a section with several pages
 * switches between them here rather than growing a submenu.
 */
export default function DataNav() {
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
