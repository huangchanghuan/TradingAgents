"""Report parity: the shared writer produces the report tree for the CLI and the
programmatic API alike (#1037)."""

import re
from types import SimpleNamespace

import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import write_report_tree


def _state():
    return {
        "market_report": "MKT",
        "news_report": "NEWS",
        "investment_debate_state": {"bull_history": "BULL"},
        "investment_plan": "RM PLAN",
        "trader_investment_plan": "TRADE",
        "risk_debate_state": {"neutral_history": "NEUTRAL"},
        "final_trade_decision": "PM DECISION",
    }


SETTINGS = {"version": "0.5.2", "llm_provider": "openai", "deep_think_llm": "gpt-6-sol",
            "quick_think_llm": "gpt-6-luna", "analysts": ["market", "news"], "max_debate_rounds": 1,
            "max_risk_discuss_rounds": 2, "data_vendors": {"core_stock_apis": "yfinance"}}


@pytest.mark.unit
def test_write_report_tree_creates_files(tmp_path):
    out = write_report_tree(_state(), "AAPL", tmp_path)
    assert out.name == "complete_report.md"
    assert (tmp_path / "1_analysts" / "market.md").read_text() == "MKT"
    assert (tmp_path / "1_analysts" / "news.md").read_text() == "NEWS"
    assert (tmp_path / "2_research" / "manager.md").read_text() == "RM PLAN"
    assert (tmp_path / "3_trading" / "trader.md").read_text() == "TRADE"
    assert (tmp_path / "5_portfolio" / "decision.md").read_text() == "PM DECISION"
    complete = out.read_text()
    assert "Trading Analysis Report: AAPL" in complete
    assert "MKT" in complete and "PM DECISION" in complete


@pytest.mark.unit
def test_save_reports_explicit_path(tmp_path):
    graph = SimpleNamespace(run_settings=lambda: SETTINGS)
    out = TradingAgentsGraph.save_reports(graph, _state(), "AAPL", save_path=tmp_path)
    assert (tmp_path / "complete_report.md").exists()
    assert out == tmp_path / "complete_report.md"


@pytest.mark.unit
@pytest.mark.parametrize("empty_value", [None, ""])
def test_save_reports_replaces_previous_run_sections(tmp_path, empty_value):
    graph = SimpleNamespace(run_settings=lambda: SETTINGS)
    first = dict(_state(), trade_date="2026-09-22")
    TradingAgentsGraph.save_reports(graph, first, "AAPL", save_path=tmp_path)
    (tmp_path / "notes.md").write_text("User notes", encoding="utf-8")
    (tmp_path / "1_analysts" / "notes.md").write_text("Analyst notes", encoding="utf-8")

    second = {
        "trade_date": "2026-09-23",
        "market_report": "NEW MARKET",
        "news_report": empty_value,
        "investment_debate_state": {"bull_history": empty_value},
    }
    out = TradingAgentsGraph.save_reports(graph, second, "AAPL", save_path=tmp_path)

    assert (tmp_path / "1_analysts" / "market.md").read_text() == "NEW MARKET"
    for filename in (
        "1_analysts/news.md", "2_research/bull.md", "2_research/manager.md",
        "3_trading/trader.md", "4_risk/neutral.md", "5_portfolio/decision.md",
    ):
        assert not (tmp_path / filename).exists()
    assert (tmp_path / "notes.md").read_text() == "User notes"
    assert (tmp_path / "1_analysts" / "notes.md").read_text() == "Analyst notes"
    assert "NEW MARKET" in out.read_text(encoding="utf-8")
    assert "NEWS" not in out.read_text(encoding="utf-8")
    assert "NEWS" not in (tmp_path / "complete_report.html").read_text(encoding="utf-8")


@pytest.mark.unit
def test_empty_report_removes_previous_sections_and_disabled_html(tmp_path):
    write_report_tree(_state(), "AAPL", tmp_path)
    out = write_report_tree({}, "AAPL", tmp_path, html=False)

    assert list(tmp_path.rglob("*.md")) == [out]
    assert not (tmp_path / "complete_report.html").exists()
    assert "Trading Analysis Report: AAPL" in out.read_text()


