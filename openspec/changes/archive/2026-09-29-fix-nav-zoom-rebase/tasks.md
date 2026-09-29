## 1. 锚点纯函数

- [x] 1.1 `web/src/pages/backtest/anchoring.ts`：新增 `anchorIndexAt(dates, startPercent)` —— 按时间插值 + 二分求窗口首点，`[0,100]` 钳制，注释写明 extent 前提与升序前提
- [x] 1.2 同文件：新增 `anchorCurves(series, startPercent)` —— 返回 `{index, day, nav, benchmark, excess}`，除数取锚点起首个有效值（缺口跳过），超额是两条已归一数组之差
- [x] 1.3 `startPercent = 0` 时与旧 `rebase()` 逐位一致（首个非空非零值为除数）

## 2. 图表包装层

- [x] 2.1 `web/src/charts/EChart.tsx`：`Props` 补 `onEvents` / `onChartReady` 类型，re-export `EChartsInstance`；运行时不动（`{...rest}` 本就透传，`notMerge` 契约不变）

## 3. 详情页接线

- [x] 3.1 `web/src/pages/backtest/Detail.tsx`：`levelAnchor(series, curves)` 与 `excessAnchor(series, curves, window)` 两个补丁构建器，首屏与每次缩放共用同一份定义，series 带显式 `id`
- [x] 3.2 同文件：`levelOption` / `excessOption` 在 `startPercent = 0` 上调用构建器，铺静态外壳；净值图 option 里不写缩放窗口
- [x] 3.3 同文件：`zoomWindow(param)` 同时接住顶层 `{start,end}`（滑条拖拽）与 `{batch:[...]}`（inside 滚轮/拖拽）两种载荷
- [x] 3.4 同文件：`followWindow` 在 `dataZoom` 事件里算锚点，对两个实例各做一次 `setOption(patch, { notMerge: false })`；`onEvents` 用 `useMemo` 保持稳定引用
- [x] 3.5 同文件：超额图补 `{ type: 'inside', disabled: true }`，窗口由净值图镜像写入
- [x] 3.6 同文件：删除旧 `rebase()`，其职责并入 `anchorCurves`
- [x] 3.7 同文件：`runId` 切换的 effect 里清空两个实例 ref

## 4. 契约测试

- [x] 4.1 `tests/test_ui_shell_contract.py`：`test_detail_rebases_its_curves` 改为锚点式
- [x] 4.2 新增「按可视窗口而非期初锚定」：钉时间插值那行与 `anchorCurves(series, window.start)`
- [x] 4.3 新增「两图共用一次推导」：超额必须是已画出的两条数组之差
- [x] 4.4 新增「缩放不经 React 状态」：`notMerge: false`、`onEvents`、净值 option 内不写窗口
- [x] 4.5 新增「锚点构建器不含闭包」：两个构建器函数体内不出现 `formatter`
- [x] 4.6 新增「超额图窗口跟随净值图」：`disabled: true` 与超额实例的 `setOption`

## 5. 校验

- [x] 5.1 echarts SSR 探针核对：百分比线性于时间、事件载荷两种形状、`series`+`yAxis` 合并补丁不动窗口、`disabled` 只关交互（脚本临时，验完即弃）
- [x] 5.2 `cd web && npm run build` 通过
- [x] 5.3 `cd web && npm run lint` 通过
- [x] 5.4 `uv run pytest tests/test_ui_shell_contract.py` 97 通过
- [x] 5.5 `uv run pytest` 全量 863 通过
- [x] 5.6 `uv run ruff check && uv run ruff format --check && uv run mypy src` 通过
- [x] 5.7 真机：headless Chromium 打开 run `97c9013c604a4e4f8a5471ca6b341464`，滚轮缩放 + 拖拽平移后 —— 轴名写出窗口首日（`净值（2019-05-27 = 1）`、`超额（相对 2020-02-10）`）、两线左端读 1、超额读 0、无 console 报错；缩放后翻交易明细分页窗口不丢
- [x] 5.8 `openspec validate 2026-09-29-fix-nav-zoom-rebase --strict` 通过

## 6. 验证收尾

- [x] 6.1 `openspec archive` 把 delta 应用到主 spec：`回测详情页面` 需求的归一规则、超额锚点、轴标签均已同步（+0 / ~1 / -0）
- [x] 6.2 主 spec 无残留冲突描述：`Compare.tsx` 的期初归一是另一条需求（`:166`）且本次未改；分区 Purpose 里「按期初归一」手改为「按锚定日归一」（delta 不承载 Purpose）
- [x] 6.3 归档器实测（OpenSpec 1.6.0）：MODIFIED 里**改名** scenario 会被拒（`contains scenario(s) not present in the modified block`）。改为保留 `曲线按期初归一` 原名、追加 `缩放后按可视区间起点归一` —— 前者在全区间下依然成立，不是权宜之计
