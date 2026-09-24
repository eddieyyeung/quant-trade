## 1. DataStore 的兜底不再静默

- [x] 1.1 `data/store.py` 的 `DataStore.__init__` 签名改为 `db_path: str | None = None`；为 `None` 时用 `config.get_config_path()` 的既有规则解析配置路径（`QUANT_CONFIG` 环境变量，否则默认配置文件），**不新造第二套解析**。docstring 写明该默认值的含义与代价（结果取决于进程环境）
- [x] 1.2 走到兜底分支时记一条 loguru warning，内容含**解析出的路径**，并用 `stacklevel` 指向调用方，使「谁在偷偷开默认库」在日志里定位得到
- [x] 1.3 确认显式传入路径时既不读配置也不告警（这条分支必须保持零副作用）
- [x] 1.4 新建 `tests/test_data_store_defaults.py`：未给出路径时解析到配置指定的库（用 `QUANT_CONFIG` 指向临时配置验证）；显式路径时不读配置；兜底时产生一条含路径的警告、正常传入时不产生；`caplog`/loguru sink 断言警告内容
- [x] 1.5 全量跑一次既有套件，确认改默认值没有波及任何现有测试（先前勘查：测试里没有任何裸 `DataStore()`）

## 2. 注册表与基类打开注入通道

- [x] 2.1 `strategies/registry.py` 的 `StrategyRegistry.get` 增加 `store: DataStore | None = None`，以 `cls(store=store)` **关键字**传递；形状与 `factors/registry.py` 的 `FactorRegistry.get(name, store=None)` 对齐
- [x] 2.2 `strategies/base.py` 的 `Strategy` 基类补默认构造 `__init__(self, store=None)`，把「策略持有哪个库」写进基类契约；docstring 说明子类若重写构造须保留该参数
- [x] 2.3 `strategies/factory.py` 的 `build_strategy(name, config, store=None)` 增加参数并透传给注册表（**不新增第二个工厂函数**，design D5）
- [x] 2.4 在 `tests/test_services_strategies.py` 或新建测试补：以 store 取用策略时策略持有同一个对象；工厂透传到策略实例；未声明 store 的策略类被以该参数实例化时抛错（design D4 的期望行为）
- [x] 2.5 在 `tests/test_strategy_registry.py`（或既有注册表测试）补一条形状一致性断言：因子注册表与策略注册表的取用接口都接受该可选参数

## 3. 调用点补齐

- [x] 3.1 `strategies/factor_ranking.py:63` 的 `factor_registry.get(fname)` 改为传 `store=store`——**它手里本来就有**，这是缺陷的现场
- [x] 3.2 `services/strategies.py:106` 的 `build_strategy(params.strategy, ctx.cfg)` 补传 `store=ctx.db`
- [x] 3.3 `services/backtest.py:157` 的 `build_strategy(params.strategy, config)` 补传该处持有的 store
- [x] 3.4 `services/factors.py:157,246,268` 三处 `factor_registry.get(name)` 补传 `store=ctx.db`
- [x] 3.5 `simulator/engine.py:94,157,212,489` 与 `simulator/comparison.py:144` 的 `strategy_registry.get(...)` 补传 `store=self._store`（对照 `simulator/snapshot.py:165` 的既有正确写法）
- [x] 3.6 核对 `backtest/engine.py:48` 的 `ds = store or DataStore()`：确认其调用方是否都传了 store，未传的补上；行为不得改变（design Non-Goal：不扩大范围）
- [x] 3.7 复核 grep 结果：全仓 `store or DataStore(...)` 共 **13** 处（D1 初稿记的 12 处漏了 `simulator/engine.py`），全部是有意保留的兜底——没有一处位于 `DataStore` 自身。每一处现在都经由 `DataStore(db_path=None)` 解析配置并在兜底时告警；需数据的调用路径都能拿到注入的 store

## 4. 回归锚点

- [x] 4.1 新增端到端断言（design D6）：配置指向一个夹具库，跑一次信号生成，断言仓库自带的 `data/quant.db` **全程未被打开**。做法是把默认路径指向一个不存在的位置，使任何落回默认库的尝试当场失败
- [x] 4.2 该断言须独立于实现：无论将来改成透传、必填还是别的机制，只要还有组件偷偷开默认库就应该变红。在测试 docstring 里写明这一点
- [x] 4.3 确认 `simulator/snapshot.py` 的既有注入路径行为不变（它本来就传对了，是本变更的回归基线）——`tests/test_simulator_snapshot.py` 全绿即为证
- [x] 4.4 在 `tests/test_services_strategies.py` 补：策略内部经注册表构造的因子持有与策略相同的数据访问对象（spec 的「策略内部的因子用同一个库」场景）

## 5. spec 与文档

