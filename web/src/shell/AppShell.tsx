import { Badge, Layout, Menu, Space, Tooltip, Typography, theme } from 'antd';
import { Link, useLocation } from 'react-router-dom';

import { useHealth, type HealthState } from '../api/health';
import { SECTIONS, sectionFor } from './navigation';
import { AppRoutes } from './routes';

const { Content, Header, Sider } = Layout;

/**
 * The platform frame: sidebar navigation, a header carrying the current
 * section and platform reachability, and the routed page below.
 */
export default function AppShell() {
  const { token } = theme.useToken();
  const location = useLocation();
  const health = useHealth();
  const active = sectionFor(location.pathname);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="light" width={216} breakpoint="lg" collapsedWidth={64}>
        <div
          style={{
            height: 56,
            display: 'flex',
            alignItems: 'center',
            padding: '0 20px',
            fontWeight: 600,
            fontSize: 15,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            borderBottom: `1px solid ${token.colorSplit}`,
          }}
        >
          量化研究平台
        </div>
        <Menu
          mode="inline"
          selectedKeys={active ? [active.key] : []}
          style={{ borderInlineEnd: 'none' }}
          items={SECTIONS.map(section => ({
            key: section.key,
            icon: section.icon,
            label: <Link to={section.path}>{section.label}</Link>,
          }))}
        />
      </Sider>

      <Layout>
        <Header
          style={{
            background: token.colorBgContainer,
            borderBottom: `1px solid ${token.colorSplit}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <Typography.Text strong>{active?.label ?? ''}</Typography.Text>
          <HealthBadge state={health} />
        </Header>

        <Content style={{ padding: 24 }}>
          <AppRoutes />
        </Content>
      </Layout>
    </Layout>
  );
}

/** Platform and database reachability, as reported by `GET /api/health`. */
function HealthBadge({ state }: { state: HealthState }) {
  if (state.kind === 'checking') {
    return <Badge status="default" text="检测中…" />;
  }
  if (state.kind === 'unreachable') {
    return (
      <Tooltip title={state.message}>
        <Badge status="error" text="后端未连接" />
      </Tooltip>
    );
  }
  if (!state.health.db_reachable) {
    return (
      <Tooltip title={state.health.detail || state.health.db_path}>
        <Badge status="warning" text="数据库不可达" />
      </Tooltip>
    );
  }
  return (
    <Space size={6}>
      <Tooltip title={state.health.db_path}>
        <Badge status="success" text="已连接" />
      </Tooltip>
    </Space>
  );
}
