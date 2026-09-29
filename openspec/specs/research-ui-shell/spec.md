# research-ui-shell Specification

## Purpose
研究平台的前端外壳：基于 Ant Design 的 `Layout` / `Sider` / `Menu` 提供数据、因子、模型、策略、回测、仿真、报告、任务中心的分区导航与路由（未开发分区给出占位说明，刷新不 404），并声明 antd 与 ECharts 依赖；任务中心页面展示运行列表与 SSE 实时日志，并支持取消运行。
## Requirements
### Requirement: 平台外壳

前端 SHALL 使用 Ant Design 的 `Layout` / `Sider` / `Menu` 构建平台外壳，提供数据 / 因子 / 模型 / 策略 / 回测 / 仿真 / 报告 / 任务中心的导航入口。

#### Scenario: 导航可达

- **WHEN** 用户打开平台首页
- **THEN** 侧边栏展示全部分区入口，当前分区高亮

#### Scenario: 未开发分区给出说明

- **WHEN** 用户点击一个尚未实现的分区（如因子）
- **THEN** 页面显示该分区由后续变更提供，SHALL NOT 显示空白页或报错

#### Scenario: 路由刷新不 404

- **WHEN** 用户直接访问 `/jobs` 并刷新浏览器
- **THEN** 页面正常渲染（由后端 SPA 回退支持）

### Requirement: 组件库与图表依赖

前端 SHALL 使用 Ant Design 作为组件库，ECharts 作为图表库。依赖 SHALL 声明在 `web/package.json` 中。

#### Scenario: 依赖声明位置正确

- **WHEN** 检查 `web/package.json`
- **THEN** 包含 `antd` / `@ant-design/icons` / `echarts` / `echarts-for-react` / `react-router-dom`

#### Scenario: 根 package.json 不含前端运行时依赖

- **WHEN** 检查仓库根 `package.json`
- **THEN** 不包含 `react-router-dom` 或 `recharts`

### Requirement: 任务中心页面

任务中心 SHALL 展示运行列表，并支持查看单个运行的实时日志。

#### Scenario: 运行列表展示状态

- **WHEN** 用户打开任务中心
- **THEN** 列表按时间倒序展示运行，每行含 `kind` / 状态标签 / 进度 / 开始时间

#### Scenario: 实时日志

- **WHEN** 用户展开一个 `running` 运行
- **THEN** 日志面板通过 SSE 实时追加新行

#### Scenario: 取消运行

- **WHEN** 用户对一个 `running` 运行点击取消
- **THEN** 前端调用取消接口，该运行的进度停止推进并最终显示为已取消
