"""A-share verification snapshot, priced from a source independent of the primary.

The primary A-share price path (``market.py``) reads Tencent's K-line feed. A
verification snapshot that re-read Tencent would be self-verification, so this
module prices from Sina's daily K-line service first and Eastmoney's push2his
K-line as backup — different vendors from the primary — then applies the exact
same Sina adjustment factors and the same re-anchoring rule as the primary
(``apply_factors`` anchored at the last bar in range). A number that disagrees
with the primary is therefore a data error, not an accounting difference.

Volume units differ by vendor and were confirmed live (2026-11-01, skill V3.10
field calibration): Sina reports shares for every board; Eastmoney reports
lots except the STAR Market's 688/689 — same rule as Tencent, so lots are
scaled ×100 to shares, 688/689 pass through.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta

import pandas as pd

from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorError,
    VendorNotApplicableError,
    VendorUnavailableError,
)
from tradingagents.dataflows.vendors.a_stock.common import (
    _http,
    adjustment_factors,
    apply_factors,
    em_gate,
    get_json,
    is_a_share,
    require_quoted,
)
from tradingagents.dataflows.vendors.snapshot_render import render_snapshot

logger = logging.getLogger(__name__)

# Long market closures (National Day, Spring Festival) can gap 10 calendar days.
MAX_STALE_DAYS = 14

_SINA_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData"
)
_EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
# push2his answers with another vendor's data only if asked with a broken ut;
# this is the public web token every eastmoney page ships with.
_EM_UT = "fa5fd1943c7b386f172d6893dbfba10b"

# Sina serves only the most recent ~1023 daily bars and takes no date range.
SINA_MAX_DALEN = 1023
# A snapshot needs ~200 trading rows of history before the analysis date for
# the 200 SMA, plus slack for closures; 320 covers a six-week holiday wall.
MIN_DEPTH_ROWS = 320

_snapshot_cache: dict[tuple, tuple[float, pd.DataFrame, str]] = {}
_SNAPSHOT_TTL = 600  # seconds; the analyst team may request the same window twice
_cache_lock = threading.Lock()


def _depth_rows(as_of_date: str) -> int:
    """Bars to request so the 200 SMA has history on or before the analysis date.

    Sina returns the most recent N bars regardless of the analysis date, so a
    backtest far in the past needs every bar since then plus the indicator
    warm-up; capped at what Sina can serve.
    """
    gap = (datetime.now() - datetime.strptime(as_of_date, "%Y-%m-%d")).days
    return min(SINA_MAX_DALEN, max(MIN_DEPTH_ROWS, gap + 250))


def _fetch_sina_daily(prefix: str, digits: str, as_of_date: str) -> pd.DataFrame:
    """Unadjusted daily bars from Sina, volume in shares for every board."""
    cn = prefix + digits
    params = {
        "symbol": cn,
        "scale": "240",  # daily
        "ma": "no",
        "datalen": str(_depth_rows(as_of_date)),
    }
    response = _http(_SINA_KLINE_URL, params=params,
                     headers={"Referer": "https://finance.sina.com.cn/"})
    text = response.text
    bracket = text.find("[")
    if bracket < 0:
        raise VendorUnavailableError(f"Sina K-line for {cn} answered without a JSON array")
    try:
        rows = json.JSONDecoder().raw_decode(text[bracket:])[0]
    except ValueError as exc:
        raise VendorUnavailableError(f"Sina K-line for {cn} did not decode: {exc}") from exc
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("day"):
            continue
        try:
            out.append({
                "Date": str(row["day"])[:10],
                "Open": float(row["open"]),
                "Close": float(row["close"]),
                "High": float(row["high"]),
                "Low": float(row["low"]),
                # Sina reports shares, no scaling needed (probed: 588200/600519).
                "Volume": float(row["volume"]),
            })
        except (KeyError, TypeError, ValueError) as exc:
            raise VendorUnavailableError(f"Sina K-line for {cn} has a malformed row: {exc}") from exc
    if not out:
        raise NoMarketDataError(cn, cn, "Sina K-line returned no rows")
    return pd.DataFrame(out)


def _fetch_em_daily(prefix: str, digits: str, as_of_date: str) -> pd.DataFrame:
    """Unadjusted daily bars from Eastmoney push2his, volume scaled to shares.

    Eastmoney takes a real date range, so the window is cut at the analysis
    date and the warm-up goes back far enough for the 200 SMA.
    """
    secid = ("1." if prefix == "sh" else "0.") + digits
    beg = (datetime.strptime(as_of_date, "%Y-%m-%d") - timedelta(days=1000)).strftime("%Y%m%d")
    end = datetime.strptime(as_of_date, "%Y-%m-%d").strftime("%Y%m%d")
    params = {
        "secid": secid,
        "klt": "101",  # daily
        "fqt": "0",    # unadjusted; factors are applied below, same as Sina
        "beg": beg,
        "end": end,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "ut": _EM_UT,
    }
    with em_gate():
        payload = get_json(_EM_KLINE_URL, params=params, timeout=(8, 25))
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data.get("klines"):
        raise VendorUnavailableError(f"Eastmoney K-line returned nothing for {secid}")
    # push2his has a history of silently answering with a different
    # instrument; a snapshot quoted as ground truth must not inherit that.
    if str(data.get("code") or "") != digits:
        raise VendorUnavailableError(
            f"Eastmoney returned code {data.get('code')!r} for {secid}; discarding as a mismatch"
        )
    if not data.get("name"):
        raise VendorUnavailableError(f"Eastmoney returned no instrument name for {secid}")
    out = []
    for line in data["klines"]:
        parts = str(line).split(",")
        if len(parts) < 6:
            continue
        try:
            out.append({
                "Date": parts[0][:10],
                "Open": float(parts[1]),
                "Close": float(parts[2]),
                "High": float(parts[3]),
                "Low": float(parts[4]),
                # Eastmoney reports lots outside the STAR Market, in shares on it.
                "Volume": float(parts[5]) * (1.0 if digits.startswith(("688", "689")) else 100.0),
            })
        except (TypeError, ValueError) as exc:
            raise VendorUnavailableError(f"Eastmoney K-line for {secid} has a malformed row: {exc}") from exc
    if not out:
        raise NoMarketDataError(secid, secid, "Eastmoney K-line returned no rows")
    return pd.DataFrame(out)


def _verified_rows(symbol: str, as_of_date: str) -> tuple[pd.DataFrame, str]:
    """Adjusted OHLCV on or before as_of_date, plus a source note for the report."""
    prefix, digits = require_quoted(symbol)
    cn = prefix + digits

    key = (cn, as_of_date)
    with _cache_lock:
        hit = _snapshot_cache.get(key)
        if hit and (time.time() - hit[0]) < _SNAPSHOT_TTL:
            return hit[1].copy(), hit[2]

    errors: list[Exception] = []
    frame = None
    source = None
    for fetcher, name in ((_fetch_sina_daily, "Sina"), (_fetch_em_daily, "Eastmoney")):
        try:
            frame = fetcher(prefix, digits, as_of_date)
            source = name
            break
        except VendorError as exc:
            errors.append(exc)
    if frame is None:
        # A source that answered "no such rows" knows the instrument; a source
        # that failed outright does not. Prefer that verdict when both spoke.
        no_data = [e for e in errors if isinstance(e, NoMarketDataError)]
        if no_data:
            raise no_data[0]
        raise VendorUnavailableError(
            "no A-share snapshot source answered: " + "; ".join(str(e) for e in errors)
        )

    df = frame.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    # Defensive cutoff: a verification path must not trust its input to be
    # pre-filtered, and Eastmoney's beg/end have surprised before.
    df = df[df["Date"] <= pd.to_datetime(as_of_date)].sort_values("Date").reset_index(drop=True)
    if df.empty:
        raise NoMarketDataError(symbol, cn, f"no price rows on or before {as_of_date}")

    latest = df["Date"].iloc[-1]
    stale_days = (pd.to_datetime(as_of_date).normalize() - latest.normalize()).days
    if stale_days > MAX_STALE_DAYS:
        raise NoMarketDataError(
            symbol, cn,
            f"latest bar is {latest.date()}, {stale_days} days before the requested {as_of_date} "
            "(suspended, delisted, or the range predates listing)",
        )

    factors = adjustment_factors(symbol)
    df, adjusted = apply_factors(df, factors, latest.strftime("%Y-%m-%d"))
    if adjusted and factors:
        basis = (
            "forward-adjusted (Sina factors, re-anchored at the last bar in range; "
            f"last factor event {factors[-1][0]})"
        )
    else:
        basis = "NOT adjusted for splits or dividends (no factor series available)"
    note = f"{source} daily K-line (independent of the Tencent primary); prices {basis}"

    with _cache_lock:
        _snapshot_cache[key] = (time.time(), df.copy(), note)
    return df, note


def build_verified_market_snapshot(
    symbol: str,
    as_of_date: str,
    look_back_days: int = 30,
    indicators=None,
) -> str:
    """Render the A-share verification snapshot; same shape as the Yahoo one."""
    datetime.strptime(as_of_date, "%Y-%m-%d")
    if not is_a_share(symbol):
        # NotApplicable, not Unavailable: a US ticker reaching this vendor is a
        # routing fact, and reporting it as a vendor failure would poison the
        # chain's verdict for symbols that genuinely have no data anywhere.
        raise VendorNotApplicableError(
            f"{symbol!r} is not a Shanghai/Shenzhen/Beijing listed code; "
            "the a_stock snapshot does not cover it"
        )
    df, source_note = _verified_rows(symbol, as_of_date)
    return render_snapshot(df, symbol, as_of_date, look_back_days, indicators, source_note)
