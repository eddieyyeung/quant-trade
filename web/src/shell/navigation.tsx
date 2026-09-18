import {
  AppstoreOutlined,
  BarChartOutlined,
  DashboardOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  FundOutlined,
  SlidersOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { ReactNode } from 'react';

/** One section of the platform, as it appears in the sidebar. */
export interface Section {
  /** Route prefix; also the menu key. */
  key: string;
  path: string;
  label: string;
  icon: ReactNode;
  /** False until the change that builds this section has shipped. */
  implemented: boolean;
}

/**
 * The eight sections of the platform.
 *
 * Order is the research workflow order: get the data, derive factors, train a
 * model, turn it into signals, test it, paper-trade it, report on it — with the
 * job centre last because everything else feeds it.
 */
export const SECTIONS: Section[] = [
  { key: '/data', path: '/data', label: '数据', icon: <DashboardOutlined />, implemented: true },
  { key: '/factors', path: '/factors', label: '因子', icon: <ExperimentOutlined />, implemented: true },
  { key: '/models', path: '/models', label: '模型', icon: <ThunderboltOutlined />, implemented: true },
  { key: '/strategies', path: '/strategies', label: '策略', icon: <SlidersOutlined />, implemented: true },
  { key: '/backtest', path: '/backtest', label: '回测', icon: <FundOutlined />, implemented: true },
  { key: '/simulator', path: '/simulator', label: '仿真', icon: <AppstoreOutlined />, implemented: true },
  { key: '/reports', path: '/reports', label: '报告', icon: <FileTextOutlined />, implemented: true },
  { key: '/jobs', path: '/jobs', label: '任务中心', icon: <BarChartOutlined />, implemented: true },
];

/** The section whose route prefix matches a pathname, for menu highlighting. */
export function sectionFor(pathname: string): Section | undefined {
  return SECTIONS.find(s => pathname === s.path || pathname.startsWith(`${s.path}/`));
}
