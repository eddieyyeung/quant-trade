/**
 * End-to-end check for the report browser, the rebuilt simulator, and the
 * strategy pages.
 *
 * Drives a headless Chromium through the flows a user takes — read the report
 * list, preview and download a report, create a session, read the decision
 * desk, switch to the comparison view, submit a signal generation, read the
 * signal history and one run's signals — and asserts the claims in the
 * `report-browser-ui`, `simulator-antd-ui` and `strategy-research-ui` specs.
 * The simulator pages were rebuilt from scratch in the C6 change, so this is
 * also their zero-regression pass: every interaction the old standalone app had
 * is exercised here or in the source contracts.
 *
 * Both signal runs are asserted. The seeded one carries fixed counts, for the
 * read path; the one this script submits is asserted against its real output,
 * because that output is a property of this fixture. It was not, until the
 * `fix-factor-store-injection` change — see the assertion in section 10.
 *
 * Not part of `pytest` — it needs a running server and a browser.
 *
 * Usage (from the repository root):
 *   uv run python scripts/seed_report_simulator_e2e_db.py
 *   QUANT_CONFIG=.scratch/e2e-report-sim/config.yaml uv run python -m quant_trade &
 *   node scripts/e2e_report_simulator_pages.mjs
 *
 * What this cannot check: the report renders inside an iframe, so assertions
 * about its contents are made against `/api/reports/{id}/html` rather than the
 * frame's DOM; and ECharts paints into a canvas, so the comparison chart is
 * checked for its container and its empty state, not its pixels.
 */
import { chromium } from 'playwright-core';

const BASE = 'http://127.0.0.1:9555';

// The universe `seed_report_simulator_e2e_db.py` writes into the scratch
// database's `index_weights`. Kept in step with `CODES` there by hand — the two
// scripts are run as a pair.
const SCRATCH_UNIVERSE = [
  '000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ', '000005.SZ',
  '000006.SZ', '000007.SZ', '000008.SZ', '000009.SZ', '000010.SZ',
];
const SHELL = `${process.env.HOME}/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell`;

let passed = 0;
let failed = 0;
function check(label, condition, detail = '') {
  if (condition) {
    passed += 1;
    console.log(`  ok   ${label}`);
  } else {
    failed += 1;
    console.log(`  FAIL ${label}${detail ? ` — ${detail}` : ''}`);
  }
}

const browser = await chromium.launch({ executablePath: SHELL });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const consoleErrors = [];
page.on('console', msg => {
  if (msg.type() === 'error') consoleErrors.push(msg.text());
});
page.on('pageerror', err => consoleErrors.push(`pageerror: ${err.message}`));

async function goto(path) {
  await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle' });
}

async function settle(ms = 1200) {
  await page.waitForTimeout(ms);
}

/** Poll a run until it reaches a terminal status. */
async function awaitRun(runId, timeoutMs = 180000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const body = await fetch(`${BASE}/api/runs/${runId}`).then(r => r.json());
    if (['ok', 'failed', 'cancelled', 'interrupted'].includes(body.status)) return body;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error(`run ${runId} never finished`);
}

// --- 1. the report list -----------------------------------------------------
console.log('report list');
await goto('/reports');
let text = await page.textContent('body');
check('the section is not the placeholder', !text.includes('该分区由后续变更提供'));
check('the seeded run is listed', text.includes('已完成'), text.slice(0, 200));
check('data freshness is shown', text.includes('数据时效'));
check('the signal count is shown', text.includes('调仓信号'));
check('the trade count is shown', text.includes('成交笔数'));
check('the report file name is shown', text.includes('weekly_2026_07_24.html'));

// --- 2. the report's own bytes ---------------------------------------------
console.log('report document');
const list = await fetch(`${BASE}/api/reports`).then(r => r.json());
check('the API lists exactly the seeded run', list.total === 1, JSON.stringify(list.total));
const runId = list.items[0].run_id;
check('freshness travels in the artifact meta', list.items[0].signal_date === '2026-07-24');

