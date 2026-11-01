"""Fundamentals for A-shares, in the accounting the company actually reports under.

Yahoo serves Chinese statements re-cut into a US-style taxonomy, sometimes in a
different currency and with periods matched to a foreign fiscal calendar. Sina
republishes the exchange filings verbatim (资产负债表 / 利润表 / 现金流量表, CNY,
报告期 as filed), which is what a Chinese analyst reads. Registry fields (industry,
share count) and the live valuation snapshot come from Eastmoney and Tencent.

Point-in-time discipline is explicit here, because A-share filings are dated by
the period they cover and published weeks later:

* a statement is served only once its statutory disclosure deadline has passed
  (Q1/Q3 +30 days, interim +62, annual +120), so a run dated inside the filing
  gap never reads figures that were not public yet;
* holder trades are dated by their 公告日 (announcement), so they are simply
  filtered on that date;
* the valuation snapshot is a present-day value with no historical vintage, so a
  past-dated run withholds it instead of scaling today's multiples back.
"""

import logging
from datetime import datetime, timedelta
from typing import Annotated

import pandas as pd

from tradingagents.dataflows.date_window import withhold_live_profile
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.vendors.a_stock.common import (
    cn_symbol,
    em_json,
    get_json,
    quote_snapshot,
    require_a_share,
)

logger = logging.getLogger(__name__)

SINA_STATEMENT_URL = (
    "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
)
EASTMONEY_STOCK_URL = "https://push2.eastmoney.com/api/qt/stock/get"

SINA_KINDS = {"lrb": "Income Statement", "fzb": "Balance Sheet", "llb": "Cash Flow"}

# Statutory disclosure deadlines, in days after the period ends: 季报 one month,
# 半年报 two (62 to cover the 8/31 weekend edge), 年报 four. Conservative on
# purpose — a company that filed early simply becomes readable a little late.
_LAG_DAYS = {3: 30, 6: 62, 9: 30, 12: 120}


def _public_periods(periods: list[str], as_of_date: str | None) -> list[str]:
    if not as_of_date:
        return periods
    cutoff = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    keep = []
    for period in periods:
        try:
            end = datetime.strptime(period, "%Y-%m-%d").date()
        except ValueError:
            continue
        if end + timedelta(days=_LAG_DAYS.get(end.month, 120)) <= cutoff:
            keep.append(period)
    return keep


def _sina_report(symbol: str, kind: str, num: int = 12) -> dict[str, dict[str, float | None]]:
    """``{报告期: {科目: 数值}}`` from Sina's republished filings."""
    if kind not in SINA_KINDS:
        raise ValueError(f"unknown statement kind {kind!r}; expected one of {sorted(SINA_KINDS)}")
    cn = cn_symbol(symbol)
    payload = get_json(
        SINA_STATEMENT_URL,
        params={"paperCode": cn, "source": kind, "type": "0", "page": "1", "num": str(num)},
        timeout=(8, 25),
    )
    data = ((payload or {}).get("result") or {}).get("data") or {}
    report_list = data.get("report_list") or {}
    if not isinstance(report_list, dict):
        raise VendorUnavailableError(f"Sina {kind} response for {cn} changed shape (report_list is not an object)")

    out: dict[str, dict[str, float | None]] = {}
    for raw_period, body in report_list.items():
        text = str(raw_period)
        if len(text) != 8 or not text.isdigit():
            continue
        period = f"{text[:4]}-{text[4:6]}-{text[6:]}"
        items: dict[str, float | None] = {}
        for item in (body or {}).get("data") or []:
            title = (item.get("item_title") or "").strip()
            if not title:
                continue
            raw = item.get("item_value")
            if raw in (None, "", "--"):
                continue
            try:
                items[title] = float(str(raw).replace(",", ""))
            except ValueError:
                continue
        if items:
            out[period] = items
    if not out:
        raise NoMarketDataError(symbol, cn, f"Sina returned no {SINA_KINDS[kind].lower()} periods")
    return out


def _statement_frame(symbol: str, kind: str, freq: str, as_of_date: str | None) -> tuple[pd.DataFrame, list[str]]:
    reports = _sina_report(symbol, kind, num=24 if freq.lower() == "annual" else 12)
    periods = _public_periods(sorted(reports, reverse=True), as_of_date)
    if freq.lower() == "annual":
        periods = [p for p in periods if p.endswith("-12-31")]
    if not periods:
        raise NoMarketDataError(
            symbol,
            symbol,
            f"no {SINA_KINDS[kind].lower()} was public on {as_of_date} "
            f"(the filing deadline for the latest period has not passed)",
        )
    frame = pd.DataFrame({p: pd.Series(reports[p], dtype=object) for p in periods})
    frame = frame.sort_index()
    return frame, periods