- [x] 5.1 直接把 `openspec/specs/factor-system/spec.md` 的 `TBD` Purpose 换成真实描述（delta 格式不承载 Purpose，这一步是直接编辑主 spec）
- [x] 5.2 复核 `openspec validate --all --strict`：`factor-system` 应从失败转为通过；剩余失败项（`data-ingestion` / `simulator-session-logging` / `strategy-engine`）不在本变更范围，但需确认数量未增加
- [x] 5.3 更新 `scripts/e2e_report_simulator_pages.mjs` 与 `scripts/seed_report_simulator_e2e_db.py` 里描述本缺陷的注释：缺陷修掉后，真实信号运行的产出**应当**可以对之断言了。若仍不可断言，说明还有漏网，须查明而不是改注释了事
- [x] 5.4 若 5.3 成立，把 e2e 里「真实运行产出不可断言」的段落改为对真实产出断言（design Risks 里把这条列为验收信号）

## 6. 验证

- [x] 6.1 `uv run pytest` 全绿
- [x] 6.2 `uv run ruff check` / `uv run ruff format --check` / `uv run mypy src` 通过
- [x] 6.3 `cd web && npm run build` 通过，`npm run lint`（oxlint）无错误
- [x] 6.4 端到端：`QUANT_CONFIG` 指向夹具配置，跑一次 `kind: strategy_signals` → 产出的订单与夹具库的数据一致（而非仓库库的）
- [x] 6.5 端到端：同一进程内不出现「仓库自带库被打开」的痕迹（4.1 的断言在真实运行下同样成立）
- [x] 6.6 端到端回归：周报生成、回测、模型训练、仿真四条链路各跑一次，结果与改动前一致（本变更不改变任何计算结果）——**只对信号链路做到了逐值比对**：固定股票池后，改动前（三条接缝恢复为丢弃 store）与改动后的订单逐值一致，digest 相同。其余三条链路比对不出结论，原因见备注的乱序问题；模型链路更是构造上就检测不到本变更（它对每一处都显式传了 store，从不走兜底）
- [x] 6.7 `node scripts/e2e_report_simulator_pages.mjs` 全绿，且 5.3/5.4 的结论成立
- [x] 6.8 `openspec validate fix-factor-store-injection --strict` 通过

## 7. 复审（`/opsx:verify`）后的修正

两路对抗性审计（一路查 spec 与代码、一路查任务与 design 声明）各自做了变异测试，提出 3 项 critical 与 8 项 warning。以下为据此所做的修正，逐条都在修完后做了反向验证。

- [x] 7.1 **`simulator/engine.py:36` 的硬编码字面量**（critical）：`db_path: str = "data/quant.db"` 绕过兜底函数直接把路径交给 `DataStore(db_path)`，因此猴补式锚点拦不住它，3.6 的核对也漏了它。改为 `db_path: str | None = None`；补 `TestSimulatorStoreFallback` 两条（跟随配置、显式路径优先）
- [x] 7.2 **兜底在配置不可解析时抛异常**（critical）：`DataStore()` 在缺少 `config/default.yaml` 的 cwd 下抛 `FileNotFoundError`，与 ADDED 场景「告警不阻断执行 …… SHALL NOT 抛出异常」冲突。**选择收紧场景而非改代码**：能在确定用哪个库时不打断、无从确定时失败，是刻意的边界；改为另选一个固定路径才是本变更要根除的行为。场景拆成「可解析时不中断」+「不可解析时失败而非另选」，并补测试
- [x] 7.3 **`Factor` 基类不实现兜底**（warning）：基类只 `self.store = store`，而没有任何因子调用 `super().__init__()`，于是 spec 里「未收到时解析配置并告警」对基类不成立；照 spec 的「实现新因子」场景写一个只实现 `compute` 的子类，会在首次查询时 `AttributeError`。**改为在基类实现兜底**（`store is None` 时懒加载 `DataStore()`），使该场景成立；`DataStore` 用局部 import 避开循环依赖
- [x] 7.4 **形状一致性测试是空转的**（warning）：原测试只断言 `"store" in params`，把 `StrategyRegistry.get` 的首个参数改名后 12 条测试全绿。改为逐参数比对 `(name, kind, default)`；变异验证：改名后该测试失败
- [x] 7.5 **「正常注入时不告警」测的是 `DataStore` 而非组件**（warning）：原断言为 `DataStore("/tmp/quiet.db")`，删掉所有组件的注入它照样绿。改为构造真实组件（经注册表的因子 + `build_strategy` 的策略）；变异验证：让 `momentum.py` 丢弃注入的 store 后该测试失败
- [x] 7.6 **7 条场景无测试**（warning）：A4.1 / A4.2 / A5.1 / B1.1 / B1.3 / B2.2 / B2.3，其中四条是本变更自己写的。新建 `tests/test_factor_store_injection.py` 覆盖
- [x] 7.7 **`test_bridge_reads_cache` 依赖开发机状态**（warning）：该测试的 `len(vals) == 3` 由仓库自带的 `data/quant.db` 满足（已实测该库在同一天确有这三只票），因此让 bridge 忽略注入的 store 仍然通过。补 `assert f.store is store`；变异验证：改 `bridge.py:27` 后该测试失败
- [x] 7.8 **行号与计数错误**：`store.py` 的乱序查询是 **208 / 234 / 322** 三处（原写 213，那是 WHERE 片段，且漏了 322）；`store or DataStore(...)` 是 **13** 处而非 12，D1 的盘点表漏了 `simulator/engine.py`
- [x] 7.9 **遗留缺陷反模式**：`tests/test_integration.py:140,489` 仍在用 `factor_registry.get(fname)` 再 `factor.store = store` 的写法（正是本变更移除的模式），`tests/test_model_strategy.py:95` 取策略不传 store。均已改为经注册表注入
- [x] 7.10 **D6 机制与实现不符**：design 原文写「把默认路径指向一个不存在的位置」，实现改为猴补 `_configured_db_path` 抛错（覆盖面更宽，但拦不住绕过该函数的字面量，7.1 正是这么漏的）。已在 D6 补记机制、覆盖面边界，以及该锚点对「注入错库」不敏感这一点
- [x] 7.11 **Purpose 占位**：填掉 `strategy-engine`、`data-ingestion`、`simulator-session-logging`、`factor-store-injection` 四处 `TBD`。`openspec validate --all --strict` 由 **35 通过 / 3 失败**变为 **38 通过 / 0 失败**（超出 5.2 的「数量未增加」，应为「全部通过」）
- [x] 7.12 **MODIFIED 措辞收紧**：`Factor compute uses injected store` 的正文仍写「与 standalone DataStore 等值」而场景已收窄；正文改为「与在同一数据库文件上打开的 store 等值」。另修 `strategy-engine` 里指向已删除 CLI 的引用
- [x] 7.13 **`proposal.md` 能力归属**：`factor-store-injection` 被列在 New Capabilities 下，但该能力早已存在（`2026-08-07-fix-simulator-memory-leak` 建立）。已移入 Modified Capabilities

