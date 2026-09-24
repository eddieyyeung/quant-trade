## 1. 订单理由透传（服务端）

- [x] 1.1 `simulator/types.py`：`OrderRequest` 增加 `reason: str = ""`
- [x] 1.2 `simulator/engine.py`：`step` 的买入成交理由从 `hasattr` 死分支改为读 `order.reason or "用户主动建仓"`
- [x] 1.3 `simulator/api.py`：`step` 的 body 解析可选 `reason`；`executed_orders` 序列化补上 `reason`
- [x] 1.4 `tests/test_simulator_engine.py`：一条带 `reason` 的买入单成交理由为该值；不带时回落为「用户主动建仓」

## 2. 推荐订单的生成（服务端）

- [x] 2.1 新增 `simulator/recommendation.py`：纯函数把 `list[StrategySignalItem]` 映射为 `list[OrderRequest]`，保持买入在前、卖出在后，逐条带上 `reason`
- [x] 2.2 `simulator/types.py`：`Snapshot` 增加 `recommended_orders: list[OrderRequest] | None` 与 `recommendation_source: str | None`
- [x] 2.3 `simulator/snapshot.py`：在 `_build_strategy_signals` 之后映射推荐订单；信号为 `None` 时推荐为 `None`、来源为 `None`，信号为 `[]` 时推荐为 `[]`、来源为策略名；`skip_heavy` 分支两者均为 `None`
- [x] 2.4 `simulator/api.py`：`_serialize_snapshot` 输出 `recommended_orders` 与 `recommendation_source`，维持 `null` 与 `[]` 的区分
- [x] 2.5 `tests/test_simulator_snapshot.py`：三种状态各一例（无策略 / 有策略无信号 / 有策略有信号），并断言目标权重原样传递、补出的卖出条目被保留、买入排在卖出之前

## 3. 前端采纳路径

- [x] 3.1 `web/src/api/simulator.ts`：`Snapshot` 类型补 `recommended_orders` 与 `recommendation_source`；`OrderRequest` 补 `reason?`；`ExecutedOrder` 已含 `reason`，确认与后端一致
- [x] 3.2 新增 `web/src/pages/simulator/recommendation.ts`：纯函数把推荐订单转成买入/卖出输入串（买入 `代码:百分比`，卖出代码列表），与 `orders.ts` 的语法互为逆运算
- [x] 3.3 新增预览模态框组件：表格列出代码、方向、目标权重、理由，标题或说明标注来源策略名，底部「执行」与「回填到表单」
- [x] 3.4 `DecisionForm.tsx`：接住回填——把订单写入买入/卖出输入框并把备注预填为标明来源策略的方案名称；成交回执逐笔补上理由
- [x] 3.5 `SessionDetail.tsx`：推荐为无值时不渲染采纳入口；为空列表时渲染禁用入口并说明本周无推荐内容；入口点击打开预览
- [x] 3.6 「执行」路径把推荐订单原样 `POST /step`（含 `reason`），成功后沿用既有的重新加载与多行回执

## 4. 校验

- [x] 4.1 `uv run pytest tests/test_simulator_snapshot.py tests/test_simulator_engine.py tests/test_simulator_api.py`
- [x] 4.2 `uv run ruff check && uv run ruff format && uv run mypy src`
- [x] 4.3 `cd web && npm run build && npm run lint`
- [x] 4.4 手工走一遍：有参考策略的会话，一键执行一周，确认成交回执带策略理由；再来一周走回填路径删掉一个标的，确认对比视图里该周出现持仓差异
- [x] 4.5 `tests/test_simulator_api.py`：订单不带 `reason` 时成交理由回落为「用户主动建仓」——此前只有引擎层覆盖，HTTP 层没有
