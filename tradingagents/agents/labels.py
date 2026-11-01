"""Chinese display labels, switched on by ``output_language``.

Two kinds of text live in a report and they must not be conflated:

- **Protocol strings** — ``FINAL TRANSACTION PROPOSAL:`` and the internal
  5-tier rating values (``Overweight`` and friends) — are read by parsers
  elsewhere in the codebase and stay English no matter what.
- **Display labels** — section headings, field labels, the analyst names —
  are for the person reading the report, so they follow ``output_language``.

Ratings keep their English value internally; ``rating_zh`` renders the
canonical value into Chinese at display time, and the parser in
``agents.rating`` accepts the Chinese words back so a Chinese-rendered
report still parses to the same canonical rating.
"""

from __future__ import annotations

import re

from tradingagents.dataflows.config import get_config

# The two protocol lines the models write at the end of their free text,
# per the prompt contract in agents.context. They stay English in the stored
# state (parsers and external greps read them); the report is for a person,
# so ``localize_protocol_lines`` rewrites them at write time.
_RATING_LINE_EN_RE = re.compile(
    r"\*\*\s*Rating\s*\*\*\s*[:：][ \t]*\**[ \t]*([A-Za-z]+)[ \t]*\**", re.IGNORECASE)
_FINAL_PROPOSAL_EN_RE = re.compile(
    r"\*{0,2}[ \t]*FINAL TRANSACTION PROPOSAL[ \t]*[:：][ \t]*\*{0,2}[ \t]*\**[ \t]*([A-Za-z]+)[ \t]*\**",
    re.IGNORECASE)


def is_zh_lang(language: str | None) -> bool:
    """Pure predicate for a language value ("Chinese", "zh-CN", "简体中文"...).

    ``output_language`` is free text the user writes, so match by containment
    rather than a prefix.
    """
    if not language:
        return False
    text = str(language).lower()
    return any(token in text for token in ("zh", "chinese", "中文"))


def is_zh() -> bool:
    """Whether the process config asks for Chinese labels."""
    return is_zh_lang(get_config().get("output_language", "English"))


def is_zh_for(settings: dict | None) -> bool:
    """Whether THIS run asks for Chinese labels.

    A run's own ``output_language`` wins: the process-level config can already
    belong to a different graph (reports are written after a run ends, and the
    next ``TradingAgentsGraph`` may have re-set the config). Fall back to the
    config only when the settings carry no language of their own.
    """
    if settings and settings.get("output_language"):
        return is_zh_lang(settings["output_language"])
    return is_zh()


RATING_ZH: dict[str, str] = {
    "buy": "买入",
    "overweight": "增持",
    "hold": "持有",
    "underweight": "减持",
    "sell": "卖出",
}

ACTION_ZH: dict[str, str] = {
    "buy": "买入",
    "hold": "持有",
    "sell": "卖出",
}


def rating_zh(value: str) -> str:
    """``Overweight`` -> ``增持``; unknown values pass through untouched."""
    return RATING_ZH.get(str(value).lower(), value)


def action_zh(value: str) -> str:
    """``BUY`` -> ``买入``; unknown values pass through untouched."""
    return ACTION_ZH.get(str(value).lower(), value)


def agent_label(key: str, settings: dict | None = None) -> str:
    """One agent's display name in the report language."""
    names_en = {
        "Market Analyst": "市场分析师",
        "Sentiment Analyst": "情绪分析师",
        "News Analyst": "新闻分析师",
        "Fundamentals Analyst": "基本面分析师",
        "Bull Researcher": "多方研究员",
        "Bear Researcher": "空方研究员",
        "Research Manager": "研究经理",
        "Trader": "交易员",
        "Aggressive Analyst": "激进派风控",
        "Conservative Analyst": "保守派风控",
        "Neutral Analyst": "中立派风控",
        "Portfolio Manager": "组合经理",
    }
    if not is_zh_for(settings):
        return key
    return names_en.get(key, key)


def debate_label(role_en: str, settings: dict | None = None) -> str:
    """The prefix stamped on one debate turn, e.g. ``Bull Analyst:``."""
    names_zh = {
        "Bull Analyst": "多方研究员",
        "Bear Analyst": "空方研究员",
        "Aggressive Analyst": "激进派风控",
        "Conservative Analyst": "保守派风控",
        "Neutral Analyst": "中立派风控",
    }
    if not is_zh_for(settings):
        return role_en
    return names_zh.get(role_en, role_en)


def localize_protocol_lines(text: str, settings: dict | None = None) -> str:
    """Rewrite the two model-written protocol lines into Chinese for display.

    The English lines stay in the stored state — the prompt contract in
    ``agents.context`` asks for them and parsers read them; this runs only at
    report write time. The canonical English value is kept in brackets so a
    reader can still match it against the 5-tier scale.
    """
    if not is_zh_for(settings):
        return text
    text = _RATING_LINE_EN_RE.sub(
        lambda m: f"**评级**：{rating_zh(m.group(1))}（{m.group(1)}）", text)
    text = _FINAL_PROPOSAL_EN_RE.sub(
        lambda m: f"最终交易建议：**{action_zh(m.group(1))}（{m.group(1).upper()}）**", text)
    return text
