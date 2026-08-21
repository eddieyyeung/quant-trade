## 1. 模块搭建

- [x] 1.1 创建 `src/quant_trade/simulator/` 目录和 `__init__.py`
- [x] 1.2 在 `pyproject.toml` 新增 `[project.optional-dependencies]` 中 `web` extra（FastAPI + uvicorn）
- [x] 1.3 在 `data/schema.py` 新增 `simulator_session` 表 DDL
- [x] 1.4 定义核心数据类型：`SimSession`、`Decision`、`Snapshot`、`StepResult`、`ComparisonResult` dataclass（`types.py`）

## 2. 会话持久化

- [x] 2.1 实现 `session.py`：`SessionStore` 类（DuckDB CRUD + JSON 文件读写）
- [x] 2.2 `create_session()` — 插入 DuckDB 行 + 创建 `data/simulator/<id>/` 目录
- [x] 2.3 `save_session()` — 更新 DuckDB 行（该 ID 已存在做 update） + `portfolio_json` 序列化
- [x] 2.4 `load_session()` — 从 DuckDB 行 + JSON 文件重建 `Portfolio`
- [x] 2.5 `list_sessions()` — 查询全部/按状态筛选
- [x] 2.6 `delete_session()` — 删 DuckDB 行 + 删目录
- [x] 2.7 `append_decision()` — 追加单条到 `data/simulator/<id>/decisions.json`
- [x] 2.8 `load_decisions()` — 读取全部 decision 历史

## 3. 时点快照

- [x] 3.1 实现 `snapshot.py`：`SnapshotBuilder` 类，消费 `DataStore` 和 `Portfolio`
- [x] 3.2 `build_market_overview()` — 沪深 300 收盘价 + 周涨跌，as_of 约束
- [x] 3.3 `build_portfolio_snapshot()` — 逐项持仓市值/盈亏/权重，现金占比
- [x] 3.4 `build_factor_ranking()` — 计算全市场因子得分 → Top-30 排名表；缺失数据标注
- [x] 3.5 `build_strategy_signals()` — 调用参考策略 `generate_signals()`，产出推荐买入/清仓列表
- [x] 3.6 `build_snapshot()` — 聚合以上全部，返回 `Snapshot` 对象

## 4. 模拟器引擎

- [x] 4.1 实现 `engine.py`：`Simulator` 类，调用 `SessionStore`、`SnapshotBuilder`、`Portfolio`
- [x] 4.2 `create()` — 创建会话，初始化 `Portfolio`，设置 cursor 为 start 后第一个周五
- [x] 4.3 `resume()` — 加载 session → 返回快照供用户查看
- [x] 4.4 `step()` — 核心方法：校验 orders → 卖单先行（T+1/涨跌停/停牌检测）→ 买单执行（30% cap）→ 记录 Decision → 前进 cursor → 标记 NAV → 保存
- [x] 4.5 `skip()` — 不调仓，前进 cursor → 标记 NAV → 记录空 Decision → 保存
- [x] 4.6 `status()` — 返回当前 session 状态摘要
- [x] 4.7 `snapshot()` — 生成当前 cursor 的快照
- [x] 4.8 复用 `backtest/rules.py`（`get_price_limits`、`detect_suspended`、`is_limit_up/down`）
- [x] 4.9 复用 `backtest/portfolio.py`（`Portfolio.buy/sell/update_prices`），扩展 `total_return` 暴露

## 5. 多线对比

- [x] 5.1 实现 `comparison.py`：`ComparisonEngine` 类
- [x] 5.2 `run_strategy_backtest()` — 对当前 session 周期调用 `backtest/engine.py:run_backtest()` 获取策略 NAV
- [x] 5.3 `compute_benchmark_nav()` — 同期沪深 300 归一化序列
- [x] 5.4 `compute_metrics_triple()` — 手动/策略/基准三方指标表
- [x] 5.5 `build_weekly_diff()` — 逐周对比用户持仓 vs 策略推荐，算 overlap/user_only/strategy_only
- [x] 5.6 `annotate_extremes()` — 标注单股 >30%、周回撤 >10%
- [x] 5.7 `compare()` — 组装完整 `ComparisonResult`
- [x] 5.8 `export_html()` — 复用 `signals/reporter.py` 的 matplotlib + Jinja2 渲染

## 6. CLI 集成

- [x] 6.1 在 `cli.py` 新增 `sim` 子命令组（Typer group）
- [x] 6.2 `quant-trade sim start` — `--name --start --end --capital --ref`
- [x] 6.3 `quant-trade sim resume <id>`
- [x] 6.4 `quant-trade sim step <id>` — 支持 `--buy "code:pct" --sell "code"`
- [x] 6.5 `quant-trade sim skip <id>`
- [x] 6.6 `quant-trade sim status <id>` — rich 格式化输出
- [x] 6.7 `quant-trade sim compare <id>` — 加 `--html` flag 导出

## 7. 测试

- [x] 7.1 新增 `tests/test_simulator_session.py` — 测试 DuckDB CRUD + JSON 读写
- [x] 7.2 新增 `tests/test_simulator_snapshot.py` — 测试快照各组件 + 无未来信息断言
- [x] 7.3 新增 `tests/test_simulator_engine.py` — 测试 create/resume/step/skip 完整流程
- [x] 7.4 新增 `tests/test_simulator_comparison.py` — 测试三方对比 + weekly diff
- [ ] 7.5 新增 `tests/test_simulator_cli.py` — 测试 CLI 子命令（后续补充）

## 8. Phase 2 — Web 前后端分离

- [x] 8.1 实现 `simulator/api.py` — FastAPI JSON API（CORS + CRUD + step/skip/compare）
- [x] 8.2 实现 `web/` — React + TypeScript + Vite 前端（独立目录，组件化）
- [x] 8.3 `quant-trade sim web` — 启动 uvicorn 的 CLI 命令
