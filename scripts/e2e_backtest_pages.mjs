/**
 * End-to-end check for the backtest research pages (C4).
 *
 * Drives a headless Chromium through the flows a user takes — submit from the
 * form, read the detail page, compare runs, cancel a long one — and asserts the
 * claims in `openspec/changes/add-backtest-research-pages/specs/backtest-research-ui/spec.md`.
 * There is no JS test runner in this repo, so this is the only runtime check
 * the frontend pages get; `tests/test_ui_shell_contract.py` covers the rest.
 *
 * Not part of `pytest` — it needs a running server and a browser.
 *
 * Usage (from the repository root):
 *   npm install --no-save playwright-core    # verification-only, not a project dep
 *   uv run python scripts/seed_backtest_e2e_db.py
 *   mkdir -p .scratch/e2e-backtest
 *   printf 'data:\n  db_path: .scratch/e2e-backtest/quant.db\n' > .scratch/e2e-backtest/config.yaml
 *   QUANT_CONFIG=.scratch/e2e-backtest/config.yaml uv run python -m quant_trade &
 *   node scripts/e2e_backtest_pages.mjs
 *
 * `playwright-core` must resolve from the repository root (node walks up from
 * this file's directory), and a Chromium must be in the ms-playwright cache —
 * the path is in SHELL below, override it for a different revision. The three
 * empty-state assertions only run against a freshly seeded database.
 *
 * What this cannot check: ECharts draws axis values into a canvas, so anything
 * about axis labels or whether a curve is rebased is invisible here. Those are
 * pinned by the source contracts and confirmed by reading a screenshot.
 */
// End-to-end verification of the backtest pages against a real running server.
//
// Drives a headless Chromium through the same flows a user takes: submit from
// the form, read the detail page, compare two runs, cancel a long one. Every
// assertion is a claim from `openspec/changes/add-backtest-research-pages`.
import { chromium } from 'playwright-core';

const BASE = 'http://127.0.0.1:9555';
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
let apiCalls = [];
page.on('console', msg => {
  if (msg.type() === 'error') consoleErrors.push(msg.text());
});
page.on('pageerror', err => consoleErrors.push(`pageerror: ${err.message}`));
page.on('request', request => {
  if (request.url().includes('/api/')) apiCalls.push(`${request.method()} ${request.url().replace(BASE, '')}`);
});

async function goto(path) {
  apiCalls = [];
  await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle' });
}

/** `networkidle` waits for requests, not for ECharts to paint its canvas. */
async function settle(ms = 1500) {
  await page.waitForTimeout(ms);
}

async function awaitRun(runId, timeoutMs = 180000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const body = await fetch(`${BASE}/api/runs/${runId}`).then(r => r.json());
    if (['ok', 'failed', 'cancelled', 'interrupted'].includes(body.status)) return body;
    await new Promise(resolve => setTimeout(resolve, 300));
  }
  throw new Error(`run ${runId} never finished`);
}

