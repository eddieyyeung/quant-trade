## Context

前一个 change（`2026-09-29-fix-nav-zoom-rebase`）把「窗口」变成了详情页的一等概念：`followWindow` 从净值图的 `dataZoom` 事件算出锚点，合并补丁到实例上。超额图接进了这条通路，回撤图没有。

那次 verify 同时确认了一件容易被忽略的事：这套做法依赖 option 的**深比较相等**——包装层只在深比较不等时才 `setOption`，且硬带 `notMerge`。回撤图的 option 里有内联 `formatter`，天然每次渲染都不等，所以它**每个渲染周期都会被重发一次**。给它加窗口而不先拆掉这个内联函数，窗口会在下一次无关点击时被抹掉。

## Goals / Non-Goals

**Goals:**

- 三张曲线图共用一个窗口，切换缩放时读到的是一段时间
- 回撤图不可交互缩放（用户只操作净值图），行为与超额图一致
- 全区间时三张图的渲染与今天逐位一致

**Non-Goals:**

- 不给回撤图独立的缩放能力
- 不动指标卡口径、不动后端

## Decisions

### 决策一：窗口抽出成 `windowAnchor`，三张图复用

`excessAnchor` 里的 dataZoom 段抽成 `windowAnchor(window)`，回撤图与超额图都用它；净值图用它作为首屏（0-100）的声明、但**不**在补丁里带窗口。

**排除的方案**：给回撤图 `xAxis.min/max`。可行但会关掉轴的 nice 取整，全区间呈现与今天会有一丝差别（前一个 change 已因此否决过同一方案）。

### 决策二：回撤图的 formatter 提到模块级

`drawdownTick`（轴百分比，一位小数）与 `percentValue`（tooltip）移到模块级，成为跨渲染稳定的引用，深比较这才成立。

**代价**：这两个常量成了「这张图的 option 不允许再出现内联函数」的隐式契约。契约测试钉住它。

**排除的方案**：给图表包装层加 `shouldSetOption` 白名单跳过回撤图。那会让「不该重发」变成包装层的知识，而它凭深比较本来就能判断——根因是有内联函数，拆掉它才是对的。

### 决策三：锚点写入 DOM 的 `aria-label`

轴名画在 canvas 里，e2e 与读屏都读不到。`followWindow` 里把 `锚定 <日期>` 写到图表的容器节点上（`getDom()` 就是 React 渲染的那个节点），使「窗口锚在哪天」可被断言。

**排除的方案**：在页面上多渲染一行文案显示锚定日期。用户已经在轴上看到了，加一行只是重复；`aria-label` 顺带解决读屏。

## Risks / Trade-offs

- **React 会覆盖属性**：`aria-label` 同时是 `EChart` 的 prop。React 只在 prop 变化时改写 DOM 属性，所以命令式写入的值会保持到下次 prop 变化（如换 run）。这是可接受的：换 run 时本就该重置。
- **回撤图仍会被重发**：`windowAnchor` 里没有函数，`percentValue`/`drawdownTick` 是模块级，深比较成立——但这条不变量是隐式的，靠契约测试守着。
- **三张图三个实例**：补丁次数从 2 涨到 3，代价可忽略（每次事件 O(n) 扩容 + 一次合并 setOption）。

## Open Questions

- 无。
