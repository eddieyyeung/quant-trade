## Purpose

Decision comparison: three-line NAV comparison reports contrasting user manual decisions against the reference strategy and the CSI 300 benchmark, including weekly position diffs and drawdown warnings.

## Requirements

### Requirement: Generate three-line comparison

The system SHALL produce a comparison report with three NAV curves: user manual decisions, reference strategy, and benchmark (CSI 300), all normalized to start at 1.0.

#### Scenario: Three NAV curves rendered

- **WHEN** user calls `compare()` after 12 weeks of manual decisions with reference strategy "factor_ranking"
- **THEN** the report includes three NAV series over the same date range; the strategy NAV is computed by re-running `run_backtest` from the session start date to the cursor date; the benchmark NAV is derived from CSI 300 prices

#### Scenario: No reference strategy configured

- **WHEN** session has `reference_strategy=None`, only manual NAV and benchmark NAV are shown; the strategy line is omitted from the curve and metrics

### Requirement: Compute key comparison metrics

The system SHALL compute and compare annual return, Sharpe ratio, max drawdown, win rate, and total return for each line (manual, strategy, benchmark).

#### Scenario: Metrics comparison table

- **WHEN** `compare()` is called
- **THEN** output includes a structured metrics dict: `{manual: {total_return, annual_return, sharpe_ratio, max_drawdown, win_rate}, strategy: {...}, benchmark: {...}}`

### Requirement: Show weekly decision differences

The system SHALL produce a per-week diff showing where user decisions diverged from the reference strategy: which stocks user bought that the strategy did not recommend, which strategy recommendations the user ignored, and the overlap rate.

#### Scenario: Weekly diff with clear divergence

- **WHEN** user bought 600519 (strategy also recommended), 002594 (strategy also recommended), and 000858 (strategy did NOT recommend) in week 5, while the strategy recommended 600900 which the user did not buy
- **THEN** the weekly diff for week 5 shows: `overlap: 2/5`, `user_only: ["000858"]`, `strategy_only: ["600900"]`, `common: ["600519", "002594"]`

#### Scenario: Weekly diff with full agreement

- **WHEN** user exactly follows the strategy's recommendations in week 8
- **THEN** the weekly diff shows `overlap: N/N`, `user_only: []`, `strategy_only: []`

### Requirement: Annotate extreme portfolio states

The comparison report SHALL flag weeks where portfolio concentration, drawdown, or turnover exceeded reasonable thresholds.

#### Scenario: Over-concentration flag

- **WHEN** a single stock exceeds 30% of portfolio value in any week
- **THEN** that week in the diff table is annotated with a concentration warning: "单股权重过高: {code} {weight_pct}%"

#### Scenario: Large drawdown flag

- **WHEN** the manual portfolio experiences a weekly NAV decline greater than 10%
- **THEN** that week is annotated with a drawdown warning: "周回撤: {pct}%"

### Requirement: Export comparison to HTML report

The system SHALL render the comparison report as an HTML file reusing the existing Jinja2 template infrastructure and matplotlib chart rendering from `signals/reporter.py`.

#### Scenario: HTML report saved to reports directory

- **WHEN** user calls `compare(export_html=True)`
- **THEN** an HTML file is written to `reports/sim_<session_id>_<date>.html` containing the three-line NAV chart (base64 PNG), metrics comparison table, and expandable weekly diff sections

#### Scenario: CLI plain text comparison (no export)

- **WHEN** user calls `compare()` without export flag
- **THEN** metrics and weekly diff summary are printed to stdout with rich-formatted tables; no HTML file is generated
