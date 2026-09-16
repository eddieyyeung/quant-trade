## 1. API 层日志 (api.py)

- [x] 1.1 添加 `from loguru import logger` 导入
- [x] 1.2 在 `create_session` 入口添加 INFO 日志，记录请求参数（name, start_date, end_date, capital, ref）
- [x] 1.3 在 `create_session` 成功返回前添加 INFO 日志，记录 session_id、total_weeks、耗时（ms）
- [x] 1.4 在 `create_session` 异常时添加 ERROR 日志，记录异常信息和请求参数

## 2. 引擎层日志 (engine.py)

- [x] 2.1 在 `Simulator.create()` 入口添加 INFO 日志，记录会话名和日期范围
- [x] 2.2 日历构建完成后添加 INFO 日志，记录周数和首个周五日期
- [x] 2.3 参考策略加载成功后添加 INFO 日志，记录策略名
- [x] 2.4 快照构建开始前添加 INFO 日志

## 3. 持久层日志 (session.py)

- [x] 3.1 在 `SessionStore.create()` 目录创建后添加 DEBUG 日志
- [x] 3.2 在 decisions.json 写入后添加 DEBUG 日志
- [x] 3.3 在 DB 行插入后添加 DEBUG 日志

## 4. 快照构建日志 (snapshot.py)

- [x] 4.1 在 `build_snapshot()` 入口添加 DEBUG 日志，记录 cursor_date 和 universe 大小
- [x] 4.2 在每个子模块（`_build_market_overview`, `_build_portfolio_snapshot`, `_build_factor_ranking`, `_build_strategy_signals`）开始执行时添加 DEBUG 日志
- [x] 4.3 在 `_build_factor_ranking` 完成后添加 DEBUG 日志，记录因子数量和排名条目数

## 5. 验证

- [x] 5.1 运行现有测试确认无回归：`uv run pytest tests/test_simulator_session.py tests/test_simulator_engine.py tests/test_simulator_snapshot.py -v`
- [x] 5.2 手动启动服务并创建会话，确认日志输出完整且可读
