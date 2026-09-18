## ADDED Requirements

### Requirement: 数据总览页面

数据总览 SHALL 展示各数据表的行数与日期跨度、股票池规模、以及数据时效（最新交易日）。

#### Scenario: 展示表统计

- **WHEN** 用户打开数据总览
- **THEN** 展示每张表的行数与最早/最晚日期，数据来自 `DataStore.table_stats`

#### Scenario: 数据缺失可见

- **WHEN** 某张表行数为 0
- **THEN** 该表在页面上被标记为无数据，而非显示为 0 行且无提示

### Requirement: 同步任务触发

数据域 SHALL 提供同步任务的提交入口，参数包含是否拉取财务数据、起始与结束日期。

#### Scenario: 提交同步任务

- **WHEN** 用户填表并提交
- **THEN** 调用 `POST /api/runs` 且 `kind` 为 `data_sync`，随后跳转任务中心

#### Scenario: 参数非法时前端拦截

- **WHEN** 用户填写的起始日期晚于结束日期
- **THEN** 表单阻止提交并给出提示

### Requirement: 股票池与交易日历展示

数据域 SHALL 提供股票池规模与覆盖率展示，以及交易日历视图。

#### Scenario: 股票池覆盖率

- **WHEN** 用户查看股票池
- **THEN** 展示股票池总数、已同步日线数据的数量与占比（来自 `universe_coverage`）

#### Scenario: 交易日历

- **WHEN** 用户查看交易日历
- **THEN** 以日历形式区分交易日与非交易日

### Requirement: 大表格分页

数据域的表格在数据量超过一页时 SHALL 使用服务端分页，SHALL NOT 一次性返回全部行。

#### Scenario: 分页请求

- **WHEN** 用户翻到第 2 页
- **THEN** 前端携带分页参数请求，服务端只返回该页数据

#### Scenario: 前端表格虚拟滚动

- **WHEN** 单页行数超过 100
- **THEN** antd `Table` 启用虚拟滚动，滚动流畅
