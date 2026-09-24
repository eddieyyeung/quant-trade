"""Source contracts for the research UI shell.

There is no JS test infrastructure in this repository, so — as with
``test_simulator_api.TestFrontendContract`` — the spec's frontend guarantees are
asserted against the sources and manifests that carry them. These are cheap
contract checks, not rendering tests; the pages themselves are verified end to
end in a browser.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

WEB = Path(__file__).parents[1] / "web"

RUNTIME_DEPENDENCIES = ("antd", "@ant-design/icons", "echarts", "echarts-for-react", "react-router-dom")

SECTION_LABELS = ("数据", "因子", "模型", "策略", "回测", "仿真", "报告", "任务中心")


def _read(*parts: str) -> str:
    return WEB.joinpath(*parts).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def package() -> dict[str, object]:
    return json.loads(_read("package.json"))


class TestDependencies:
    def test_runtime_deps_are_declared_in_the_web_package(self, package: dict[str, object]) -> None:
        declared = package["dependencies"]  # type: ignore[index]
        missing = [name for name in RUNTIME_DEPENDENCIES if name not in declared]
        assert missing == [], f"web/package.json is missing {missing}"

    def test_no_module_imports_recharts(self) -> None:
        """Charts are ECharts; recharts was removed with the legacy simulator tree."""
        offenders = [
            path.relative_to(WEB)
            for path in (WEB / "src").rglob("*")
            if path.is_file() and path.suffix in {".ts", ".tsx"} and "recharts" in path.read_text(encoding="utf-8")
        ]
        assert offenders == [], f"recharts is no longer a dependency but is imported by {offenders}"


class TestShell:
    def test_sections_cover_the_eight_domains(self) -> None:
        nav = _read("src", "shell", "navigation.tsx")
        missing = [label for label in SECTION_LABELS if f"label: '{label}'" not in nav]
        assert missing == [], f"navigation.tsx does not declare {missing}"

    def test_shell_is_built_from_antd_layout_primitives(self) -> None:
        shell = _read("src", "shell", "AppShell.tsx")
        for primitive in ("Layout", "Sider", "Menu"):
            assert primitive in shell, f"AppShell.tsx does not use antd {primitive}"

    def test_theme_is_applied_through_config_provider(self) -> None:
        """Tokens live in one place rather than in per-page stylesheets."""
        main = _read("src", "main.tsx")
        assert "ConfigProvider" in main
        assert "platformTheme" in main

    def test_no_hand_written_stylesheet_is_imported(self) -> None:
        """The shell must not depend on the removed index.css."""
        assert not (WEB / "src" / "index.css").exists()
        assert "index.css" not in _read("src", "main.tsx")

    def test_unbuilt_sections_route_to_the_placeholder(self) -> None:
        routes = _read("src", "shell", "routes.tsx")
        assert "Placeholder" in routes
        assert "!section.implemented" in routes


class TestBacktestSection:
    """The backtest section, asserted against the sources that carry it."""

    BACKTEST_PAGES = ("BacktestNav", "Compare", "Detail", "List")

    def _backtest_sources(self) -> list[Path]:
        return sorted((WEB / "src" / "pages" / "backtest").glob("*.tsx"))

    def test_section_is_marked_implemented(self) -> None:
        nav = _read("src", "shell", "navigation.tsx")
        assert (
            "{ key: '/backtest', path: '/backtest', label: '回测', icon: <FundOutlined />, implemented: true }" in nav
        )

    def test_routes_are_declared_explicitly(self) -> None:
        routes = _read("src", "shell", "routes.tsx")
        for path in ('path="/backtest"', 'path="/backtest/compare"', 'path="/backtest/:runId"'):
            assert path in routes, f"routes.tsx is missing {path}"

    def test_compare_route_precedes_the_run_id_route(self) -> None:
        """A `:runId` route declared first would swallow `/backtest/compare`."""
        routes = _read("src", "shell", "routes.tsx")
        assert routes.index('path="/backtest/compare"') < routes.index('path="/backtest/:runId"')

    def test_pages_are_present(self) -> None:
        names = {path.stem for path in self._backtest_sources()}
        missing = [name for name in self.BACKTEST_PAGES if name not in names]
        assert missing == [], f"pages/backtest is missing {missing}"

    def test_charts_go_through_the_shared_wrapper(self) -> None:
        """Type-only imports are fine — they carry no runtime code."""
        offenders: list[str] = []
        for path in self._backtest_sources():
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("import type"):
                    continue
                if "from 'echarts'" in stripped or 'from "echarts"' in stripped:
                    offenders.append(f"{path.name}: {stripped}")
        assert offenders == [], f"import the shared EChart wrapper instead: {offenders}"

    def test_wrapper_registers_the_pie_chart(self) -> None:
        """The position-weight pie needs its chart type registered on demand."""
        assert "PieChart" in _read("src", "charts", "echarts.ts")

    def test_no_new_stylesheet(self) -> None:
        directory = WEB / "src" / "pages" / "backtest"
        sheets = [path.name for path in directory.rglob("*") if path.suffix in {".css", ".less", ".scss"}]
        assert sheets == [], f"styling comes from antd and theme tokens, not {sheets}"

    def test_submits_through_the_run_api(self) -> None:
        source = _read("src", "pages", "backtest", "List.tsx")
        assert "runsApi.submit('backtest'" in source

    def test_detail_rebases_its_curves(self) -> None:
        """Strategy NAV is absolute yuan and the benchmark is a 1.0 index.

        Drawn as-is they cannot share a y-axis, and their difference would be
        the account balance rather than excess return (design D12).
        """
        source = _read("src", "pages", "backtest", "Detail.tsx")
        assert "function rebase(" in source
        assert "净值（期初 = 1）" in source

    def test_compare_rebases_so_initial_capital_does_not_decide_the_winner(self) -> None:
        """Defining a `rebase` is not the point — the curves must go through it."""
        source = _read("src", "pages", "backtest", "Compare.tsx")
        assert "function rebase(" in source, "no rebase helper"
        assert "rebase(run.series?.nav" in source, "the overlay does not rebase its series"
        assert "净值（期初 = 1）" in source, "the axis does not say it is rebased"

    def test_list_shows_progress_for_running_runs(self) -> None:
        source = _read("src", "pages", "backtest", "List.tsx")
        assert "row.progress" in source
        assert "Progress" in source
        assert "progress" in _read("src", "api", "backtests.ts")
        # The terminal branch is what keeps a finished row from showing a bar.
        assert "isTerminal(row.status as RunStatus) ?" in source, "no terminal/non-terminal split"

    def test_a_failed_request_is_not_reported_as_empty(self) -> None:
        """Three pages can mistake a failure for an empty result set."""
        listing = _read("src", "pages", "backtest", "List.tsx")
        assert "回测列表加载失败" in listing
        assert "listFailure !== null" in listing, "the empty state would win over the error"

        detail = _read("src", "pages", "backtest", "Detail.tsx")
        assert "回测详情加载失败" in detail
        assert "交易明细加载失败" in detail, "a failed trades request would read as 无成交"
        assert "tradesFailure !== null" in detail

    def test_detail_resets_state_when_the_run_changes(self) -> None:
        """React Router reuses the instance across `:runId`, so a stale run's
        curves would otherwise sit under the next run's URL."""
        source = _read("src", "pages", "backtest", "Detail.tsx")
        assert "}, [runId]);" in source, "no reset keyed on runId"

    def test_missing_benchmark_is_not_shown_as_zero(self) -> None:
        """The engine leaves benchmark_return at 0.0 when there is no benchmark."""
        source = _read("src", "pages", "backtest", "Detail.tsx")
        assert "hasBenchmark ? formatPercent(detail.metrics.benchmark_return)" in source

    def test_comparison_marks_cancelled_runs_on_the_row(self) -> None:
        source = _read("src", "pages", "backtest", "Compare.tsx")
        assert "status === 'cancelled' ?" in source, "cancelled rows are not marked apart"
        assert "覆盖交易日" in source, "the coverage difference is invisible"
        assert "覆盖区间不同，指标不具可比性" in source

    def test_empty_states_replace_the_table_rather_than_emptying_it(self) -> None:
        """A header row with nothing under it says less than one line of text."""
        assert "尚无回测" in _read("src", "pages", "backtest", "List.tsx")
        detail = _read("src", "pages", "backtest", "Detail.tsx")
        assert "无成交" in detail
        assert "期末空仓" in detail

    def test_cancelled_runs_are_labelled_by_what_actually_happened(self) -> None:
        source = _read("src", "pages", "backtest", "Detail.tsx")
        assert "{detail.status === 'cancelled' ? '取消于' : '完成于'}" in source, (
            "the label must depend on the status — an unconditional 取消于 is as wrong as 完成于"
        )

    def test_section_tabs_track_the_route(self) -> None:
        source = _read("src", "pages", "backtest", "BacktestNav.tsx")
        assert "activeKey={location.pathname}" in source
        assert "onChange={key => navigate(key)}" in source
        for page in ("/backtest', label: '回测", "/backtest/compare', label: '对比"):
            assert page in source, f"BacktestNav.tsx is missing the {page} tab"

    def test_comparison_has_an_empty_state(self) -> None:
        assert "没有可对比的结果" in _read("src", "pages", "backtest", "Compare.tsx")