const inline = await fetch(`${BASE}/api/reports/${runId}/html`);
const inlineHtml = await inline.text();
check('the document is served as HTML', inline.headers.get('content-type').includes('text/html'));
check('no download prompt when previewing', !inline.headers.get('content-disposition'));
// The two sections this change exists to fix: both were empty or absent before.
check('the IC panel has rows', inlineHtml.includes('因子表现跟踪') && inlineHtml.includes('MA20'));
check('the trade table has rows', inlineHtml.includes('交易明细') && inlineHtml.includes('共 7 笔'));
check('turnover is not rendered', !inlineHtml.includes('换手'));
check('no false Friday notice on a Friday signal date', !inlineHtml.includes('非调仓日运行'));

const download = await fetch(`${BASE}/api/reports/${runId}/html?download=1`);
check(
  'downloading names the report file',
  download.headers.get('content-disposition') === 'attachment; filename="weekly_2026_07_24.html"',
  download.headers.get('content-disposition') ?? 'none',
);

// --- 3. the preview page ----------------------------------------------------
console.log('report preview');
await goto(`/reports/${runId}`);
text = await page.textContent('body');
check('the preview is not a 404', !text.includes('不存在'), text.slice(0, 200));
check('the report is embedded', (await page.locator('iframe').count()) === 1);
check('the source run is named', text.includes(runId));
check('freshness is on the page', text.includes('2026-07-24'));
// antd inserts a space between two CJK glyphs, so the label renders as 下 载.
check('a download entry exists', (await page.getByRole('button', { name: /下\s*载/ }).count()) === 1);
check(
  'the download points at the flagged document URL',
  (await page.locator('a[href*="/html?download=1"]').count()) === 1,
);

// A direct load of the deep link, not a client-side transition.
await page.reload({ waitUntil: 'networkidle' });
check('refreshing the deep link does not 404', !(await page.textContent('body')).includes('页面不存在'));

// --- 4. an unregistered report is a not-found, not a blank page -------------
await goto('/reports/does-not-exist');
text = await page.textContent('body');
check('an unknown report says so', text.includes('不存在'));
check('and offers a way back', text.includes('返回报告列表'));

// Visiting a report that does not exist is a deliberate 404, so the console
// error log starts clean from here; an error after this point is a real one.
const expected404 = consoleErrors.length;

// --- 5. the simulator landing page -----------------------------------------
console.log('simulator list');
await goto('/simulator');
text = await page.textContent('body');
check('the section is not the placeholder', !text.includes('该分区由后续变更提供'));
check('the create form is present', text.includes('创建会话'));
check('the strategy list is populated from the backend', text.includes('参考策略'));

// --- 6. create a session from the form --------------------------------------
console.log('create a session');
await page.locator('#name').fill('E2E 复盘会话');
const pickers = page.locator('.ant-picker-input input');
await pickers.nth(0).click();
await pickers.nth(0).fill('2024-12-02');
await page.keyboard.press('Enter');
await page.locator('.ant-picker-dropdown').waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {});
await Promise.all([
  page.waitForURL(/\/simulator\/[0-9a-f-]+/, { timeout: 60000 }),
  page.getByRole('button', { name: '创建' }).click().catch(() => {}),
]);
const sessionId = page.url().split('/simulator/')[1];
check('creating navigates to the decision desk', Boolean(sessionId), page.url());

// --- 7. the decision desk ---------------------------------------------------
console.log('decision desk');
await settle(2500);
text = await page.textContent('body');
check('the desk is not a 404', !text.includes('不存在'), text.slice(0, 300));
check('the portfolio value is shown', text.includes('组合市值'));
check('cash is shown', text.includes('现金'));
check('the decision count is shown', text.includes('已决策次数'));
check('the week position is shown', /第 \d+\/\d+ 周/.test(text));
check('holdings render', text.includes('当前持仓'), 'expected the 空仓 empty state at minimum');
check('factor ranking renders', text.includes('因子排名'));
check('the reference signals render', text.includes('参考策略信号'));
// `null` and `[]` must not collapse into one message; this session has a
// reference strategy, so it must not claim there is none.
check('a configured strategy is not reported as absent', !text.includes('无参考策略'));
check('the decision form is present', text.includes('提交决策'));

// --- 8. the comparison view -------------------------------------------------
console.log('comparison view');
await page.getByText('对比', { exact: true }).click();
await settle(2500);
text = await page.textContent('body');
check('the comparison renders', text.includes('净值曲线') || text.includes('尚无净值数据'));
check('the weekly diff section renders', text.includes('逐周决策差异'));
check('metrics are grouped by subject', text.includes('关键指标'));