@pytest.mark.unit
def test_save_reports_defaults_under_results_dir(tmp_path):
    graph = object.__new__(TradingAgentsGraph)
    graph.config = {"results_dir": str(tmp_path)}
    graph.run_settings = lambda: SETTINGS
    out = graph.save_reports(_state(), "AAPL")
    assert out.exists()
    assert out.parent.parent.name == "reports"  # results_dir/reports/AAPL_<stamp>/...
    assert out.parent.name.startswith("AAPL_")



@pytest.mark.unit
def test_the_report_names_the_analysis_date_and_what_produced_it(tmp_path):
    state = dict(_state(), trade_date="2026-09-23")

    header = write_report_tree(state, "NVDA", tmp_path, settings=SETTINGS).read_text().split("## ")[0]

    assert "Analysis date: 2026-09-23" in header
    assert "TradingAgents 0.5.2" in header
    assert "openai, deep gpt-6-sol, quick gpt-6-luna" in header
    assert "Analysts: market, news" in header
    assert "research debate rounds 1, risk debate rounds 2" in header
    assert "core_stock_apis yfinance" in header


@pytest.mark.unit
def test_run_settings_record_the_run_without_endpoints_or_paths():
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = object.__new__(TradingAgentsGraph)
    graph.selected_analysts = ("market", "news")
    graph.config = {"llm_provider": "openai", "deep_think_llm": "gpt-6-sol", "quick_think_llm": "gpt-6-luna",
                    "max_debate_rounds": 1, "max_risk_discuss_rounds": 1, "output_language": "English",
                    "data_vendors": {"core_stock_apis": "yfinance"}, "tool_vendors": {},
                    "backend_url": "https://user:secret@relay.example/v1", "results_dir": "/home/me/results"}

    settings = graph.run_settings()

    assert settings["analysts"] == ["market", "news"]
    assert settings["deep_think_llm"] == "gpt-6-sol"
    assert "secret" not in str(settings) and "/home/me" not in str(settings)


@pytest.mark.unit
def test_a_partial_settings_dict_still_writes_the_report(tmp_path):
    out = write_report_tree(_state(), "AAPL", tmp_path, settings={"llm_provider": "openai"})
    assert "openai" in out.read_text()


@pytest.mark.unit
def test_run_settings_name_the_version_of_the_running_code():
    import tradingagents
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = object.__new__(TradingAgentsGraph)
    graph.selected_analysts, graph.config = ("market",), {}
    assert graph.run_settings()["version"] == tradingagents.__version__


@pytest.mark.unit
@pytest.mark.parametrize("decision, rating", [
    ("**Rating**: Overweight\n\nAdd on weakness.", "Overweight"),
    ("Add on weakness.", "REVIEW"),           # no readable rating is said, not left out
])
def test_the_report_header_names_the_rating(tmp_path, decision, rating):
    state = dict(_state(), final_trade_decision=decision)

    header = write_report_tree(state, "NVDA", tmp_path, settings=SETTINGS).read_text().split("## ")[0]

    assert f"- Rating: {rating}" in header


@pytest.mark.unit
def test_the_header_names_the_analysts_as_users_select_them(tmp_path):
    settings = {**SETTINGS, "analysts": ["market", "social", "news"]}

    write_report_tree(_state(), "NVDA", tmp_path, settings=settings, html=True)

    markdown = (tmp_path / "complete_report.md").read_text(encoding="utf-8")
    assert "market, sentiment, news" in markdown and "social" not in markdown
    page = (tmp_path / "complete_report.html").read_text(encoding="utf-8")
    fields = re.findall(r'<span class="field">([^<]*)</span>', page)
    assert {"market", "sentiment", "news"} <= set(fields) and "social" not in page


@pytest.mark.unit
@pytest.mark.parametrize("language, expected", [
    ("Chinese", True),
    ("zh-CN", True),
    ("简体中文", True),
    ("English", False),
])
def test_is_zh_accepts_free_text_language_names(language, expected):
    """output_language is user-written free text, so match by containment."""
    import copy

    from tradingagents import default_config
    from tradingagents.agents.labels import is_zh
    from tradingagents.dataflows.config import set_config

    set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))
    set_config({"output_language": language})
    try:
        assert is_zh() is expected
    finally:
        set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))