class TestModelSection:
    """The model section, asserted against the sources that carry it."""

    MODEL_PAGES = ("Evaluate", "ModelNav", "Predict", "Train")

    def _model_sources(self) -> list[Path]:
        return sorted((WEB / "src" / "pages" / "models").glob("*.tsx"))

    def test_section_is_marked_implemented(self) -> None:
        nav = _read("src", "shell", "navigation.tsx")
        assert (
            "{ key: '/models', path: '/models', label: '模型', icon: <ThunderboltOutlined />, implemented: true }"
            in nav
        )

    def test_routes_are_declared_explicitly(self) -> None:
        routes = _read("src", "shell", "routes.tsx")
        for path in ('path="/models"', 'path="/models/evaluate/:runId"', 'path="/models/predict"'):
            assert path in routes, f"routes.tsx is missing {path}"

    def test_pages_are_present(self) -> None:
        names = {path.stem for path in self._model_sources()}
        missing = [name for name in self.MODEL_PAGES if name not in names]
        assert missing == [], f"pages/models is missing {missing}"

    def test_charts_go_through_the_shared_wrapper(self) -> None:
        """Type-only imports are fine — they carry no runtime code."""
        offenders: list[str] = []
        for path in self._model_sources():
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("import type"):
                    continue
                if "from 'echarts'" in stripped or 'from "echarts"' in stripped:
                    offenders.append(f"{path.name}: {stripped}")
        assert offenders == [], f"import the shared EChart wrapper instead: {offenders}"

    def test_no_new_stylesheet(self) -> None:
        directory = WEB / "src" / "pages" / "models"
        sheets = [path.name for path in directory.rglob("*") if path.suffix in {".css", ".less", ".scss"}]
        assert sheets == [], f"styling comes from antd and theme tokens, not {sheets}"

    def test_submits_through_the_run_api(self) -> None:
        source = _read("src", "pages", "models", "Train.tsx")
        assert "runsApi.submit('model_train'" in source

    def test_evaluation_only_reads(self) -> None:
        """Evaluation is a point-in-time read: it must not be able to submit anything."""
        assert "runsApi" not in _read("src", "pages", "models", "Evaluate.tsx")

    # The states below are asserted as source text, like everything else in this
    # file. Whether they *render* is a manual check: the repo has no
    # component-test harness, and adding one is a change of its own — these
    # assertions exist so the states cannot be dropped unnoticed.

    def test_train_page_covers_its_empty_and_blocked_states(self) -> None:
        source = _read("src", "pages", "models", "Train.tsx")
        assert "尚无训练" in source, "empty history needs an entry point, not a blank table"
        assert "无产出" in source, "a finished run that produced nothing is marked as such"
        assert "起始日期不能晚于结束日期" in source, "an inverted range is blocked before the request"

    def test_train_rows_show_progress_while_running(self) -> None:
        """A running row's results are empty until it ends; progress is what moves."""
        source = _read("src", "pages", "models", "Train.tsx")
        assert "<Progress" in source
        assert "row.progress" in source

    def test_evaluate_page_covers_its_empty_and_error_states(self) -> None:
        source = _read("src", "pages", "models", "Evaluate.tsx")
        assert "无有效 IC" in source, "a run with no rankable day must not draw an empty curve"
        assert "该次训练已取消" in source, "a cancelled run is labelled and its curve stops early"
        assert "不存在" in source, "a run id that is not a training run says so"

    def test_predict_page_covers_the_untrained_state(self) -> None:
        source = _read("src", "pages", "models", "Predict.tsx")
        assert "尚无预测" in source, "nothing trained yet is a prompt, not a failed request"
        assert "disabledDate" in source, "dates without predictions are not offered"