async function submitRun(params) {
  const response = await fetch(`${BASE}/api/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: 'backtest', params }),
  });
  if (response.status !== 202) throw new Error(`submit failed: ${response.status}`);
  return (await response.json()).run_id;
}

// --- 1. submit a run from the form -----------------------------------------
console.log('submit from the form');
const preexisting = (await fetch(`${BASE}/api/backtests?limit=1&offset=0`).then(r => r.json())).total;
await goto('/backtest');
let text = await page.textContent('body');
check('page is not the placeholder', !text.includes('该分区由后续变更提供'));
if (preexisting === 0) {
  check('empty state replaces the table', text.includes('尚无回测'));
  check('no empty table headers when there is nothing', !text.includes('净值点'));
} else {
  console.log(`  skip empty-state checks — database already holds ${preexisting} runs`);
}

// Fill the plain number field first: editing the range picker re-commits the
// form, which can replace the number input mid-fill if it goes second.
await page.locator('#top_n').fill('5');
const inputs = page.locator('.ant-picker-input input');
await inputs.nth(0).click();
await inputs.nth(0).fill('2024-06-01');
await inputs.nth(1).fill('2025-06-30');
await page.keyboard.press('Enter');
await page.locator('.ant-picker-dropdown').waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {});
// The click unmounts the form as the app navigates; race it with the redirect.
await Promise.all([
  page.waitForURL(/\/jobs\/[0-9a-f]+/, { timeout: 30000 }),
  page.locator('button[type="submit"]').click().catch(() => {}),
]);
const runA = page.url().split('/jobs/')[1];
check('submit navigates to the job centre', Boolean(runA), page.url());
const recordA = await awaitRun(runA);
check('run finished ok', recordA.status === 'ok', recordA.error ?? '');
check('progress reached 100%', recordA.progress === 1);

const status = await fetch(`${BASE}/api/data/status`).then(r => r.json());
const tables = Array.isArray(status.tables) ? status.tables : Object.values(status.tables ?? {});
for (const name of ['backtest_nav', 'backtest_trade', 'backtest_metric', 'backtest_position']) {
  const stats = tables.find(table => table.table === name);
  check(`${name} is listed with rows`, Boolean(stats) && stats.rows > 0, JSON.stringify(stats));
}

// --- 2. the list now shows the run -----------------------------------------
console.log('list');
await goto('/backtest');
text = await page.textContent('body');
check('run appears in the list', text.includes('已完成'));
check('progress column is present', text.includes('进度'));
check('actual covered window is shown', /2024-06-0\d ~ 2025-06-30/.test(text));

// --- 3. the detail page ----------------------------------------------------
console.log('detail');
await goto(`/backtest/${runA}`);
await settle();
text = await page.textContent('body');
check('status is shown', text.includes('已完成'));
check('completion time is labelled 完成于', text.includes('完成于'));
check('nav chart is labelled', (await page.locator('[aria-label="策略净值与基准净值曲线"]').count()) > 0);
check('metric cards rendered', text.includes('年化收益') && text.includes('Calmar') && text.includes('周胜率'));
check('positions section', text.includes('期末持仓'));
check('trades section', text.includes('交易明细'));
check('charts rendered', (await page.locator('canvas').count()) >= 3);

// --- 4. comparison ---------------------------------------------------------
console.log('compare');
await goto('/backtest/compare');
check('direct open renders nothing computed', !apiCalls.some(call => call.includes('/backtests/compare')), apiCalls.join(' | '));
check('direct open explains what to do', (await page.textContent('body')).includes('点击「对比」'));

const runB = await submitRun({ start: '2024-09-01', end: '2025-09-30', initial_capital: 200000, top_n: 3 });
check('second run finished ok', (await awaitRun(runB)).status === 'ok');

await goto(`/backtest/compare?runs=${runA},${runB}`);
await settle(2000);
text = await page.textContent('body');
check('carrying a selection computes once', text.includes('净值叠加'));
check('overlay chart is labelled', (await page.locator('[aria-label="多个回测的净值叠加曲线"]').count()) > 0);
check('comparison shows covered trading days', text.includes('覆盖交易日'));
check('metrics table rendered', text.includes('指标对照'));
// ECharts draws axis values into a canvas, so "are the curves rebased" is not
// observable from the DOM: it is pinned by
// tests/test_ui_shell_contract.py::TestBacktestSection::test_compare_rebases_...
// and confirmed by reading a screenshot. What is observable here is that the
// chart rendered for two runs funded differently.
check('overlay canvas rendered for differently funded runs', (await page.locator('canvas').count()) > 0);

// --- 5. cancel a long run --------------------------------------------------
console.log('cancel');
const runC = await submitRun({ start: '2024-01-01', end: '2026-12-31' });
await goto(`/jobs/${runC}`);
const cancelButton = page.getByRole('button', { name: '取消' });
await cancelButton.waitFor({ state: 'visible', timeout: 30000 });
await cancelButton.click().catch(() => {});
const recordC = await awaitRun(runC);
check('run ends as cancelled', recordC.status === 'cancelled', recordC.status);
check('progress is not pushed to 100%', recordC.progress < 1, `progress=${recordC.progress}`);

await goto(`/backtest/${runC}`);
await settle(2500);
text = await page.textContent('body');
check('detail marks the run cancelled', text.includes('已取消'));
check('timestamp is labelled 取消于', text.includes('取消于'));
check('detail explains the truncated curve', text.includes('曲线停在取消那一周'));
// Two legitimate outcomes: weeks completed before the cancel (a curve), or the
// cancel beat week one (no curve, and the page says so). What must never
// happen is a blank detail page.
const cancelledDetail = await page.locator('canvas').count();
check(
  'cancelled detail shows its curve or explains that there is none',
  cancelledDetail > 0 || (await page.textContent('body')).includes('没有可展示的净值曲线'),
  `canvases=${cancelledDetail}`,
);

check('no console errors before the deliberate 404', consoleErrors.length === 0, consoleErrors.join(' | '));

// --- 6. routing ------------------------------------------------------------
console.log('routing');
await goto('/backtest/compare');
await page.reload({ waitUntil: 'networkidle' });
check('reloading /backtest/compare renders', (await page.textContent('body')).includes('回测对比'));

await goto('/backtest/does-not-exist');
text = await page.textContent('body');
check('missing run explains itself', text.includes('不存在'));
check('missing run offers a way back', text.includes('返回回测列表'));

// A missing run is a 404 by design; anything else on the console is not.
const unexpected = consoleErrors.filter(line => !line.includes('404'));
check('no unexpected console errors', unexpected.length === 0, unexpected.join(' | '));

console.log(`\nRUN_A=${runA}\nRUN_B=${runB}\nRUN_C=${runC}`);
await browser.close();
console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exitCode = 1;
