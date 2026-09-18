import { Tabs } from 'antd';
import { useLocation, useNavigate } from 'react-router-dom';

const PAGES = [
  { key: '/models', label: '训练' },
  { key: '/models/evaluate', label: '评估' },
  { key: '/models/predict', label: '预测' },
];

/** The page a pathname belongs to, so `/models/evaluate/<id>` lights up 评估. */
function activeKeyFor(pathname: string): string {
  let active = PAGES[0].key;
  for (const page of PAGES) {
    if (pathname === page.key || pathname.startsWith(`${page.key}/`)) active = page.key;
  }
  return active;
}

/**
 * Navigation between the model-domain pages.
 *
 * Same shape as the data and factor sections. Evaluation is the one page that
 * only exists for a chosen run, so its tab is a position marker rather than a
 * destination: it is disabled while no evaluation is open — a link to
 * `/models/evaluate` has nothing to show — and the click handler never routes
 * there. Entering it happens from a row in the training history.
 */
export default function ModelNav() {
  const location = useLocation();
  const navigate = useNavigate();

  const active = activeKeyFor(location.pathname);

  return (
    <Tabs
      activeKey={active}
      onChange={key => {
        if (key !== '/models/evaluate') navigate(key);
      }}
      items={PAGES.map(page => ({
        key: page.key,
        label: page.label,
        disabled: page.key === '/models/evaluate' && active !== page.key,
      }))}
    />
  );
}
