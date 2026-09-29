## Context

`Detail.tsx` 原来的 `rebase()` 把每条曲线除以**整段序列**的首个非空值，`dataZoom` 只影响横轴可视范围。净值图的缩放因此只回答「这些年怎么走」，不回答用户真正在问的「这段区间里谁涨得多」。

超额图更彻底：它没有 `dataZoom`，永远画全区间。

约束来自图表包装层：`web/src/charts/EChart.tsx` 固定 `notMerge: true`，而 `echarts-for-react` 只在 option **深比较不等**时才重发。这两条决定了整条实现路线。

## Goals / Non-Goals

**Goals:**

- 缩放后两条净值线在窗口左端读 1、超额读 0，纵轴写清锚定日期
- 超额曲线与净值曲线永远同锚点、同窗口，不可能互相矛盾
- 全区间（`start = 0`）与旧实现逐位一致

**Non-Goals:**

- 不动 `Compare.tsx` 与仿真盘对比视图的归一
- 不动指标卡口径（仍是全区间）
- 不改后端与落库口径

## Decisions

### 决策一：锚点在时间轴上求，不在索引上求

`start`/`end` 是**轴 extent 的百分比**；time 轴的 extent 是一对时间戳（ECharts 由原始数据算出：`AxisProxy` 走 `scaleRawExtentInfo.makeNoZoom()`），所以映射线性于时间：

```
t     = tFirst + (start / 100) * (tLast - tFirst)
index = 首个 t_i >= t 的点
```

**排除的方案**：`round(start / 100 * (length - 1))`。日线序列里周末与长假让索引与时间非线性，11 年下来两者能差出数周，窗口一开在长假后立刻可见。

**实测核对**（echarts 6.1.0 SSR）：`dispatchAction({type:'dataZoom', start: 51.2155})` 后轴窗口起点与上式算出的时间戳一致（差值 = 时区常偏移，两端与每个数据点同样偏移，选出的索引不变）。

### 决策二：补丁合并到实例上，不走 React 状态

`dataZoom` 事件只由 `dispatchAction` 产生，`setOption` 不会产生它 —— 所以命令式补丁不会自激。

**选择的方案**：`onEvents.dataZoom` 里算出锚点，对两个实例各做一次 `setOption(patch, { notMerge: false })`，`patch` 只含 `yAxis.name` 与 `series`。

**排除的方案**：把缩放窗口放进 React state，并把 `dataZoom.start/end` 写回 option。它会：每次拖动都带 `notMerge` 重发整个 option（正在被拖的滑条组件被重建，滑条与拖拽状态打架）；state 是用户正在连续改动的值的滞后副本，合并更新会让窗口倒退；而且锚点没有任何 React 渲染结果依赖它。

**代价**：option 里**不能出现函数值**。包装层用深比较决定是否重发，一个 `formatter` 就是每次渲染的新引用，会让无关重渲染（翻交易明细分页）都触发 `notMerge` 重发，抹掉缩放窗口。已在代码与契约测试里双向钉住。

### 决策三：超额图的窗口由净值图镜像驱动

超额图加一个 `{ type: 'inside', disabled: true }` 的 dataZoom：自己不接受交互，窗口由净值图回报的百分比写入（两张图共用同一个 `dates` 数组，同一百分比即同一窗口，无需二次推导 extent）。

**排除的方案**：给超额图 `xAxis.min/max`。可行，但固定 min/max 会关掉轴的「nice」取整，全区间渲染与今天会有一丝差别，违背「全区间逐位一致」。

**排除的方案**：超额图只换锚点不换窗口。那会画出一条以中段为基准、却铺在锚点之前日期上的曲线，正是本次要消掉的那种混合口径。

**实测核对**：`disabled: true` 只关漫游（`roams.js` 的 `dataZoomModel.get('disabled')` 判断），窗口照常经 dataZoom processor 生效；合并补丁写入百分比确实能移动窗口；不带 dataZoom 的 `series`+`yAxis` 合并补丁不会动窗口。

## Risks / Trade-offs

- **深比较陷阱**：日后谁在净值/超额 option 里加 `formatter`，缩放窗口会在每次无关重渲染时被悄悄抹掉。契约测试 + 就地注释双重拦截。
- **extent 前提**：锚点公式假定 x 轴没有显式 `min`/`max`、且只有一个 x 轴。加轴范围会改变百分比语义 —— 已在 `anchorIndexAt` 注释写明。
- **升序前提**：二分要求 `dates` 升序；不在此处排序，排序会让并列的数值数组错位。
- **缺口**：某条曲线在锚点处为空值时，该序列的 1.0 落在其后首个有效点；超额在缺口处留空（画成断口）而不是 0 —— 这是诚实的画法，不是 0。
- **极窄视口**：轴名从「期初 = 1」变成日期后变长，可能与图例拥挤（真机顺带看一眼）。

## Open Questions

- 无。若日后要把同样的锚定规则推广到 `Compare.tsx`（多运行叠加图），本模块可直接复用，`Compare.tsx` 当前的期初归一是独立实现。