def _statement(symbol: str, kind: str, freq: str, as_of_date: str | None) -> str:
    label = cn_symbol(symbol)
    frame, periods = _statement_frame(symbol, kind, freq, as_of_date)
    header = f"# {SINA_KINDS[kind]} data for {label} ({freq})\n"
    header += (
        f"# Periods served: {', '.join(periods)} — each was public by the run date "
        f"({as_of_date or 'today'}) under the exchange disclosure deadline\n"
    )
    header += "# Figures are as the company filed them with the exchange: CNY, Chinese GAAP (企业会计准则)\n"
    return header + frame.to_csv()


def get_income_statement(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    as_of_date: Annotated[str, "current date in YYYY-MM-DD format"] = None,
):
    """利润表 (income statement) as filed, in CNY."""
    return _statement(ticker, "lrb", freq, as_of_date)


def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    as_of_date: Annotated[str, "current date in YYYY-MM-DD format"] = None,
):
    """资产负债表 (balance sheet) as filed, in CNY."""
    return _statement(ticker, "fzb", freq, as_of_date)


def get_cashflow(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    as_of_date: Annotated[str, "current date in YYYY-MM-DD format"] = None,
):
    """现金流量表 (cash flow statement) as filed, in CNY."""
    return _statement(ticker, "llb", freq, as_of_date)


def _registry(symbol: str) -> dict:
    """Industry, share count, market cap and listing date (Eastmoney)."""
    prefix, digits = require_a_share(symbol)
    secid = f"{1 if prefix == 'sh' else 0}.{digits}"
    payload = get_json(
        EASTMONEY_STOCK_URL,
        params={
            "fltt": "2",
            "invt": "2",
            "fields": "f57,f58,f84,f85,f127,f116,f117,f189",
            "secid": secid,
        },
        headers={"Referer": "https://quote.eastmoney.com/"},
        timeout=(8, 20),
    )
    data = (payload or {}).get("data") or {}
    if not data:
        raise VendorUnavailableError(f"Eastmoney has no registry record for {secid}")
    return {
        "code": data.get("f57"),
        "name": data.get("f58"),
        "industry": data.get("f127"),
        "total_shares": data.get("f84"),
        "float_shares": data.get("f85"),
        "mcap": data.get("f116"),
        "float_mcap": data.get("f117"),
        "list_date": str(data.get("f189") or ""),
    }


def _key_items(items: dict[str, float | None], wanted: tuple[str, ...]) -> list[str]:
    lines = []
    for name in wanted:
        if name in items:
            value = items[name]
            if value is None:
                continue
            lines.append(f"  {name}: {value:,.2f}")
    return lines


