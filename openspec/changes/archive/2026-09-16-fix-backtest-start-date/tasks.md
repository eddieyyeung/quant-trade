## 1. 修复

- [x] 1.1 在 `backtest/engine.py` 的 `run_backtest` 中，于 `weeks_between` 之前计算 `actual_start = calendar.next_trade_date(start) or start`
- [x] 1.2 将 `weeks_between` 与 `trade_dates_between` 的起始参数改为 `actual_start`
- [x] 1.3 在归一化发生时记录 INFO 日志（原起始日 → 实际起始日），便于排查

## 2. 回归测试

- [x] 2.1 在 `tests/test_integration.py` 新增用例：起始日向前回退至最近的周末（保证非交易日）
- [x] 2.2 断言周度调仓日程非空，且 `nav_series` 非空、`metrics["total_return"]` 为有效数值
- [x] 2.3 断言起始日为交易日时结果与归一化前一致（防止过度修正）
- [x] 2.4 断言起始日之后无交易日时返回空结果且不抛异常
- [x] 2.5 参数化用例覆盖法定节假日起始（2025-01-01 元旦、2025-10-01 国庆），从 mock 日历中剔除该日以模拟休市
- [x] 2.6 断言默认配置（`BacktestParams.from_config`，start 取 `config.backtest.start_date`）产出非空指标与净值
- [x] 2.7 断言回测与模拟盘对同一非交易日起始归一化到同一天（模拟盘规则为 `calendar.next_trade_date(start)`）

## 3. 验证

- [x] 3.1 `uv run pytest tests/test_integration.py tests/test_calendar.py -v` 通过
- [x] 3.2 `uv run pytest` 全绿
- [x] 3.3 `uv run ruff check` 与 `uv run mypy src` 通过
- [x] 3.4 端到端验证：默认配置（`start=2015-01-01`）下回测产出非零总收益率与非空净值序列
- [x] 3.5 `openspec validate fix-backtest-start-date --strict` 通过
