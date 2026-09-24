## MODIFIED Requirements

### Requirement: Generate three-line comparison

The system SHALL produce a comparison report with three NAV curves: user manual decisions, reference strategy, and benchmark (CSI 300), all normalized to start at 1.0.

当参考策略的回测失败时，系统 SHALL 仍然返回手动盘与基准两条序列，并 SHALL 在结果中给出失败原因——少一条线必须说得出为什么，SHALL NOT 静默省略。

#### Scenario: Three NAV curves rendered

- **WHEN** user calls `compare()` after 12 weeks of manual decisions with reference strategy "factor_ranking"
- **THEN** the report includes three NAV series over the same date range; the strategy NAV is computed by re-running `run_backtest` from the session start date to the cursor date; the benchmark NAV is derived from CSI 300 prices

#### Scenario: No reference strategy configured

- **WHEN** session has `reference_strategy=None`, only manual NAV and benchmark NAV are shown; the strategy line is omitted from the curve and metrics

#### Scenario: Strategy backtest fails

- **WHEN** the reference strategy's shadow backtest raises, or the strategy name is not registered
- **THEN** the report still carries the manual and benchmark series, the strategy series is absent, and the failure reason is reported alongside it

### Requirement: Show weekly decision differences

The system SHALL produce a per-week comparison of what the user aimed at against what was recommended for that week, sourced from the decision record's own submitted orders and the signals recorded with them — SHALL NOT depend on a shadow backtest's per-week holdings.

两侧的组合 SHALL 由方向为买入且目标仓位大于 0 的代码集合构成；被剔除 SHALL 为「推荐有、用户没有」，被额外加入 SHALL 为「用户有、推荐没有」。

没有可依据的推荐时（未配置参考策略，或策略未产出信号），该周的偏离 SHALL 为无值，SHALL NOT 记为完全跟随。完全跟随 SHALL NOT 与「无推荐可依据」合并呈现。

#### Scenario: Weekly diff with clear divergence

- **WHEN** the recommendation for week 5 is 600519 and 002594, and the user submits 600519, 002594 and 000858
- **THEN** the weekly diff for week 5 reports the user added 000858 and dropped nothing, and is not marked as following the recommendation

#### Scenario: Weekly diff with full agreement

- **WHEN** user exactly follows the strategy's recommendations in week 8
- **THEN** the weekly diff for week 8 is marked as following the recommendation, with nothing added or dropped

#### Scenario: No recommendation to compare against

- **WHEN** a week's decision carries no strategy signals, or the session has no reference strategy
- **THEN** that week's deviation is absent, and is not reported as following the recommendation

#### Scenario: Weight changes alone do not count as divergence

- **WHEN** the user submits the same names as the recommendation with different target weights
- **THEN** the week is marked as following the recommendation

### Requirement: Annotate extreme portfolio states

The comparison report SHALL flag weeks where portfolio concentration or drawdown exceeded reasonable thresholds.

集中度标注 SHALL 反映当周目标组合的只数，SHALL NOT 依赖逐个持仓的权重百分比——差异表比较的是意图，不是期末持仓。

#### Scenario: Over-concentration flag

- **WHEN** a week's target portfolio holds two or fewer stocks
- **THEN** that week in the diff table is annotated with a concentration warning naming the count

#### Scenario: Large drawdown flag

- **WHEN** the manual portfolio experiences a weekly NAV decline greater than 10%
- **THEN** that week is annotated with a drawdown warning: "周回撤: {pct}%"

## REMOVED Requirements

### Requirement: Export comparison to HTML report

**Reason**: 这条需求描述了两件已不存在的事。其一，报告并非「复用 `signals/reporter.py` 的 Jinja2 模板」——`comparison.py` 自己拼一段内联 f-string。其二，它带一条 `CLI plain text comparison (no export)` 场景，而平台早已没有命令行入口（`simulator-web` 的 spec 已记录独立启动命令被移除，`pyproject.toml` 也没有 `console_scripts`）。保留原文会让 spec 描述一个无法触发的路径。

**Migration**: 由同一能力下的 `Export comparison report to HTML` 取代——它保留导出契约，去掉 CLI 场景，并把逐周差异表的口径对齐到接口。

## ADDED Requirements

### Requirement: Export comparison report to HTML

The system SHALL render the comparison report as a standalone HTML file under `reports/`, built in the comparison module itself.

报告 SHALL 包含净值曲线、指标对比与逐周差异表，逐周差异 SHALL 使用与接口相同的口径（完全跟随 / 被剔除 / 被额外加入）。

平台不提供对比的命令行入口；对比只能经接口或页面触发。

#### Scenario: HTML report saved to reports directory

- **WHEN** user calls `compare(export_html=True)`
- **THEN** an HTML file is written to `reports/sim_<session_id>_<date>.html` containing the NAV chart (base64 PNG), the metrics comparison, and the weekly diff table

#### Scenario: Weekly diff table matches the API

- **WHEN** the exported report lists a week in which the user dropped a recommended name
- **THEN** that name appears in the report's "你剔除" column for that week
