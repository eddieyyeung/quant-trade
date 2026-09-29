## Why

仿真决策台目前只有一条决策路径：手写「代码:百分比」文本。参考策略信号已经在快照里返回并展示，但用户要采纳它，只能照着表格把代码和权重一个个敲进输入框——15 只等权持仓就是 15 段手工转录，每一步都可能在转录中出错，也让「参考策略到底比我强在哪」这个问题得不到干净的回答。

信号到订单的转换是领域语义（含自动补卖、目标权重口径），不是界面序列化细节。把它固化成服务层能力，前端才有稳定的采纳路径可用，回执里也才能留下「这笔单子是怎么来的」。

## What Changes

- 新增服务端推荐能力：在会话当前游标处生成「推荐方案订单」——由参考策略信号映射为可直接提交的订单列表，携带代码、方向、目标权重与理由，并标注来源策略名。
- `POST /api/sessions/{id}/step` 的订单请求 SHALL 支持可选 `reason`，使成交回执能保留推荐理由而非一律记为「用户主动建仓」。
- `OrderRequest` 增加可选 `reason` 字段；`Simulator.step` 中现有的 `hasattr(order, "reason")` 分支随之生效。
- 决策台新增「按推荐方案」入口：先预览将要执行的订单（含目标权重与理由），用户可选择直接执行，或把订单回填到现有决策表单后自行增删再提交。
- 无参考策略的会话 SHALL NOT 提供推荐入口，沿用既有「无参考策略」文案说明原因。

**BREAKING**: 无。新增字段可选、新增端点独立，既有手动路径行为不变。

## Capabilities

### New Capabilities

- `simulator-recommendation`: 推荐方案的生成与采纳语义——信号到订单的映射规则、来源标注、理由随订单流转到成交回执、无参考策略时的空态语义。

### Modified Capabilities

- `simulator-web`: 周度决策 API 的订单结构新增可选 `reason`；新增推荐方案查询端点。
- `simulator-antd-ui`: 决策台新增「按推荐方案」入口与订单预览，及回填表单的采纳路径。

## Impact

- `src/quant_trade/simulator/types.py` — `OrderRequest` 增加 `reason`
- `src/quant_trade/simulator/engine.py` — 推荐订单生成；`reason` 透传到 `ExecutedOrder`
- `src/quant_trade/simulator/api.py` — 新增推荐端点，`step` 解析 `reason`
- `web/src/api/simulator.ts`、`web/src/pages/simulator/DecisionForm.tsx`、`SessionDetail.tsx` — 采纳路径与预览
- `tests/test_simulator_api.py`、`tests/test_simulator_engine.py` — 端点与理由透传的覆盖
- 不改动：快速引擎的 `cash * 0.3` 单笔上限、对比视图的策略净值口径、`factor_ranking` 兜底推荐