## 备注

- **本变更声称不改变任何计算结果。** 它只改变数据**从哪来**。6.6 是这条声称的验证：四条既有链路的结果必须与改动前逐值一致。唯一例外是当配置指向非默认库时——那时结果**应当**改变，因为此前它是错的。
- **D1（两层一起修）未经用户确认**：开工前问过「兜底怎么办」的岔口，用户直接要求快进。design D1 记录了理由与被否的两个替代方案。只补调用点的话，把兜底原样留下即可，工作量约减半，但地雷仍在。
- **不把 store 改必填**是刻意的范围限制（Non-Goal）：十二处兜底、每个单测都要构造 store，改动量数倍，与「不改计算结果」的约束相冲。留给 Open Questions 里的后续变更。
- **`DataStore()` 从此有环境依赖**是 D2 明写的代价。它把一个「隐式的错」换成「隐式的对」，隐性本身仍在；D3 的警告与 Open Questions 的必填化是两条出路。
- 缺陷的最初发现者不是本变更，是 `add-strategy-research-pages` 的端到端验证——当时撞上「提交的信号生成对着夹具库跑、产出却来自仓库库」，据此记下了 `FactorRegistry.get(store=None)` 与 `factor_ranking.py:64` 的证据链。
- **验证 6.6 时撞出一个既有缺陷，不在本变更范围内**：`data/store.py:208`、`:234` 与 `:322` 的股票池查询都是 `SELECT DISTINCT ts_code` 且**没有 `ORDER BY`**。DuckDB 每次进程返回的行序都不同（实测 801 只股票，三次运行三种顺序），而 `factor_ranking` 的 `sort_values` 在并列分数上按该顺序破平，`_apply_industry_constraint` 也按该顺序截断。后果是**同一份代码连跑两次的选股与回测结果可以不同**——实测同一模式两次运行，`total_return` 一次 0.2693 一次 -0.1152。这让「结果与改动前逐值一致」这条验收在 801 只股票的池子上根本无法比对，两边都被乱序淹没。`scripts/smoke_ml_pipeline.py:49` 的 `np.random.default_rng(hash(c))` 是同类问题的另一处（`hash` 按进程随机化）。**建议另立变更**：查询补 `ORDER BY`，并列分数用稳定的破平规则。
- **能力归属经用户确认改为并入既有 `factor-store-injection`**：本变更最初把 store 透传写成新能力 `store-injection`，但 `2026-08-07-fix-simulator-memory-leak` 已经建过 `factor-store-injection` 覆盖同一件事，且其「未传 store 时保持既有行为」的场景在本变更后不再成立。现 delta 目录已改名为 `factor-store-injection`，三条新要求仍为 ADDED，另以 MODIFIED 修正那条场景与「与 `DataStore()` 同库等值」的场景（`DataStore()` 现在解析配置，不再能指向任意文件）。