def get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    as_of_date: Annotated[str, "analysis date in YYYY-MM-DD format"] = None,
):
    """Company overview: registry fields, the latest public filing, and (today only) valuation."""
    label = cn_symbol(ticker)
    sections: list[str] = [f"# Company Fundamentals for {label} (A-share)"]

    withheld = withhold_live_profile(as_of_date, label)
    if withheld:
        # Still worth serving the filings: they are dated by period and the
        # deadline rule keeps them honest, unlike the live profile. The wording
        # leads with what IS available because a model reading this result once
        # mistook the withheld note for the whole payload being denied.
        sections.append(
            "\nIMPORTANT — the financial data below IS available, use it: only the "
            "live-quote fields (valuation multiples, market cap, company registry) "
            "are withheld for this past-dated run, because Tencent and Eastmoney "
            "serve present-day values with no historical vintage and serving them "
            f"would put post-{as_of_date} information into this analysis. The "
            "income-statement and balance-sheet figures that follow are complete, "
            "were public by the run date under the exchange disclosure deadline, "
            "and should be treated as valid inputs."
        )
    else:
        registry = _registry(ticker)
        snapshot = quote_snapshot(ticker)
        mcap = snapshot.get("mcap_yi")
        sections.append(
            "\n".join(
                [
                    "",
                    "## Profile (present-day registry fields)",
                    f"  Name: {registry['name']}",
                    f"  Industry (Eastmoney classification): {registry['industry']}",
                    f"  Listed: {_ymd(registry['list_date'])}  Board: {_board(registry['code'] or '')}",
                    f"  Total shares: {_yi(registry['total_shares'])}  Float: {_yi(registry['float_shares'])}",
                    f"  Market cap: {mcap} 亿CNY  Float cap: {snapshot.get('float_mcap_yi')} 亿CNY",
                    "",
                    "## Valuation (live quote, not a historical vintage)",
                    f"  Price: {snapshot['price']} CNY  Change: {snapshot['change_pct']}%",
                    f"  PE(TTM): {snapshot['pe_ttm']}  PE(static): {snapshot['pe_static']}  PB: {snapshot['pb']}",
                    f"  Turnover: {snapshot['turnover_pct']}%  Volume ratio: {snapshot['volume_ratio']}",
                    f"  Limit up/down: {snapshot['limit_up']} / {snapshot['limit_down']}",
                    (
                        "  WARNING: zero volume and unchanged price — suspended or a retired code;"
                        " do not use these multiples"
                        if snapshot.get("is_stale")
                        else ""
                    ),
                ]
            )
        )

    reports = _sina_report(ticker, "lrb", num=8)
    public = _public_periods(sorted(reports, reverse=True), as_of_date)
    if not public:
        sections.append("\n## Filings\n  No income statement was public by this date.")
    else:
        latest = public[0]
        items = reports[latest]
        sections.append(f"\n## Latest public income statement — 报告期 {latest} (as filed, CNY)")
        for line in _key_items(
            items,
            ("营业总收入", "营业收入", "营业总成本", "营业利润", "利润总额", "净利润",
             "归属于母公司所有者的净利润", "基本每股收益", "稀释每股收益"),
        ):
            sections.append(line)
        if len(public) > 1:
            previous = public[1]
            sections.append(f"  (previous period {previous}: 营业总收入 {reports[previous].get('营业总收入', 'n/a')}, "
                            f"净利润 {reports[previous].get('净利润', 'n/a')})")

    balance = _sina_report(ticker, "fzb", num=4)
    balance_public = _public_periods(sorted(balance, reverse=True), as_of_date)
    if balance_public:
        period = balance_public[0]
        sections.append(f"\n## Latest public balance sheet — 报告期 {period} (as filed, CNY)")
        for line in _key_items(
            balance[period],
            ("资产总计", "负债合计", "所有者权益(或股东权益)合计", "归属于母公司所有者权益合计",
             "货币资金", "应收账款", "存货"),
        ):
            sections.append(line)

    sections.append(
        "\nNote: these are the exchange filings republished by Sina (Chinese GAAP), not a "
        "Yahoo re-cut. Periods are gated by the statutory disclosure deadline for a past-dated run."
    )
    return "\n".join(sections)


def _board(code: str) -> str:
    if code.startswith(("688", "689")):
        return "科创板 STAR"
    if code.startswith(("300", "301")):
        return "创业板 ChiNext"
    if code.startswith(("8", "4", "92")):
        return "北交所 BSE"
    return "主板 Main board"


def _yi(value) -> str:
    try:
        return f"{float(value) / 1e8:,.2f} 亿股"
    except (TypeError, ValueError):
        return "n/a"


def _ymd(value) -> str:
    """Eastmoney dates come as YYYYMMDD ints or strings; print them as a date."""
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text or "n/a"


def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol of the company"],
    as_of_date: Annotated[str | None, "analysis date, yyyy-mm-dd"] = None,
):
    """重要股东增减持 (shareholder increases/decreases), dated by 公告日."""
    prefix, digits = require_a_share(ticker)
    filters = f'(SECURITY_CODE="{digits}")'
    if as_of_date:
        filters += f"(NOTICE_DATE<='{as_of_date}')"
    rows = em_json(
        "RPT_SHARE_HOLDER_INCREASE",
        filter_str=filters,
        page_size=50,
        sort_columns="NOTICE_DATE,SECURITY_CODE,HOLDER_NAME,START_DATE,END_DATE",
        sort_types="-1,1,1,1,1",
    )
    if not rows:
        return (
            f"No shareholder increase/decrease disclosures for '{ticker}'"
            + (f" on or before {as_of_date}" if as_of_date else "")
        )

    records = []
    for row in rows:
        records.append(
            {
                "公告日": str(row.get("NOTICE_DATE") or "")[:10],
                "股东": row.get("HOLDER_NAME"),
                "方向": row.get("DIRECTION"),
                "变动股数(万股)": row.get("CHANGE_NUM_SYMBOL"),
                "占总股本(%)": row.get("AFTER_CHANGE_RATE"),
                "变动后持股(万股)": row.get("AFTER_HOLDER_NUM"),
                "变动后占比(%)": row.get("HOLD_RATIO"),
                "均价": row.get("TRADE_AVERAGE_PRICE"),
                "方式": row.get("MARKET"),
                "起始日": str(row.get("START_DATE") or "")[:10],
                "截止日": str(row.get("END_DATE") or "")[:10],
            }
        )
    frame = pd.DataFrame(records)
    header = f"# Shareholder trades for {cn_symbol(ticker)} (重要股东增减持, Eastmoney)\n"
    header += "# Dated by announcement (公告日): a past-dated run only sees what had been disclosed\n"
    return header + frame.to_csv(index=False)
