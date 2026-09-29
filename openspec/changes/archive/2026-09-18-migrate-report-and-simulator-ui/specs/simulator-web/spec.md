## MODIFIED Requirements

### Requirement: 前端 API 基址约定

前端 SHALL 默认使用相对路径 `/api` 作为 API 基址，并可通过 `VITE_API_BASE` 环境变量覆盖（用于连接部署后的后端）。

基址解析 SHALL 由平台共享的请求模块承担，仿真客户端 SHALL 建立在其之上，SHALL NOT 自带第二套基址解析与错误归一化。仿真客户端 SHALL NOT 依赖已被删除的独立模拟盘前端模块。

#### Scenario: 默认基址

- **WHEN** 未设置 `VITE_API_BASE`
- **THEN** 所有 API 请求发往 `/api` 相对路径，经 Vite 代理到达后端

#### Scenario: 自定义基址

- **WHEN** 构建时设置 `VITE_API_BASE=https://api.example.com`
- **THEN** 所有 API 请求发往该绝对地址

#### Scenario: 仿真客户端复用平台请求模块

- **WHEN** 检查仿真前端发起请求的路径
- **THEN** 基址解析与错误归一化来自平台共享的请求模块，仿真客户端只声明路径与响应类型

## ADDED Requirements

### Requirement: 仿真分区并入平台外壳

仿真前端 SHALL 作为研究平台的一个分区存在，通过侧边栏「仿真」进入 `/simulator`，SHALL NOT 再作为独立应用运行。`/simulator` 入口 SHALL 不再指向占位页。

分区内 SHALL 由列表行进入单个会话的决策台，SHALL NOT 为详情页声明顶部标签——详情页的 `activeKey` 匹配不到任何标签，标签栏会空白。

页面样式 SHALL 使用 antd 组件与主题 token，SHALL NOT 新增手写样式表。图表 SHALL 使用按需注册的共享 ECharts 封装，SHALL NOT 引入其他图表库。

#### Scenario: 分区入口不再是占位页

- **WHEN** 用户点击侧边栏「仿真」
- **THEN** 进入会话创建与列表页面，而非「该分区由后续变更提供」的占位内容

#### Scenario: 刷新不 404

- **WHEN** 用户直接刷新 `/simulator` 或处于某个会话的决策台时刷新
- **THEN** 后端 SPA 回退返回页面外壳，路由正常渲染，请求不打到回退处理之外

#### Scenario: 无手写样式表

- **WHEN** 检查仿真分区新增的文件
- **THEN** 不存在新增的 `.css` / `.less` / `.scss` 文件，页面样式来源于 antd 组件与主题 token

#### Scenario: 图表按需注册

- **WHEN** 检查仿真分区的图表引入路径
- **THEN** 图表均经由共享封装组件使用，未出现直接的全量 ECharts 引入，也未引入其他图表库

#### Scenario: 后端错误可见

- **WHEN** 任一仿真页面的请求返回错误
- **THEN** 页面展示该错误的信息，SHALL NOT 静默吞掉
