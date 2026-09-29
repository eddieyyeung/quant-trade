## Why

`2026-09-29-fix-nav-zoom-rebase` 让净值曲线与超额曲线跟着缩放窗口走，但同屏第三张图——回撤面积图——没跟上：用户在 2021-2023 上缩放，上面两张图换成 2021-2023，下面这张仍从 2015 铺到 2026。三张图同屏两种口径，最容易被读成「回撤也是这个窗口的」。这是那次 verify 留下的 S1。

修它之前有个必须先拆的雷：回撤图的 option 里带着内联 `formatter`（`axisLabel` 与 tooltip 的 `valueFormatter`），每次渲染都是新引用，图表包装层的深比较因此**每次渲染都不等**，会带 `notMerge` 重发整个 option。给这样一张图加上窗口，等于每次翻页、每次无关重渲染都把窗口抹回 0-100。

## What Changes

- 回撤面积图 SHALL 与净值、超额曲线共用同一可视区间，其窗口由净值曲线的缩放驱动。
- 回撤图的 option SHALL NOT 携带内联函数值（`formatter` 等）：改为模块级常量，使深比较成立、窗口不被重发抹掉。
- 净值曲线的锚定日期 SHALL 可从页面 DOM 读出（写入图表容器的 `aria-label`），因为轴名画在 canvas 里，测试与读屏软件都够不着。

**BREAKING**: 无。回撤曲线的数值口径不变，只是可视区间跟随缩放；全区间时三张图与今天一致。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `backtest-research-ui`: 回测详情页的回撤面积图从「永远全区间」改为「跟随净值曲线的缩放窗口」。

## Impact

- `web/src/pages/backtest/Detail.tsx` — `windowAnchor` 抽出并被净值图以外的两张图复用；`drawdownOption` 的两个 formatter 提到模块级；`followWindow` 增加回撤补丁与 `aria-label` 写入
- `tests/test_ui_shell_contract.py` — 新增回撤跟随、回撤 option 不含内联闭包、锚点可从 DOM 读出三条契约；补上「首屏与缩放共用构建器」的断言
- `web/src/pages/backtest/anchoring.ts` — 注释写明 `anchorCurves` 对空序列的前置条件
- `openspec/AGENTS.md` — 记录 OpenSpec 的目录命名与归档约定（此前只存在于 22 个归档目录的形状里）
- 不改动：后端、落库口径、`Compare.tsx`、仿真盘对比视图