// Switching views must not reload the session snapshot.
const requests = [];
page.on('request', request => requests.push(request.url()));
await page.getByText('决策', { exact: true }).click();
await settle(800);
check(
  'switching back does not refetch the session',
  !requests.some(url => url.includes(`/api/sessions/${sessionId}`) && !url.includes('compare')),
);

// --- 9. a weekly decision ---------------------------------------------------
console.log('weekly decision');
const week = text => Number((text.match(/第 (\d+)\/(\d+) 周/) ?? [])[1]);

/**
 * Wait for the week counter to reach `expected`, and return the body text.
 *
 * A fixed sleep is not enough here: the decision round-trips through the
 * engine, and a sleep that expires first leaves the form in its `loading`
 * state, so the next click is swallowed and the run fails for a reason that
 * has nothing to do with what is being tested. Polling on the outcome the
 * test actually cares about removes the race.
 */
async function waitForWeek(expected, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let text = '';
  while (Date.now() < deadline) {
    text = await page.textContent('body');
    if (week(text) === expected) return text;
    await page.waitForTimeout(300);
  }
  throw new Error(`week never reached ${expected}; still at ${week(text)}`);
}

/** Wait for the submit button to stop showing a spinner. */
async function waitIdle() {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    const loading = await page.locator('.ant-btn-loading').count();
    if (loading === 0) return;
    await page.waitForTimeout(200);
  }
}

const before = week(await page.textContent('body'));

// `CODE:PCT` on the buy line; the page converts the percentage to a fraction
// before sending it, which the receipt's fill count confirms reached the engine.
await page.locator('#buy').fill('000001.SZ:20');
await page.getByRole('button', { name: /提交决策/ }).click();
let decided = await waitForWeek(before + 1);
check('a fill receipt is shown', /成交 \d+ 笔|没有成交/.test(decided), decided.slice(0, 200));
check('the week advanced', week(decided) === before + 1, `${before} -> ${week(decided)}`);
await waitIdle();

// An empty submission is a deliberate no-op, so it must ask before advancing.
await page.getByRole('button', { name: /提交决策/ }).click();
await page.locator('.ant-modal-confirm').waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
check('an empty submission asks first', (await page.locator('.ant-modal-confirm').count()) > 0);
// Scoped to the dialog: the page button and the dialog button share a word.
await page.locator('.ant-modal-confirm').getByRole('button', { name: /取\s*消/ }).click();
await page.locator('.ant-modal-confirm').waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {});

await page.getByRole('button', { name: /跳过本周/ }).click();
await page.locator('.ant-modal-confirm').waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
check('skipping asks first', (await page.locator('.ant-modal-confirm').count()) > 0);
await page.locator('.ant-modal-confirm').getByRole('button', { name: /跳\s*过/ }).click();
check('skipping also advances the week', week(await waitForWeek(before + 2)) === before + 2);

// --- 10. the strategy section ----------------------------------------------
console.log('strategy section');
await goto('/strategies');
text = await page.textContent('body');
check('the section is not the placeholder', !text.includes('该分区由后续变更提供'));
check('registered strategies are listed', text.includes('factor_ranking'));
check('the submit form is present', text.includes('发起信号生成'));

// Submitting works end to end: it queues, runs, and lands back in the job
// centre — and its output is asserted here, because it is a property of this
// fixture.
//
// It was not always. Until the `fix-factor-store-injection` change,
// `factor_ranking` built its factors with `factor_registry.get(name)`,
// `FactorRegistry.get` defaulted `store=None`, and every factor class therefore
// opened `DataStore()` — the hardcoded `data/quant.db` rather than the
// `db_path` this config names. The live run read prices from the repository's
// own database while reading the scratch one for its universe, so what came out
// belonged to neither and nothing here could be asserted. The repository
// database holds only three of the ten codes below, so the order list is a
// discriminator: it is a set only the scratch database can produce.
await Promise.all([
  page.waitForURL(/\/jobs\/[0-9a-f-]+/, { timeout: 120000 }),
  page.getByRole('button', { name: /发起信号生成/ }).click().catch(() => {}),
]);
const strategyRunId = page.url().split('/jobs/')[1];
check('submitting navigates to the job centre', Boolean(strategyRunId), page.url());