class TestServedShell:
    def test_page_title_names_the_platform(self) -> None:
        assert "<title>量化研究平台</title>" in _read("index.html")


class TestReportsSection:
    """The report section, asserted against the sources that carry it."""

    REPORTS_PAGES = ("List", "Preview")

    def _reports_sources(self) -> list[Path]:
        return sorted((WEB / "src" / "pages" / "reports").glob("*.tsx"))

    def test_section_is_marked_implemented(self) -> None:
        nav = _read("src", "shell", "navigation.tsx")
        assert (
            "{ key: '/reports', path: '/reports', label: '报告', icon: <FileTextOutlined />, implemented: true }" in nav
        )

    def test_routes_are_declared_explicitly(self) -> None:
        routes = _read("src", "shell", "routes.tsx")
        for path in ('path="/reports"', 'path="/reports/:runId"'):
            assert path in routes, f"routes.tsx is missing {path}"

    def test_pages_are_present(self) -> None:
        names = {path.stem for path in self._reports_sources()}
        missing = [name for name in self.REPORTS_PAGES if name not in names]
        assert missing == [], f"pages/reports is missing {missing}"

    def test_no_new_stylesheet(self) -> None:
        directory = WEB / "src" / "pages" / "reports"
        sheets = [path.name for path in directory.rglob("*") if path.suffix in {".css", ".less", ".scss"}]
        assert sheets == [], f"styling comes from antd and theme tokens, not {sheets}"

    def test_preview_never_triggers_a_generation(self) -> None:
        """Reading a report must not be able to start one."""
        assert "runsApi" not in _read("src", "pages", "reports", "Preview.tsx")

    def test_preview_embeds_the_document_rather_than_inlining_it(self) -> None:
        """The report carries its charts as base64; srcDoc would double it."""
        source = _read("src", "pages", "reports", "Preview.tsx")
        assert "<iframe" in source
        # The prop, not the word: the comment above the frame explains why it
        # is not a srcDoc, and a bare substring check would read that as usage.
        assert "srcDoc=" not in source
        assert "reportsApi.htmlUrl" in source, "the frame must point at the served document"

    def test_preview_offers_a_download(self) -> None:
        assert "download: true" in _read("src", "pages", "reports", "Preview.tsx")

    def test_list_does_not_report_a_failure_as_empty(self) -> None:
        source = _read("src", "pages", "reports", "List.tsx")
        assert "报告列表加载失败" in source
        assert "listFailure !== null" in source, "the empty state would win over the error"

    def test_list_empty_state_explains_where_reports_come_from(self) -> None:
        """Hand-placed files are not listed; say so rather than look lossy."""
        source = _read("src", "pages", "reports", "List.tsx")
        assert "尚无报告" in source
        assert "历史文件不在列表中" in source

    def test_preview_links_are_gated_on_having_a_report(self) -> None:
        """Not on the status: a cancelled run may still have written its report.

        Gating on `ok` would hide a readable report and leave the preview page's
        cancelled-label branch unreachable.
        """
        source = _read("src", "pages", "reports", "List.tsx")
        assert "row.file_name ?" in source, "a run with no file would offer a dead link"
        assert "row.status === 'ok' ?" not in source, "the gate is the file, not the status"

    def test_running_rows_show_progress(self) -> None:
        source = _read("src", "pages", "reports", "List.tsx")
        assert "row.progress" in source
        assert "<Progress" in source


