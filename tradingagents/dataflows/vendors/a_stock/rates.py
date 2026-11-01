"""China onshore rates for the A-share vendor: LPR and the CGB yield curve.

Two sources, both dated historical series, so a past-dated run is served by
filtering observations to ``<= as_of`` — no live-snapshot withholding applies.

- LPR (loan prime rate): Eastmoney datacenter report ``RPTA_WEB_RATE``. Rows
  before 2013-10-25 carry only the legacy benchmark ``RATE_1``/``RATE_2``
  (never rendered as LPR); between 2013-10-25 and 2019-08 only the 1Y tenor
  exists; monthly quotes since. The report is capped at 500 rows per page, so
  the server-side ``TRADE_DATE<=`` filter (probed to work) keeps old vintages
  reachable; the client-side pass is the authoritative guard.
- CGB curve (中债国债收益率曲线): chinabond.com.cn official history endpoint.
  Returns an HTML page whose last <table> holds the curve; parsed with regex
  (``pandas.read_html`` would drag in an undeclared lxml dependency). One
  request silently answers 0 rows beyond ~365 days, so windows are sliced at
  360 days. A ≥7-day slice answering 0 rows means the endpoint's contract
  changed and raises rather than inventing an empty report.

Partial degradation: if chinabond fails, the LPR section is still returned
with an explicit "curve unavailable" note — better than a whole-tool outage.
"""
import logging
import re
from datetime import datetime, timedelta

from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.vendors.a_stock.common import _http, em_json

logger = logging.getLogger(__name__)

LPR_REPORT = "RPTA_WEB_RATE"
LPR_TENORS_SHOWN = 12

CHINABOND_URL = "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery"
TREASURY_QX_ID = "hzsylqx"  # 中债国债收益率曲线
CHINABOND_SLICE_DAYS = 360
_EXPECTED_HEADER = ["曲线名称", "日期", "3月", "6月", "1年", "3年", "5年", "7年", "10年", "30年"]


def _lpr_rows(as_of_date: str, periods: int) -> list[dict]:
    """The last ``periods`` LPR quotes on or before ``as_of_date``, oldest first."""
    rows = em_json(
        LPR_REPORT,
        filter_str=f"(TRADE_DATE<='{as_of_date}')",
        columns="TRADE_DATE,LPR1Y,LPR5Y",
        page_size="500",
        sort_columns="TRADE_DATE",
        sort_types="-1",
    )
    out = []
    for row in rows:
        day = str(row.get("TRADE_DATE", ""))[:10]
        lpr_1y = row.get("LPR1Y")
        if not day or day > as_of_date or lpr_1y is None:
            continue
        out.append({"date": day, "lpr_1y": float(lpr_1y), "lpr_5y": row.get("LPR5Y")})
    if not out:
        raise NoMarketDataError(f"no LPR quotes on or before {as_of_date}")
    return sorted(out, key=lambda r: r["date"])[-periods:]


