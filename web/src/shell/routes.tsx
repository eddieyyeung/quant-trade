import { Navigate, Route, Routes } from 'react-router-dom';

import Compare from '../pages/backtest/Compare';
import Detail from '../pages/backtest/Detail';
import List from '../pages/backtest/List';
import Calendar from '../pages/data/Calendar';
import Overview from '../pages/data/Overview';
import SyncForm from '../pages/data/SyncForm';
import Universe from '../pages/data/Universe';
import Correlation from '../pages/factors/Correlation';
import IcAnalysis from '../pages/factors/IcAnalysis';
import Library from '../pages/factors/Library';
import Quantile from '../pages/factors/Quantile';
import RunDetail from '../pages/jobs/RunDetail';
import RunList from '../pages/jobs/RunList';
import Evaluate from '../pages/models/Evaluate';
import Predict from '../pages/models/Predict';
import Train from '../pages/models/Train';
import ReportList from '../pages/reports/List';
import ReportPreview from '../pages/reports/Preview';
import SimulatorList from '../pages/simulator/SessionList';
import SimulatorDetail from '../pages/simulator/SessionDetail';
import StrategyList from '../pages/strategies/List';
import StrategyRuns from '../pages/strategies/Runs';
import StrategySignals from '../pages/strategies/Signals';
import NotFound from './NotFound';
import Placeholder from './Placeholder';
import { SECTIONS } from './navigation';

/**
 * The platform's route table.
 *
 * A section that has not been built yet still resolves — to a placeholder that
 * names it — so navigation never dead-ends or shows a blank page.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/data" replace />} />

      <Route path="/data" element={<Overview />} />
      <Route path="/data/sync" element={<SyncForm />} />
      <Route path="/data/universe" element={<Universe />} />
      <Route path="/data/calendar" element={<Calendar />} />

      <Route path="/factors" element={<Library />} />
      <Route path="/factors/ic" element={<IcAnalysis />} />
      <Route path="/factors/quantile" element={<Quantile />} />
      <Route path="/factors/correlation" element={<Correlation />} />

      <Route path="/models" element={<Train />} />
      {/* Evaluation is a sub-page rather than a second route segment: it only
          exists for a chosen run, and a `:runId` sibling of `/predict` would
          need the declaration-order care `/backtest/compare` does. */}
      <Route path="/models/evaluate/:runId" element={<Evaluate />} />
      <Route path="/models/predict" element={<Predict />} />

      <Route path="/backtest" element={<List />} />
      {/* `compare` before `:runId`: React Router ranks static segments above
          dynamic ones, but declaring it in this order keeps the intent plain. */}
      <Route path="/backtest/compare" element={<Compare />} />
      <Route path="/backtest/:runId" element={<Detail />} />

      <Route path="/reports" element={<ReportList />} />
      {/* The preview hangs off a list row, the way a backtest detail does, so
          it is not a sibling segment and has no tab of its own. */}
      <Route path="/reports/:runId" element={<ReportPreview />} />

      <Route path="/simulator" element={<SimulatorList />} />
      {/* Reached from a session row. The decision desk carries its own
          decision/compare switch in-page, so compare needs no route. */}
      <Route path="/simulator/:sessionId" element={<SimulatorDetail />} />

      <Route path="/strategies" element={<StrategyList />} />
      {/* The static segment is declared before the dynamic one: a `:runId`
          route first would swallow `/strategies/signals` itself. */}
      <Route path="/strategies/signals" element={<StrategyRuns />} />
      {/* One run's signals, reached from a history row. Not its own tab — the
          信号 tab stays lit because the path sits under it. */}
      <Route path="/strategies/signals/:runId" element={<StrategySignals />} />

      <Route path="/jobs" element={<RunList />} />
      <Route path="/jobs/:runId" element={<RunDetail />} />

      {SECTIONS.filter(section => !section.implemented).map(section => (
        <Route key={section.key} path={`${section.path}/*`} element={<Placeholder section={section} />} />
      ))}

      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
