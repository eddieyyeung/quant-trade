## 1. 修 `_to_date` 类型泄漏

- [x] 1.1 `comparison.py`：删掉本地 `_to_date` 定义，改从 `quant_trade.data.calendar` 导入（与 `TradeCalendar` 同一行），7 个调用点不动
- [x] 1.2 `comparison.py`：确认 `compare()` 传给 `_run_strategy_shadow` / `_build_manual_nav` / `_compute_benchmark` 的 start / end 都是 `datetime.date`
- [x] 1.3 `tests/test_simulator_comparison.py`：一例断言传 `pd.Timestamp` 得到 `datetime.date`；一例断言参考策略存在时 `nav_strategy` 非空

## 2. 回测失败不再静默

- [x] 2.1 `types.py`：`ComparisonResult` 增加可空的失败原因字段
- [x] 2.2 `comparison.py`：`_run_strategy_shadow` 失败时把异常信息带出来，替换掉 `except Exception` 里只写日志的降级
- [x] 2.3 `api.py`：`compare` 端点序列化该字段
- [x] 2.4 `tests/test_simulator_comparison.py`：一例让策略抛异常，断言手动盘与基准仍在、失败原因非空、策略序列为无值

## 3. 差异表换成决策偏离口径

- [x] 3.1 `types.py`：`WeeklyDiff` 改为承载偏离对象（可空）——完全跟随、被剔除、被额外加入；移除 `user_holds` / `strategy_holds` / `user_only` / `strategy_only` / `common` / `overlap_count` / `total_user` / `total_strategy`
- [x] 3.2 `comparison.py`：新增按目标组合求偏离的纯函数（买入且目标仓位 > 0 的代码集合之差的三个结果）
- [x] 3.3 `comparison.py`：`_build_weekly_diffs` 改用它，数据来自 `Decision.user_orders` 与 `Decision.strategy_orders`；`strategy_orders` 为 `None` 时偏离对象为 `None`
- [x] 3.4 `comparison.py`：`_run_strategy_shadow` 不再返回第二个值；删掉恒空的 `strategy_holds_by_week` 一路传参
- [x] 3.5 `api.py`：`compare` 端点按新结构序列化 `weekly_diffs`，维持 `null` 与「空列表」的区分
- [x] 3.6 `tests/test_simulator_comparison.py`：完全跟随 / 部分采纳 / 无策略三种周次各一例；一例断言卖出不入选目标组合

## 4. 前端

- [x] 4.1 `web/src/api/simulator.ts`：`WeeklyDiff` 换成新结构；`ComparisonResult` 补失败原因字段
- [x] 4.2 `ComparisonView.tsx`：差异表改列为「你剔除 / 你额外加」，完全跟随有标记，无推荐的行显示「无参考策略」
- [x] 4.3 `ComparisonView.tsx`：表头或卡片说明注明比较的是目标组合
- [x] 4.4 `ComparisonView.tsx`：结果带失败原因时展示 Alert，不静默少一条曲线

## 5. 校验

- [x] 5.1 `uv run pytest tests/test_simulator_comparison.py tests/test_simulator_api.py tests/test_simulator_engine.py`
- [x] 5.2 `uv run ruff check && uv run ruff format && uv run mypy src`
- [x] 5.3 `cd web && npm run build && npm run lint`
- [x] 5.4 真机：有参考策略的会话，一键执行一周后进对比页，策略净值线出现、该周显示完全跟随；再来一周走回填路径删掉一只，该周显示「你剔除」那一只
- [x] 5.5 回填 `add-simulator-one-click-follow` 的任务 4.4

## 6. 验证收尾

- [x] 6.1 补 `specs/decision-comparison/spec.md` delta：主 spec 仍在描述已删除的 `overlap` / `user_only` / `strategy_only` / `common` 字段，归档后会与代码矛盾
- [x] 6.2 同 delta 内一并修正三处既有漂移：HTML 导出的机制描述（不是 Jinja2）、已不存在的 CLI 场景、集中度警告文案
- [x] 6.3 把「警告」列写回 `simulator-antd-ui` 的 MODIFIED 需求文本——实现一直在渲染，契约里漏了
- [x] 6.4 真机验未跑过的 UI 场景：未配置参考策略不展示采纳入口；有策略无信号展示禁用入口且 tooltip 可弹出；策略回测失败展示原因；无参考策略不绘制策略曲线；无推荐周次显示「无参考策略」而非「完全跟随」
- [x] 6.5 补回撤警告的测试：`drawdown_warning` 此前从未被断言过，只检查过 key 存在
- [x] 6.6 补导出报告的断言：确认 HTML 表头含「完全跟随 / 你剔除 / 你额外加」，且被剔除的代码确实出现在表里
- [x] 6.7 清掉 `_annotate_extremes` 里永假的条件（`i >= len(values)` 在 `i <= len(values) - 1` 的守卫下不可能成立）
- [x] 6.8 直接修正主 spec `decision-comparison` 的 Purpose（delta 改不了 Purpose，实测 archive 会忽略 delta 里的 `## Purpose` 段）
