## 1. 让股票池有序

- [x] 1.1 `data/store.py`：`get_universe` 的成分股查询补 `ORDER BY ts_code`，注释写明为什么（DISTINCT 不保证行序，而下游的并列截断依赖它）
- [x] 1.2 `data/store.py`：`_universe_from_kline` 同样补 `ORDER BY ts_code`
- [x] 1.3 `data/store.py`：确认 `_exclude_st` 保序（当前实现已是列表推导，保持不变）

## 2. 测试

- [x] 2.1 `tests/test_universe.py`：同一连接重复调用 `get_universe` 两次，断言列表相等（含顺序）
- [x] 2.2 `tests/test_universe.py`：覆盖 kline 回退路径的顺序
- [x] 2.3 `tests/test_universe.py`：`filter_st=True` 时剔除后其余代码相对顺序不变
- [x] 2.4 `tests/`：一例断言因子排名策略在同一输入上重复调用选出同一组合（此前 reversed 输入会变，现在由有序入参保证）

## 3. 校验

- [x] 3.1 `uv run pytest tests/test_universe.py tests/test_simulator_comparison.py tests/test_backtest_result_store.py`
- [x] 3.2 `uv run pytest`（全量——本变更会动并列处的选股结果，可能有断言依赖旧的任意顺序）
- [x] 3.3 `uv run ruff check && uv run ruff format && uv run mypy src`
- [x] 3.4 真机：同一日期连续两次取推荐，组合一致
- [x] 3.5 回填 `fix-simulator-comparison` 的 5.4（一键跟随 → 完全跟随）
- [x] 3.6 回填 `add-simulator-one-click-follow` 的 4.4