const strategyRun = await awaitRun(strategyRunId, 180000);
check('the signal run reaches a terminal status', strategyRun.status === 'ok', strategyRun.error ?? '');

const liveResponse = await fetch(`${BASE}/api/strategies/runs/${strategyRunId}`);
check('the live run produced signals', liveResponse.status === 200, `status ${liveResponse.status}`);
const live = liveResponse.status === 200 ? await liveResponse.json() : { orders: [], universe_size: 0 };

check(
  'the live run selects from the scratch universe',
  live.orders.length === SCRATCH_UNIVERSE.length &&
    live.orders.every(order => SCRATCH_UNIVERSE.includes(order.ts_code)),
  `${live.orders.length} orders: ${live.orders.map(order => order.ts_code).join(', ')}`,
);
check(
  'its universe size comes from the scratch index weights',
  live.universe_size === SCRATCH_UNIVERSE.length,
  String(live.universe_size),
);

// The invariant that holds either way, and the one worth pinning: a run
// registers an artifact exactly when it has orders to point at. The service
// reports not-found on the same condition, so the two must agree.
const liveArtifact = (await fetch(`${BASE}/api/runs/${strategyRunId}/artifacts`).then(r => r.json()))[0];
check(
  'artifact registration agrees with whether the run produced orders',
  (liveResponse.status === 200) === (liveArtifact !== undefined),
  `orders=${liveResponse.status === 200} artifact=${liveArtifact?.ref}`,
);

// --- 11. the signal read path ----------------------------------------------
console.log('signal read path');
const seeded = await fetch(`${BASE}/api/strategies/runs`).then(r => r.json());
const seededRun = seeded.items.find(row => row.run_id === 'e2e-strategy-signals');
check('the seeded run is in the history', Boolean(seededRun));
check('its summary is derived from the signal rows', seededRun?.order_count === 6, JSON.stringify(seededRun?.order_count));
check('its universe size comes from the artifact', seededRun?.universe_size === 10, JSON.stringify(seededRun?.universe_size));

await goto('/strategies/signals');
text = await page.textContent('body');
check('the history page lists the run', text.includes('已完成'));
check('the history shows an order count', text.includes('订单数'));
check('the signal date is shown', text.includes('2026-07-24'));

const detail = await fetch(`${BASE}/api/strategies/runs/e2e-strategy-signals`).then(r => r.json());
check('the detail returns the orders in engine order', detail.orders.every((o, i) => o.seq === i + 1));
check('the orders carry a direction and a weight', detail.orders.every(o => o.direction && typeof o.target_pct === 'number'));
check('the detail carries the run status', detail.status === 'ok', JSON.stringify(detail.status));
// The date the engine used. A blank submission resolves to the latest trade
// date, and 2026-07-24 is exactly that in this database.
check('the signal date is the engine\u0027s', detail.signal_date === '2026-07-24', JSON.stringify(detail.signal_date));

await goto('/strategies/signals/e2e-strategy-signals');
text = await page.textContent('body');
check('the detail page is not a 404', !text.includes('不存在'), text.slice(0, 200));
check('the detail page names the strategy', text.includes('factor_ranking'));
check('the detail page shows the engine signal date', text.includes('2026-07-24'));
check('the detail page shows the order count', text.includes('共 6 笔'));
check('the detail page renders the order table', text.includes(detail.orders[0].ts_code));
check('a direction tag is rendered', text.includes(detail.orders[0].direction));
check('a target weight is rendered as a percentage', /\d+\.\d%/.test(text));

await page.reload({ waitUntil: 'networkidle' });
check('refreshing the signal deep link does not 404', !(await page.textContent('body')).includes('页面不存在'));

// --- 12. refresh the deep link ---------------------------------------------
await goto(`/simulator/${sessionId}`);
text = await page.textContent('body');
check('refreshing a session deep link does not 404', !text.includes('页面不存在'));

const status = consoleErrors.slice(expected404);
check('no console errors along the way', status.length === 0, status.slice(0, 3).join(' | '));

await browser.close();
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
