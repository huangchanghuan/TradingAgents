"""Reusable report-tree writer shared by the CLI and the programmatic API.

Writes a run's per-section markdown (analysts, research, trading, risk,
portfolio) plus a consolidated ``complete_report.md`` under ``save_path``. The
CLI and ``TradingAgentsGraph.save_reports`` both call this, so a headless / API
run produces the same on-disk report tree a CLI run does.
"""

from datetime import datetime
from pathlib import Path

from tradingagents.agents.rating import run_rating


def analyst_names(keys) -> list[str]:
    """The analysts as users select them: the sentiment analyst's key is "social"."""
    return ["sentiment" if key == "social" else key for key in keys or []]


def _header(ticker: str, final_state: dict, settings: dict | None) -> str:
    """The report's title and what produced it: analysis date, version, models, analysts, vendors."""
    from tradingagents.agents.labels import is_zh_for, rating_zh

    zh = is_zh_for(settings)
    colon = "：" if zh else ": "
    lines = [f"# {'交易分析报告' if zh else 'Trading Analysis Report'}{colon}{ticker}", ""]
    if final_state.get("trade_date"):
        lines.append(f"- {'分析日期' if zh else 'Analysis date'}{colon}{final_state['trade_date']}")
    if final_state.get("final_trade_decision"):
        rating = run_rating(final_state)
        shown = f"{rating_zh(rating)}（{rating}）" if zh else rating
        lines.append(f"- {'评级' if zh else 'Rating'}{colon}{shown}")
    lines.append(f"- {'生成时间' if zh else 'Generated'}{colon}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if settings:
        s = settings.get
        deep_provider = s("deep_think_provider") or s("llm_provider", "?")
        quick_provider = s("quick_think_provider") or s("llm_provider", "?")
        if deep_provider == quick_provider:
            models = f"{deep_provider}, deep {s('deep_think_llm', '?')}, quick {s('quick_think_llm', '?')}"
        else:   # each tier on its own provider (#1440)
            models = (f"deep {deep_provider} {s('deep_think_llm', '?')}, "
                      f"quick {quick_provider} {s('quick_think_llm', '?')}")
        lines.append(f"- TradingAgents {s('version', '?')}{colon}{models}")
        if zh:
            rounds = (f"研究辩论 {s('max_debate_rounds', '?')} 轮，"
                      f"风险讨论 {s('max_risk_discuss_rounds', '?')} 轮")
            lines.append(f"- 分析师：{'、'.join(analyst_names(s('analysts')))}；{rounds}")
        else:
            lines.append(f"- Analysts: {', '.join(analyst_names(s('analysts')))}; "
                         f"research debate rounds {s('max_debate_rounds', '?')}, "
                         f"risk debate rounds {s('max_risk_discuss_rounds', '?')}")
        vendors = {**(s("data_vendors") or {}), **(s("tool_vendors") or {})}
        if vendors:
            joiner = "，" if zh else ", "
            lines.append(f"- {'数据源' if zh else 'Data vendors'}{colon}"
                         + joiner.join(f"{k} {v}" for k, v in vendors.items()))
    if final_state.get("memory_note"):
        lines.append(f"- {'记忆日志' if zh else 'Memory log'}{colon}{final_state['memory_note']}")
    return "\n".join(lines) + "\n\n"


# The report's sections in order: (heading, folder, [(agent, file, state path)]).
# Headings and agent names are display labels (see agents.labels); the folder
# and filename parts are paths other tools may reference and stay English.
_SECTIONS = (
    (("一、分析师团队报告", "I. Analyst Team Reports"), "1_analysts", (
        ("Market Analyst", "market.md", ("market_report",)),
        ("Sentiment Analyst", "sentiment.md", ("sentiment_report",)),
        ("News Analyst", "news.md", ("news_report",)),
        ("Fundamentals Analyst", "fundamentals.md", ("fundamentals_report",)),
    )),
    (("二、研究团队决策", "II. Research Team Decision"), "2_research", (
        ("Bull Researcher", "bull.md", ("investment_debate_state", "bull_history")),
        ("Bear Researcher", "bear.md", ("investment_debate_state", "bear_history")),
        ("Research Manager", "manager.md", ("investment_plan",)),
    )),
    (("三、交易团队计划", "III. Trading Team Plan"), "3_trading", (
        ("Trader", "trader.md", ("trader_investment_plan",)),
    )),
    (("四、风险管理团队决策", "IV. Risk Management Team Decision"), "4_risk", (
        ("Aggressive Analyst", "aggressive.md", ("risk_debate_state", "aggressive_history")),
        ("Conservative Analyst", "conservative.md", ("risk_debate_state", "conservative_history")),
        ("Neutral Analyst", "neutral.md", ("risk_debate_state", "neutral_history")),
    )),
    (("五、组合经理决策", "V. Portfolio Manager Decision"), "5_portfolio", (
        ("Portfolio Manager", "decision.md", ("final_trade_decision",)),
    )),
)


def _report_parts(final_state: dict, settings: dict | None = None) -> list[tuple[str, str, list[tuple[str, str, str]]]]:
    """The sections with something to say: (heading, folder, [(agent, file, text)])."""
    from tradingagents.agents.labels import agent_label, is_zh_for, localize_protocol_lines

    parts = []
    for headings, folder, agents in _SECTIONS:
        heading = headings[0] if is_zh_for(settings) else headings[1]
        written = []
        for agent, filename, path in agents:
            value = final_state
            for key in path:
                value = (value or {}).get(key)
            if value:
                written.append((agent_label(agent, settings), filename,
                                localize_protocol_lines(value, settings)
                                if is_zh_for(settings) else value))
        if written:
            parts.append((heading, folder, written))
    return parts


def write_report_tree(final_state: dict, ticker: str, save_path, settings: dict | None = None,
                      html: bool = True) -> Path:
    """Save a completed run's reports to ``save_path``; return the complete-report path.

    ``settings`` (``TradingAgentsGraph.run_settings()``) adds what produced the run
    to the report's header. ``html`` also writes ``complete_report.html``, one
    self-contained page of the same report (#1419).
    Reusing a directory replaces the generated report files, removing sections
    absent from this run and disabled HTML. Other files and directories are kept.
    """
    save_path = Path(save_path)
    save_path.mkdir(parents=True, exist_ok=True)
    parts = _report_parts(final_state, settings)
    sections = []
    for heading, folder, written in parts:
        (save_path / folder).mkdir(exist_ok=True)
        for _agent, filename, text in written:
            (save_path / folder / filename).write_text(text, encoding="utf-8")
        content = "\n\n".join(f"### {agent}\n{text}" for agent, _filename, text in written)
        sections.append(f"## {heading}\n\n{content}")

    (save_path / "complete_report.md").write_text(
        _header(ticker, final_state, settings) + "\n\n".join(sections), encoding="utf-8"
    )
    if html:
        from tradingagents.report_html import render_report

        (save_path / "complete_report.html").write_text(
            render_report(ticker, final_state, settings, parts), encoding="utf-8"
        )
    else:
        (save_path / "complete_report.html").unlink(missing_ok=True)

    # Reconcile only the filenames owned by this writer; never clear the tree,
    # since callers may keep notes or other artifacts alongside the reports.
    current_files = {
        (folder, filename)
        for _heading, folder, written in parts
        for _agent, filename, _text in written
    }
    for _heading, folder, agents in _SECTIONS:
        for _agent, filename, _path in agents:
            if (folder, filename) not in current_files:
                (save_path / folder / filename).unlink(missing_ok=True)
    return save_path / "complete_report.md"