def _chinabond_slice(start: str, end: str) -> list[tuple[str, float | None, float | None]]:
    """One ≤360-day slice -> [(date, 1y %, 10y %)], oldest first."""
    response = _http(
        CHINABOND_URL,
        params={"startDate": start, "endDate": end, "gjqx": 0,
                "qxId": TREASURY_QX_ID, "locale": "cn_ZH"},
        headers={"Referer": "https://yield.chinabond.com.cn/"},
        timeout=(8, 30),
    )
    html = re.sub(r"<!--.*?-->", "", response.content.decode("utf-8", "replace"), flags=re.S)
    tables = html.split("<table")
    if len(tables) < 2:
        raise VendorUnavailableError("chinabond answered without a data table")
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tables[-1], re.S)
    parsed: list[tuple[str, float | None, float | None]] = []
    header_checked = False
    for row in rows:
        cells = [re.sub(r"<[^>]+>|\s+", "", c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if not cells:
            continue
        if not header_checked:
            if cells != _EXPECTED_HEADER:
                raise VendorUnavailableError(
                    f"chinabond table header drifted: {cells[:4]}..."
                )
            header_checked = True
            continue
        day = cells[1]
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            continue

        def _num(text: str) -> float | None:
            try:
                return float(text)
            except ValueError:
                return None

        parsed.append((day, _num(cells[4]), _num(cells[8])))
    if not header_checked:
        raise VendorUnavailableError("chinabond table header row missing")
    return parsed


def _cgb_rows(as_of_date: str, look_back_days: int) -> list[tuple[str, float | None, float | None]]:
    """CGB 1Y/10Y yields inside [as_of - look_back, as_of], oldest first."""
    end = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    start = end - timedelta(days=look_back_days)
    out: list[tuple[str, float | None, float | None]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=CHINABOND_SLICE_DAYS - 1), end)
        out.extend(
            (day, y1, y10)
            for day, y1, y10 in _chinabond_slice(cursor.isoformat(), stop.isoformat())
            if start.isoformat() <= day <= as_of_date
        )
        cursor = stop + timedelta(days=1)
    if not out and (end - start).days >= 7:
        raise VendorUnavailableError(
            f"chinabond returned 0 rows for a >=7-day window ({start}..{end})"
        )
    # The endpoint answers newest-first within a slice.
    return sorted(out, key=lambda r: r[0])


def get_china_rates(as_of_date: str, look_back_days: int = 90) -> str:
    """Render the China rates report: LPR quotes plus the CGB 1Y/10Y curve."""
    if look_back_days is None or int(look_back_days) <= 0:
        look_back_days = 90
    look_back_days = int(look_back_days)

    lines = [f"# China onshore rates, window ending {as_of_date}", ""]

    lpr = _lpr_rows(as_of_date, LPR_TENORS_SHOWN)
    latest, previous = lpr[-1], (lpr[-2] if len(lpr) > 1 else None)
    lines += [
        "## LPR (loan prime rate, monthly quotes)",
        "",
        "| 报价日 | 1年期(%) | 5年期(%) |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| {r['date']} | {r['lpr_1y']:.2f} | "
        + (f"{float(r['lpr_5y']):.2f}" if r["lpr_5y"] is not None else "-")
        + " |"
        for r in lpr
    ]
    lpr_note = f"LPR 1Y {latest['lpr_1y']:.2f}%"
    if previous and previous["lpr_1y"] != latest["lpr_1y"]:
        lpr_note += f"（上次报价 {previous['date']} 为 {previous['lpr_1y']:.2f}%，已变动）"
    lines += ["", f"**Summary:** {lpr_note}; 5Y "
              + (f"{float(latest['lpr_5y']):.2f}%" if latest["lpr_5y"] is not None else "-")
              + f" as of {latest['date']}.", ""]

    try:
        cgb = _cgb_rows(as_of_date, look_back_days)
    except VendorUnavailableError as exc:
        logger.warning("CGB curve unavailable: %s", exc)
        lines.append(
            f"## CGB yield curve (中债国债收益率曲线)\n\n"
            f"UNAVAILABLE for this window: {exc} LPR above is still valid; "
            "do not fabricate curve values."
        )
        return "\n".join(lines)
    if not cgb:
        lines.append(
            f"## CGB yield curve (中债国债收益率曲线)\n\n"
            f"No observations in the {look_back_days}-day window up to {as_of_date} "
            "(holiday span or non-trading days)."
        )
        return "\n".join(lines)

    shown = cgb[-15:]
    lines += [
        "## CGB yield curve (中债国债收益率曲线, 1Y/10Y)",
        "",
        "| 日期 | 1年(%) | 10年(%) | 10Y-1Y利差(bp) |",
        "| --- | --- | --- | --- |",
    ]
    for day, y1, y10 in shown:
        if y1 is None or y10 is None:
            lines.append(f"| {day} | {_fmt(y1)} | {_fmt(y10)} | - |")
        else:
            lines.append(f"| {day} | {y1:.3f} | {y10:.3f} | {(y10 - y1) * 100:.1f} |")
    last_day, last_1y, last_10y = cgb[-1]
    summary = f"CGB 1Y {_fmt(last_1y)}% / 10Y {_fmt(last_10y)}% as of {last_day}"
    if last_1y is not None and last_10y is not None:
        summary += f"; 10Y-1Y spread {(last_10y - last_1y) * 100:.0f}bp"
    lines += ["", f"**Summary:** {summary}."]
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"