class TestSimulatorSection:
    """The simulator section, asserted against the sources that carry it."""

    SIMULATOR_PAGES = (
        "ComparisonView",
        "CreateSession",
        "DecisionForm",
        "FactorRanking",
        "PortfolioTable",
        "SessionDetail",
        "SessionList",
        "StrategySignals",
    )

    def _simulator_sources(self) -> list[Path]:
        return sorted((WEB / "src" / "pages" / "simulator").glob("*.tsx"))

    def test_section_is_marked_implemented(self) -> None:
        nav = _read("src", "shell", "navigation.tsx")
        assert (
            "{ key: '/simulator', path: '/simulator', label: '仿真', icon: <AppstoreOutlined />, implemented: true }"
            in nav
        )

    def test_routes_are_declared_explicitly(self) -> None:
        routes = _read("src", "shell", "routes.tsx")
        for path in ('path="/simulator"', 'path="/simulator/:sessionId"'):
            assert path in routes, f"routes.tsx is missing {path}"

    def test_pages_are_present(self) -> None:
        names = {path.stem for path in self._simulator_sources()}
        missing = [name for name in self.SIMULATOR_PAGES if name not in names]
        assert missing == [], f"pages/simulator is missing {missing}"

    def test_no_new_stylesheet(self) -> None:
        directory = WEB / "src" / "pages" / "simulator"
        sheets = [path.name for path in directory.rglob("*") if path.suffix in {".css", ".less", ".scss"}]
        assert sheets == [], f"styling comes from antd and theme tokens, not {sheets}"

    def test_charts_go_through_the_shared_wrapper(self) -> None:
        """Type-only imports are fine — they carry no runtime code."""
        offenders: list[str] = []
        for path in self._simulator_sources():
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("import type"):
                    continue
                if "from 'echarts'" in stripped or 'from "echarts"' in stripped:
                    offenders.append(f"{path.name}: {stripped}")
        assert offenders == [], f"import the shared EChart wrapper instead: {offenders}"

    def test_comparison_does_not_rebase(self) -> None:
        """The engine already normalises every curve to 1.0.

        Dividing by the first point again — which the backtest pages must do,
        because their benchmark and their NAV are in different units — would
        scale these curves a second time.
        """
        source = _read("src", "pages", "simulator", "ComparisonView.tsx")
        assert "rebase" not in source

    def test_order_grammar_is_preserved(self) -> None:
        """Buys are `CODE:PCT` with the percentage converted to a fraction."""
        source = _read("src", "pages", "simulator", "orders.ts")
        # `\b100\b`, not `/ 100`: the bare substring also matches `/ 1000`.
        assert re.search(r"/\s*100\b", source), "the fraction conversion is gone"
        assert source.index("direction: 'BUY'") < source.index("direction: 'SELL'"), (
            "sells must not be emitted before buys — the engine fills in order"
        )
        # A sell is a full exit, so its target weight is zero — not the buy
        # default carried over.
        assert re.search(r"target_pct:\s*0\b", source), "a sell must target zero weight"

    def test_incomplete_buys_are_skipped_not_fatal(self) -> None:
        """`600519.SH:10, 002594.SZ` places the order it can."""
        source = _read("src", "pages", "simulator", "orders.ts")
        assert "continue" in source, "a malformed pair would abort the whole submission"
        assert "Number.isFinite" in source, "a non-numeric percentage would serialise to null"

    def test_signal_emptiness_keeps_its_two_meanings(self) -> None:
        """`null` is "no reference strategy"; `[]` is "configured and quiet".

        Checked by order, not just presence: both strings live in one file, so
        asserting they merely exist would stay green if the two branches were
        swapped — which is precisely the bug this guards.
        """
        source = _read("src", "pages", "simulator", "StrategySignals.tsx")
        no_strategy = source.index("signals === null")
        empty = source.index("signals.length === 0")
        assert no_strategy < empty, "the null branch must come first"
        assert source.index("无参考策略") < source.index("本周无策略信号"), "the two states are swapped"

    def test_decision_receipt_is_not_a_single_line(self) -> None:
        """`notification.info`, not `message.info` — `message` is one line.

        Asserting merely that the word "notification" appears would be satisfied
        by the `useApp()` destructuring alone and would pass even if the receipt
        were routed through `message`.
        """
        source = _read("src", "pages", "simulator", "DecisionForm.tsx")
        assert "notification.info(" in source, "a multi-line fill report would be truncated by message"
        assert "message.info(" not in source, "the receipt must not go through the single-line API"

    def test_decision_failure_keeps_the_input(self) -> None:
        """A rejected order usually needs one character changed, not retyping."""
        source = _read("src", "pages", "simulator", "DecisionForm.tsx")
        assert "resetFields" in source
        # The reset happens on the success path only, never in the catch.
        assert source.index("resetFields") < source.index("catch"), "the form is cleared on failure too"

    def test_benchmark_metrics_are_hidden_when_absent(self) -> None:
        """A market-less snapshot must not render a benchmark close of 0.

        Both benchmark tiles carry their own gate — asserting that one gate
        exists would leave the other removable.
        """
        source = _read("src", "pages", "simulator", "SessionDetail.tsx")
        assert source.count("{market && (") == 2, "each benchmark tile needs its own gate"

    def test_detail_switch_does_not_reload_the_session(self) -> None:
        """Switching to compare must not refetch the snapshot."""
        source = _read("src", "pages", "simulator", "SessionDetail.tsx")
        assert "Segmented" in source
        assert "}, [sessionId]);" in source, "no reset keyed on sessionId"

    def test_a_failed_request_is_not_reported_as_empty(self) -> None:
        listing = _read("src", "pages", "simulator", "SessionList.tsx")
        assert "会话列表加载失败" in listing
        assert "failure !== null" in listing, "the empty state would win over the error"

    def test_delete_failure_leaves_the_row_in_place(self) -> None:
        """A delete that failed must not look like one that worked."""
        source = _read("src", "pages", "simulator", "SessionList.tsx")
        # Scoped to the delete handler: the page has other try/catch blocks, and
        # searching the whole file for "catch" finds whichever came first.
        handler = source[source.index("onOk: async () => {") :]
        assert handler.index("await simulatorApi.deleteSession") < handler.index("setRows(previous =>"), (
            "the row must not be removed before the request has succeeded"
        )
        assert handler.index("setRows(previous =>") < handler.index("catch"), (
            "the removal belongs on the success path, not in the failure path"
        )

    def test_unknown_session_status_still_renders_a_label(self) -> None:
        source = _read("src", "pages", "simulator", "SessionList.tsx")
        assert "STATUS_LABELS[status] ?? status" in source, "an unknown status would render blank"

    def test_create_sends_no_empty_parameters(self) -> None:
        """Blank optional fields must be omitted so the backend owns the default."""
        source = _read("src", "api", "simulator.ts")
        assert "value !== undefined && value !== ''" in source

    def test_preview_covers_its_error_states(self) -> None:
        """Both failure branches render an Alert, not just a toast."""
        source = _read("src", "pages", "reports", "Preview.tsx")
        assert "报告详情加载失败" in source, "a failed load would render nothing"
        assert "failure !== null" in source
        assert "不存在" in source, "an unregistered report needs its own branch"

    def test_create_failure_leaves_a_persistent_message(self) -> None:
        """A toast is gone in seconds; a failed create is re-read while fixing it."""
        source = _read("src", "pages", "simulator", "CreateSession.tsx")
        assert "会话创建失败" in source, "the error only ever appears as a transient toast"
        assert "failure !== null" in source


