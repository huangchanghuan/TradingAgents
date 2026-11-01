"""Prices and technical indicators for A-shares, from Tencent's K-line feed.

Two differences from the Yahoo path matter for the rest of the graph:

* Prices are forward-adjusted by ratio and re-anchored at the analysis date, so
  an ex-dividend day no longer reads as a -30% crash, and a run dated 2023 sees
  2023's price level rather than today's rescaled one (Tencent's own `qfq` is
  anchored at today, and its arithmetic style can even go negative on old bars).
* Volume is reported in shares for every board. Tencent reports 科创板 in
  shares and the rest in lots, which silently made 688xxx look 100x thinner.
"""

import logging
from datetime import datetime
from typing import Annotated

import pandas as pd
from dateutil.relativedelta import relativedelta
from stockstats import wrap

from tradingagents.dataflows.errors import NoMarketDataError, VendorError
from tradingagents.dataflows.vendors.a_stock.common import (
    adjustment_factors,
    apply_factors,
    daily_bars,
    require_quoted,
)
from tradingagents.dataflows.vendors.yahoo.common import price_text

logger = logging.getLogger(__name__)

# Long market closures (National Day, Spring Festival) can gap 10 calendar days.
MAX_STALE_DAYS = 14


def _load(symbol: str, start_date: str, end_date: str) -> tuple[pd.DataFrame, bool]:
    """Adjusted bars for the window, plus whether the adjustment applied."""
    require_quoted(symbol)
    raw = daily_bars(symbol, start_date, end_date)
    frame = raw.copy()
    anchor = frame["Date"].iloc[-1]
    frame, adjusted = apply_factors(frame, adjustment_factors(symbol), anchor)
    for column in ("Open", "High", "Low", "Close"):
        frame[column] = frame[column].astype(float)
    return frame, adjusted


def get_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
):
    """Daily OHLCV for a Shanghai/Shenzhen/Beijing listed share."""
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    frame, adjusted = _load(symbol, start_date, end_date)

    latest = pd.to_datetime(frame["Date"].iloc[-1]).normalize()
    stale_days = (pd.to_datetime(end_date).normalize() - latest).days
    if stale_days > MAX_STALE_DAYS:
        raise NoMarketDataError(
            symbol,
            symbol,
            f"latest bar is {latest.date()}, {stale_days} days before the requested {end_date} "
            "(suspended, delisted, or the range predates listing)",
        )

    frame = frame[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
    # Adj Close carries the same series: the frame is already adjusted, and a
    # second column labelled "unadjusted" would only invite the wrong one to be used.
    frame["Adj Close"] = frame["Close"]
    for column in ("Open", "High", "Low", "Close", "Adj Close"):
        frame[column] = frame[column].map(price_text, na_action="ignore")
    frame["Volume"] = frame["Volume"].map(lambda v: str(int(round(float(v)))))

    basis = (
        "forward-adjusted (ratio, re-anchored at the last bar in range)"
        if adjusted
        else "NOT adjusted for splits or dividends (no factor series available)"
    )
    header = f"# Stock data for {symbol} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(frame)}\n"
    header += f"# Prices: {basis}; currency CNY; Volume in shares\n\n"
    return header + frame.to_csv(index=False)


def get_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    as_of_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    """One stockstats indicator over the look-back window, as a dated table."""
    as_of_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    before = as_of_dt - relativedelta(days=int(look_back_days))
    # Indicators need history: a 50-day average read from a 30-day window is
    # empty, so load a year ahead of the requested start and cut only the report.
    load_start = (before - relativedelta(days=400)).strftime("%Y-%m-%d")
    frame, _ = _load(symbol, load_start, as_of_date)

    try:
        stats = wrap(frame.copy())
        stats[indicator]  # stockstats computes lazily on first access
    except Exception as exc:
        raise ValueError(
            f"Indicator {indicator!r} is not supported by stockstats: {exc}"
        ) from exc

    stats["Date"] = pd.to_datetime(stats["Date"]).dt.strftime("%Y-%m-%d")
    values = {}
    for _, row in stats.iterrows():
        value = row.get(indicator)
        values[row["Date"]] = "N/A" if pd.isna(value) else str(value)

    lines = []
    current = as_of_dt
    while current >= before:
        day = current.strftime("%Y-%m-%d")
        lines.append(f"{day}: {values.get(day, 'N/A: Not a trading day (weekend or holiday)')}")
        current -= relativedelta(days=1)

    return (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {as_of_date}:\n\n"
        + "\n".join(lines)
        + "\n\n"
        + _describe(indicator)
    )


_DESCRIPTIONS = {
    "close_50_sma": "50 SMA: medium-term trend, acts as dynamic support/resistance; lags price.",
    "close_200_sma": "200 SMA: long-term trend benchmark; best for strategic confirmation, not entries.",
    "close_10_ema": "10 EMA: responsive short-term average; prone to noise in choppy markets.",
    "macd": "MACD: momentum from the difference of two EMAs; watch crossovers and divergence.",
    "macds": "MACD Signal: EMA smoothing of MACD; crossovers with MACD line mark shifts.",
    "macdh": "MACD Histogram: momentum strength and early divergence; volatile, use with filters.",
    "rsi": "RSI: overbought/oversold momentum gauge; 30/70 are the conventional lines.",
    "boll": "Bollinger middle band (20 SMA): the rolling mean price reverts to.",
    "boll_ub": "Bollinger upper band: often marks overbought/breakout territory.",
    "boll_lb": "Bollinger lower band: often marks oversold/breakdown territory.",
    "atr": "ATR: average true range, a volatility measure for stops and position sizing.",
    "vwma": "VWMA: volume-weighted moving average; confirms price moves with participation.",
    "mfi": "MFI: money flow index, RSI weighted by volume; >80 overbought, <20 oversold.",
}


def _describe(indicator: str) -> str:
    return _DESCRIPTIONS.get(indicator, f"No description available for {indicator}.")


def latest_close(symbol: str, as_of_date: str) -> float | None:
    """Last settled close on or before ``as_of_date``, for price grounding."""
    try:
        start = (datetime.strptime(as_of_date, "%Y-%m-%d") - relativedelta(days=30)).strftime("%Y-%m-%d")
        frame, _ = _load(symbol, start, as_of_date)
    except (VendorError, ValueError):
        return None
    if frame.empty:
        return None
    try:
        return float(frame["Close"].iloc[-1])
    except (TypeError, ValueError):
        return None
