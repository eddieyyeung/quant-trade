## 1. 回撤图接入窗口

- [x] 1.1 `web/src/pages/backtest/Detail.tsx`：抽出 `windowAnchor(window)`，超额图改用它
- [x] 1.2 同文件：`drawdownOption` 首屏带 `windowAnchor({ start: 0, end: 100 })`
- [x] 1.3 同文件：`drawdownTick` / `percentValue` 两个 formatter 提到模块级，回撤图的 option 不再含内联函数
- [x] 1.4 同文件：`drawdownChart` 实例 ref + `onChartReady`，`followWindow` 里补上回撤补丁，`runId` 切换时清空

## 2. 锚点可读

- [x] 2.1 同文件：`followWindow` 把锚定日期写入 `getDom()` 的 `aria-label`

## 3. 验证收尾（前次 verify 的 W1 / S3）

- [x] 3.1 `tests/test_ui_shell_contract.py`：补「首屏与缩放共用 `anchorCurves(series, 0)` 构建器」断言（W1）
- [x] 3.2 `web/src/pages/backtest/anchoring.ts`：`anchorCurves` 注释写明 `dates` 非空且等长的前置条件（S3）

## 4. 契约测试

- [x] 4.1 新增「回撤图跟随同一窗口」：`drawdownChart.current?.setOption(windowAnchor(window)` 与首屏 0-100
- [x] 4.2 新增「回撤 option 不含内联闭包」：函数体内不出现 `formatter: (`
- [x] 4.3 新增「锚点可从 DOM 读出」：`getDom().setAttribute('aria-label'` 与 `锚定 ${curves.day}`

## 5. 校验

- [x] 5.1 `cd web && npm run build` 通过
- [x] 5.2 `cd web && npm run lint` 通过
- [x] 5.3 `uv run pytest tests/test_ui_shell_contract.py` 100 通过
- [x] 5.4 `uv run pytest` 全量通过
- [x] 5.5 `uv run ruff check && uv run ruff format --check && uv run mypy src` 通过
- [x] 5.6 真机：headless Chromium 滚轮缩放后 —— 回撤图窗口与净值图一致、百分比刻度仍为一位小数、翻交易明细分页后窗口与像素不变；`aria-label` 从「策略净值与基准净值曲线」变为「…，锚定 2019-05-27」
- [x] 5.7 `openspec validate fix-drawdown-follow-window --strict` 通过

## 6. 约定落纸

- [x] 6.1 `AGENTS.md`（仓库根，代理入口）：写下 change 目录不带日期前缀（归档时由 CLI 加）、delta 四种操作、Purpose 不被 delta 承载、MODIFIED 不得丢 scenario 四条约定（S2）