class TestStrategiesSection:
    """The strategy section, asserted against the sources that carry it."""

    STRATEGY_PAGES = ("List", "Runs", "Signals", "StrategyNav")

    def _strategy_sources(self) -> list[Path]:
        return sorted((WEB / "src" / "pages" / "strategies").glob("*.tsx"))

    def test_section_is_marked_implemented(self) -> None:
        nav = _read("src", "shell", "navigation.tsx")
        assert (
            "{ key: '/strategies', path: '/strategies', label: '策略', icon: <SlidersOutlined />, implemented: true }"
            in nav
        )

    def test_all_eight_sections_are_now_built(self) -> None:
        """The last placeholder is gone — that is what this change was for.

        Both halves matter: no section may still be flagged unimplemented, and
        each section's path must have an explicit route rather than falling
        through to the placeholder loop.
        """
        nav = _read("src", "shell", "navigation.tsx")
        assert "implemented: false" not in nav, "a section is still a placeholder"

        declared = set(re.findall(r'path="(/[a-z]+)"', _read("src", "shell", "routes.tsx")))
        declared |= set(re.findall(r'path="(/[a-z]+)/', _read("src", "shell", "routes.tsx")))
        missing = sorted(set(re.findall(r"path: '(/[a-z]+)'", nav)) - declared)
        assert missing == [], f"these sections have no explicit route: {missing}"

    def test_routes_are_declared_explicitly(self) -> None:
        routes = _read("src", "shell", "routes.tsx")
        for path in ('path="/strategies"', 'path="/strategies/signals"', 'path="/strategies/signals/:runId"'):
            assert path in routes, f"routes.tsx is missing {path}"

    def test_the_static_signals_route_precedes_the_dynamic_one(self) -> None:
        """A `:runId` route declared first would swallow `/strategies/signals`."""
        routes = _read("src", "shell", "routes.tsx")
        assert routes.index('path="/strategies/signals"') < routes.index('path="/strategies/signals/:runId"')

    def test_pages_are_present(self) -> None:
        names = {path.stem for path in self._strategy_sources()}
        missing = [name for name in self.STRATEGY_PAGES if name not in names]
        assert missing == [], f"pages/strategies is missing {missing}"

    def test_no_new_stylesheet(self) -> None:
        directory = WEB / "src" / "pages" / "strategies"
        sheets = [path.name for path in directory.rglob("*") if path.suffix in {".css", ".less", ".scss"}]
        assert sheets == [], f"styling comes from antd and theme tokens, not {sheets}"

    def test_section_draws_no_charts(self) -> None:
        """It is a table section; a chart library here would be unused weight."""
        offenders = [path.name for path in self._strategy_sources() if "echarts" in path.read_text(encoding="utf-8")]
        assert offenders == [], f"the strategy section has no series to plot: {offenders}"

    def test_submits_through_the_run_api(self) -> None:
        source = _read("src", "pages", "strategies", "List.tsx")
        assert "runsApi.submit('strategy_signals'" in source

    def test_an_empty_universe_is_blocked_before_the_request(self) -> None:
        """`[]` asks for a pool of nothing; the service reads absence as default."""
        source = _read("src", "pages", "strategies", "List.tsx")
        assert "values.universe.length === 0" in source, "no guard for an explicitly empty pool"
        assert source.index("values.universe.length === 0") < source.index("runsApi.submit"), (
            "the guard must run before the request is issued"
        )

    def test_unset_fields_are_sent_as_null_not_omitted_values(self) -> None:
        """The service's defaults decide the date, pool and holding count."""
        source = _read("src", "pages", "strategies", "List.tsx")
        assert "values.as_of ? values.as_of.format('YYYY-MM-DD') : null" in source
        assert "values.top_n ?? null" in source

    def test_strategy_options_come_from_the_backend(self) -> None:
        source = _read("src", "pages", "strategies", "List.tsx")
        # Whitespace-tolerant: the call is chained across lines.
        assert re.search(r"strategiesApi\s*\.\s*list\(\)", source), "the selector must not be a hardcoded list"

    def test_run_list_covers_its_error_state(self) -> None:
        source = _read("src", "pages", "strategies", "Runs.tsx")
        assert "信号运行列表加载失败" in source
        assert "listFailure !== null" in source, "the empty state would win over the error"
        assert "尚无信号生成" in source

    def test_detail_link_is_gated_on_the_run_having_stopped(self) -> None:
        """Not on `status === 'ok'`, and not on the row having orders.

        Gating on `ok` hides a run cancelled after it wrote its signals. Gating
        on `order_count` hides a run that finished and selected nothing — it has
        an answer ("no signals") and no way to reach it.
        """
        source = _read("src", "pages", "strategies", "Runs.tsx")
        assert "isTerminal(row.status as RunStatus) ?" in source
        assert "row.status === 'ok' ?" not in source
        assert "row.order_count ?" not in source

    def test_the_empty_history_offers_a_way_to_start_one(self) -> None:
        """A sentence saying where to go is not an entry point."""
        source = _read("src", "pages", "strategies", "Runs.tsx")
        assert "尚无信号生成" in source
        assert 'to="/strategies"' in source, "the empty state must link to the submit page"

    def test_detail_page_does_not_reorder_the_orders(self) -> None:
        """The engine's order carries meaning — sells before buys, and so on."""
        source = _read("src", "pages", "strategies", "Signals.tsx")
        assert ".sort(" not in source, "the signal rows must render in the order they arrived"

    def test_detail_page_never_regenerates_signals(self) -> None:
        assert "runsApi" not in _read("src", "pages", "strategies", "Signals.tsx")

    def test_detail_page_covers_its_states(self) -> None:
        source = _read("src", "pages", "strategies", "Signals.tsx")
        assert "无信号" in source, "a run with no orders must say so, not render an empty table"
        assert "不存在" in source, "an unregistered run needs its own branch"
        assert "信号加载失败" in source, "a failed load must not render as an empty run"
        assert "}, [runId]);" in source, "no reset keyed on runId"

    def test_detail_labels_a_cancelled_run_by_what_happened(self) -> None:
        source = _read("src", "pages", "strategies", "Signals.tsx")
        assert "{data.status === 'cancelled' ? '取消于 ' : '完成于 '}" in source

    def test_detail_shows_the_date_the_engine_used(self) -> None:
        """Not the submitted one: leaving it blank falls back server-side."""
        source = _read("src", "pages", "strategies", "Signals.tsx")
        assert "data.signal_date" in source

    def test_the_client_uses_the_shared_request_module(self) -> None:
        source = _read("src", "api", "strategies.ts")
        assert "from './http'" in source
        assert "VITE_API_BASE" not in source, "it must not resolve a second base of its own"

    def test_submission_goes_through_the_run_api_not_a_bespoke_client(self) -> None:
        """The section has no write endpoint of its own."""
        source = _read("src", "api", "strategies.ts")
        assert "POST" not in source and "method:" not in source, "submission belongs to runsApi"

    def test_nav_keeps_the_signals_tab_lit_on_a_detail_page(self) -> None:
        source = _read("src", "pages", "strategies", "StrategyNav.tsx")
        assert "location.pathname.startsWith" in source, "the detail path must still highlight its tab"


class TestFrontendContractStillHolds:
    def test_the_shared_request_module_is_unchanged(self) -> None:
        """The simulator client builds on it; it must keep its own base."""
        assert "import.meta.env.VITE_API_BASE || '/api'" in _read("src", "api", "http.ts")

    def test_the_simulator_client_uses_the_shared_module(self) -> None:
        source = _read("src", "api", "simulator.ts")
        assert "from './http'" in source
        assert "VITE_API_BASE" not in source, "it must not resolve a second base of its own"
