"""Southbound Stock Connect flow (港股通) for the A-share vendor.

Eastmoney datacenter report ``RPT_MUTUAL_DEAL_HISTORY`` carries the daily
mutual-market history keyed by ``MUTUAL_TYPE``: 003 = 港股通沪, 004 = 港股通深.
Every row is a dated historical observation, so a past-dated run is served by
filtering ``TRADE_DATE <= as_of`` — no live-snapshot withholding applies.

Field honesty (2026-11 probed): the 深 leg (004, and 002 深股通) publishes
``NET_DEAL_AMT``/``BUY_AMT``/``SELL_AMT``; the 沪 leg (003, 001) stopped
publishing net/buy/sell upstream — those fields come back None and the report
says so rather than inventing a number. Amounts are in millions of CNY;
rendered in 亿 (100M). The rows also carry an ``INDEX_CLOSE_PRICE`` whose
provenance is unverified (the 004 value equals the 002 northbound one), so it
is deliberately not rendered.
"""
import logging

from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.vendors.a_stock.common import em_json

logger = logging.getLogger(__name__)

_MUTUAL_REPORT = "RPT_MUTUAL_DEAL_HISTORY"
_SH_SOUTH = "003"  # 港股通沪
_SZ_SOUTH = "004"  # 港股通深
MILLION_TO_YI = 100.0  # report unit is 百万 (1e6 CNY); 亿 = 1e8


def _south_rows(mutual_type: str, as_of_date: str, look_back_days: int) -> list[dict]:
    """One leg's daily rows inside [as_of - look_back, as_of], oldest first.

    Rows come newest-first with a generous page size; the client-side pass is
    the authoritative anti-lookahead guard (the server-side AND-combined date
    filter is unprobed, so it is not relied on here).
    """
    rows = em_json(
        _MUTUAL_REPORT,
        filter_str=f'(MUTUAL_TYPE="{mutual_type}")',
        columns="TRADE_DATE,DEAL_AMT,NET_DEAL_AMT,BUY_AMT,SELL_AMT",
        page_size=str(look_back_days + 15),
        sort_columns="TRADE_DATE",
        sort_types="-1",
    )
    out = []
    for row in rows:
        day = str(row.get("TRADE_DATE", ""))[:10]
        if not day or day > as_of_date:
            continue
        out.append({"date": day, **{k: row.get(k) for k in
                                    ("DEAL_AMT", "NET_DEAL_AMT", "BUY_AMT", "SELL_AMT")}})
    out.sort(key=lambda r: r["date"])
    return out


def _yi(value) -> str:
    """Millions -> 亿 string; None renders as an explicit dash."""
    return "-" if value is None else f"{value / MILLION_TO_YI:,.2f}"


def get_southbound_flow(as_of_date: str, look_back_days: int = 30) -> str:
    """Render the southbound Stock Connect flow report (港股通沪 + 港股通深)."""
    if look_back_days is None or int(look_back_days) <= 0:
        look_back_days = 30
    look_back_days = int(look_back_days)
    sh = _south_rows(_SH_SOUTH, as_of_date, look_back_days)
    sz = _south_rows(_SZ_SOUTH, as_of_date, look_back_days)
    if not sh and not sz:
        raise NoMarketDataError(
            f"no southbound Stock Connect rows on or before {as_of_date}"
        )
    by_day = {r["date"]: {"sh": r, "sz": r} for r in sh}
    for r in sz:
        by_day.setdefault(r["date"], {})["sz"] = r
        by_day[r["date"]]["sh"] = by_day[r["date"]].get("sh")

    days = sorted(by_day)[-max(look_back_days, 1):]
    shown = days[-15:]
    lines = [
        f"# Southbound Stock Connect flow (港股通), window ending {as_of_date}",
        "",
        "| 日期 | 港股通沪成交额(亿) | 港股通深成交额(亿) | 港股通深净买入(亿) |",
        "| --- | --- | --- | --- |",
    ]
    for day in shown:
        sh_row = by_day[day].get("sh") or {}
        sz_row = by_day[day].get("sz") or {}
        lines.append(
            f"| {day} | {_yi(sh_row.get('DEAL_AMT'))} | {_yi(sz_row.get('DEAL_AMT'))} "
            f"| {_yi(sz_row.get('NET_DEAL_AMT'))} |"
        )
    sz_net = sum(
        r["NET_DEAL_AMT"] for r in sz if r["NET_DEAL_AMT"] is not None
    )
    sh_deal = sum(r["DEAL_AMT"] for r in sh if r["DEAL_AMT"] is not None)
    sz_deal = sum(r["DEAL_AMT"] for r in sz if r["DEAL_AMT"] is not None)
    lines += [
        "",
        f"**Summary over {len(days)} trading days (up to {days[-1] if days else as_of_date}):**",
        f"- 港股通深 cumulative net buy: {_yi(sz_net)} 亿"
        + ("（沪端净买入上游未披露，仅成交额可得）" if sh else ""),
        f"- 港股通沪 cumulative turnover: {_yi(sh_deal)} 亿; 港股通深: {_yi(sz_deal)} 亿",
        "- Net-buy figures cover the 深 leg only; the 沪 leg discontinued publishing "
        "net/buy/sell upstream, so treat the combined southbound net as a lower bound.",
    ]
    return "\n".join(lines)
