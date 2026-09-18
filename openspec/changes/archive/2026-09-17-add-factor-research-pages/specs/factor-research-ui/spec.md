## ADDED Requirements

### Requirement: 因子分区导航

因子分区的四个页面 SHALL 通过 `/factors` 路由可达，分区内切换 SHALL 使用与数据分区一致的顶部标签（antd `Tabs`）形式，`/factors` 侧边栏入口 SHALL 不再指向占位页。

#### Scenario: 分区入口不再是占位页

- **WHEN** 用户点击侧边栏「因子」
- **THEN** 进入因子库页面，而非「该分区由后续变更提供」的占位内容

#### Scenario: 分区内切换

- **WHEN** 用户在 `/factors` 与 `/factors/ic` 之间切换
- **THEN** 顶部标签高亮跟随当前路径，刷新页面后仍在同一页面

#### Scenario: 刷新不 404

- **WHEN** 用户直接刷新 `/factors/quantile`
- **THEN** 后端 SPA 回退返回页面外壳，路由正常渲染

### Requirement: 因子库页面

因子库页面 SHALL 展示因子名、分类与持久化覆盖度，并支持按分类筛选。列表 SHALL 使用服务端分页，SHALL NOT 一次返回全部因子行。

页面 SHALL 为每个因子提供勾选，勾选结果 SHALL 保存在浏览器本地存储，作为 IC 分析、分层回测与相关性三页因子选择器的默认值。

#### Scenario: 展示因子与覆盖度

- **WHEN** 用户打开因子库页面
- **THEN** 每行展示因子名、分类，以及持久化的日期范围与记录数；未持久化的因子标注为无数据

#### Scenario: 按分类筛选

- **WHEN** 用户选择分类「动量」
- **THEN** 列表只剩该分类的因子

#### Scenario: 手工因子标注未落库

- **WHEN** 列表包含未写入 `factor_values` 的手工因子
- **THEN** 该行标注为未落库，不显示为「0 条记录」以外的虚假覆盖度

#### Scenario: 分页与虚拟滚动

- **WHEN** 因子总数超过一页
- **THEN** 列表分页请求服务端，且单页超过 100 行时表格启用虚拟滚动

#### Scenario: 勾选被其他页面复用

- **WHEN** 用户在因子库勾选两个因子后进入 IC 分析页面
- **THEN** 因子选择器默认已选中这两个因子

### Requirement: IC 分析页面

IC 分析页面 SHALL 展示所选因子在给定区间与持有期下的 RankIC 序列曲线、IC_IR、IC 胜率与因子衰减图。数据 SHALL 来自 `ic_series` 表的读取接口。

曲线 SHALL 含零轴参考线。衰减图 SHALL 以柱状图展示同一因子在不同持有期下的 IC 均值。

#### Scenario: 展示 IC 序列

- **WHEN** 用户选择一个已计算过 IC 的因子与持有期
- **THEN** 页面渲染按日期排列的 RankIC 折线，并叠加零轴参考线

#### Scenario: 展示汇总指标

- **WHEN** IC 序列加载完成
- **THEN** 页面同时展示 IC 均值、IC 标准差、IC_IR 与 IC 正值占比

#### Scenario: 展示因子衰减

- **WHEN** 该因子存在多个持有期的 IC 记录
- **THEN** 页面以柱状图展示各持有期的 IC 均值对比

#### Scenario: 因子尚无 IC 记录

- **WHEN** 所选因子在 `ic_series` 中无记录
- **THEN** 页面给出「尚无 IC 数据」的空状态与前往计算任务的入口，SHALL NOT 渲染空图表

### Requirement: 分层回测页面

分层回测页面 SHALL 提供因子、日期区间、分组数与调仓频率的输入，提交后展示分组净值多线图与多空组合净值图。

页面 SHALL 明确标注该结果为因子区分度的统计视图，非可交易组合收益。

#### Scenario: 展示分组净值

- **WHEN** 用户提交一次 5 组分层回测
- **THEN** 页面渲染 5 条分组净值曲线与 1 条多空组合净值曲线

#### Scenario: 统计视图标注

- **WHEN** 分层回测结果展示
- **THEN** 页面上可见说明文字，指出结果不含交易成本与交易约束，不代表可交易收益

#### Scenario: 参数非法被拦截

- **WHEN** 用户输入的起始日期晚于结束日期
- **THEN** 前端阻止提交并给出提示

#### Scenario: 区间无数据

- **WHEN** 所选因子在所选区间没有可用的因子值
- **THEN** 页面展示空状态说明，SHALL NOT 渲染空图表

### Requirement: 相关性页面

相关性页面 SHALL 允许选择一组因子后计算并展示相关性热力图。计算 SHALL 在用户显式触发后执行，SHALL NOT 在页面加载时自动计算。

因子选择数量超过上限时 SHALL 在提交前提示。

#### Scenario: 展示相关矩阵

- **WHEN** 用户选择 5 个因子并点击计算
- **THEN** 页面渲染 5×5 热力图，轴标签为因子名，并展示参与计算的有效日期数

#### Scenario: 不自动计算

- **WHEN** 用户打开相关性页面
- **THEN** 页面不发起相关矩阵计算请求，等待用户选择因子并显式触发

#### Scenario: 超过因子上限

- **WHEN** 用户选择的因子数超过上限
- **THEN** 页面在提交前提示超出上限，不发起请求

### Requirement: 因子计算任务提交

因子库页面 SHALL 提供 Alpha158 因子计算的提交入口，提交后跳转任务中心查看进度与日志。客户端 SHALL 通过统一的运行接口提交，SHALL NOT 为因子计算新增专用写接口。

#### Scenario: 提交因子计算

- **WHEN** 用户提交一次 Alpha158 因子计算
- **THEN** 请求以 `kind: factor_compute` 提交到统一运行接口，页面跳转到该运行的任务详情

#### Scenario: 进度与日志可见

- **WHEN** 因子计算任务运行期间用户停留在任务详情页
- **THEN** 进度按因子计算阶段推进，日志实时追加

### Requirement: 图表依赖与实现约定

因子分区页面的图表 SHALL 使用 ECharts，通过按需注册的共享封装引入，SHALL NOT 引入全量 ECharts 包，SHALL NOT 引入其他图表库。页面样式 SHALL 使用 antd 组件与主题 token，SHALL NOT 新增手写样式表。

#### Scenario: 图表按需注册

- **WHEN** 检查因子分区页面的图表引入路径
- **THEN** 图表均经由共享封装组件使用，未出现直接的全量 ECharts 引入

#### Scenario: 无手写样式表

- **WHEN** 检查因子分区新增的文件
- **THEN** 不存在新增的 `.css` / `.less` / `.scss` 文件，页面样式来源于 antd 组件与主题 token

#### Scenario: 后端错误可见

- **WHEN** 任一因子页面的请求返回错误
- **THEN** 页面展示该错误的信息，SHALL NOT 静默吞掉