@pytest.mark.unit
def test_a_chinese_run_renders_chinese_labels_and_still_parses(tmp_path):
    """Display labels follow output_language; the protocol line and the rating
    parser keep working, so a Chinese report is machine-readable too."""
    import copy

    from tradingagents import default_config
    from tradingagents.agents.rating import parse_rating
    from tradingagents.dataflows.config import set_config

    set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))
    set_config({"output_language": "Chinese"})
    try:
        out = write_report_tree(_state(), "510300.SS", tmp_path)
        complete = out.read_text()
        assert "# 交易分析报告：510300.SS" in complete
        assert "## 一、分析师团队报告" in complete
        assert "## 五、组合经理决策" in complete
        assert "Trading Analysis Report" not in complete

        # The PM decision is written in Chinese by the model; the parser must
        # still recover the canonical rating from it.
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating, render_pm_decision

        decision = PortfolioDecision(
            rating=PortfolioRating.OVERWEIGHT,
            executive_summary="分批增持。",
            investment_thesis="资金面证据最干净。",
        )
        rendered = render_pm_decision(decision)
        assert "**评级**：增持" in rendered
        assert parse_rating(rendered) == "Overweight"
    finally:
        set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))


@pytest.mark.unit
def test_protocol_lines_translate_for_display_but_not_in_state(tmp_path):
    """The English protocol lines are a prompt contract parsers read, so the
    state keeps them; the report the person reads shows Chinese instead."""
    import copy

    from tradingagents import default_config
    from tradingagents.dataflows.config import set_config

    analyst_text = ("结论如下。\n\n**Rating**: SELL\n\nFINAL TRANSACTION PROPOSAL: **SELL**\n\n"
                     "**FINAL TRANSACTION PROPOSAL:** **BUY**")  # bold-colon variant
    state = {**_state(), "market_report": analyst_text}

    set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))
    set_config({"output_language": "Chinese"})
    try:
        out = write_report_tree(_state(), "510300.SS", tmp_path)  # smoke: unchanged state
        # Now the localized tree:
        out = write_report_tree(state, "510300.SS", tmp_path)
        market = (tmp_path / "1_analysts" / "market.md").read_text()
        assert "**评级**：卖出（SELL）" in market
        assert "最终交易建议：**卖出（SELL）**" in market
        assert "**Rating**: SELL" not in market
        # Both spellings of the proposal line — the bold-colon one models also
        # write — must be translated, leaving no English protocol line behind.
        assert "FINAL TRANSACTION PROPOSAL" not in market
        assert market.count("最终交易建议") == 2
        # The state the run produced is untouched — parsers still see English.
        assert state["market_report"] == analyst_text
    finally:
        set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))


@pytest.mark.unit
def test_the_reports_language_comes_from_the_runs_settings_not_the_process(tmp_path):
    """Reports are written after a run ends, by which time the process config
    may belong to a different graph; the run's own output_language must win."""
    import copy

    from tradingagents import default_config
    from tradingagents.dataflows.config import set_config

    state = _state()
    settings = dict(SETTINGS)
    process_chinese_settings = {**settings, "output_language": "Chinese"}
    english_settings = {**settings, "output_language": "English"}

    # Process config says Chinese (as if another graph had just set it), but
    # this run asked for English: the report must be English.
    set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))
    set_config({"output_language": "Chinese"})
    try:
        out = write_report_tree(state, "AAPL", tmp_path, settings=english_settings)
        complete = out.read_text()
        assert "Trading Analysis Report: AAPL" in complete
        assert "交易分析报告" not in complete
    finally:
        set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))

    # And the reverse: process config English, run asked for Chinese.
    out = write_report_tree(state, "AAPL", tmp_path, settings=process_chinese_settings)
    complete = out.read_text()
    assert "# 交易分析报告：AAPL" in complete
    assert "Trading Analysis Report" not in complete
