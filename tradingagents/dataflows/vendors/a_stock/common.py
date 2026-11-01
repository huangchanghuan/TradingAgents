"""What every A-share request shares: symbol parsing, the HTTP sessions with
their throttles, the Tencent K-line pager, the Sina adjustment factors and the
Tencent quote snapshot.

Nothing here needs an API key. Endpoint shapes and field indexes follow the
`a-stock-data` skill (V3.10); they are recorded next to the parsing code so a
future field shift is a one-line fix rather than a hunt.

Only Shanghai/Shenzhen/Beijing listed shares are served. Anything else — a US
ticker, a Hong Kong line, an index, a crypto pair — makes the caller raise
`VendorUnavailableError` so the router moves on to the next configured vendor.
"""

import logging
import random
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.net import vendor_reachable

logger = logging.getLogger(__name__)

CN_TZ = timezone(timedelta(hours=8))
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

TENCENT_KLINE_HOSTS = (
    "https://web.ifzq.gtimg.cn",
    "https://proxy.finance.qq.com/ifzqgtimg",
    "https://ifzq.gtimg.cn",
)
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
SINA_FACTOR_URL = "https://finance.sina.com.cn/realstock/company/{symbol}/{kind}.js"
EASTMONEY_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# A K-line request returns at most 640 bars, so a daily window is paged by date.
SPAN_DAYS = {"day": 600, "week": 3600, "month": 18000}
MINUTE_PERIODS = ("m1", "m5", "m15", "m30", "m60")

# Eastmoney pages behind a WAF: keep calls serial and spaced out. Batching a
# hundred symbols without this is the usual way an IP gets blocked for hours.
_EM_LOCK = threading.Lock()
_EM_MIN_INTERVAL = 1.0
_em_last_call = [0.0]


_SYMBOL_RE = re.compile(
    r"^(?:(?P<prefix>sh|sz|bj)(?P<digits1>\d{6})|(?P<digits2>\d{6})(?:\.(?P<suffix>ss|sh|sz|bj))?)$",
    re.IGNORECASE,
)

_client = requests.Session()
_client.headers.update({"User-Agent": UA})
try:  # transient connection errors and 5xx are worth one more try; 403 is not
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    _adapter = HTTPAdapter(
        max_retries=Retry(
            total=2,
            connect=2,
            backoff_factor=0.6,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
    )
    _client.mount("https://", _adapter)
except Exception:  # very old urllib3 without allowed_methods
    pass


def cn_now() -> datetime:
    """Beijing wall-clock time — the A-share market's own calendar."""
    return datetime.now(CN_TZ)


def is_a_share(symbol: str) -> bool:
    return parse_symbol(symbol) is not None


def parse_symbol(symbol: str) -> tuple[str, str] | None:
    """Split an A-share symbol into ``(market_prefix, 6_digits)``.

    Accepts what this project and its users actually type: ``600519``,
    ``600519.SS`` (Yahoo's spelling), ``600519.SH``, ``000001.SZ``,
    ``920982.BJ``, ``sh600519``. Returns None for anything else — a Hong Kong
    ``0700.HK`` or a ``BTC-USD`` is not an A-share and must not be parsed as one.
    """
    match = _SYMBOL_RE.match(str(symbol).strip())
    if not match:
        return None
    digits = match.group("digits1") or match.group("digits2")
    market = (match.group("prefix") or match.group("suffix") or "").lower()
    if market == "ss":
        market = "sh"
    if digits.startswith("000") and market == "sh":
        # 000xxx is shared: Shenzhen shares (000001 Ping An Bank) and Shanghai
        # indexes (000300 CSI 300). An explicit Shanghai market here can only
        # mean the index, and this vendor serves companies, so decline.
        return None
    natural = _natural_market(digits)
    if market and market != natural:
        # A wrong explicit market ("SZ600519") would silently price another
        # company, so say so instead of guessing.
        logger.warning("A-share symbol %r declares %s but %s belongs to %s", symbol, market, digits, natural)
        return None
    return natural, digits


def _natural_market(digits: str) -> str:
    if digits.startswith(("92", "4", "8")):
        return "bj"
    # A "1" prefix is not Shenzhen-only: 110/111/113 are Shanghai convertibles
    # (113050 is listed in Shanghai), while 123/127/128 are Shenzhen's.
    if digits.startswith(("110", "111", "113", "132")):
        return "sh"
    if digits.startswith("399"):
        return "sz"
    if digits[0] in ("5", "6", "9"):
        return "sh"
    return "sz"


# Prefixes that are quoted on an exchange but are not operating companies. The
# vendor quotes them; it has no statements or shareholder register for them,
# because a fund and a bond do not have one.
_CONVERTIBLES = {"sh": ("110", "111", "113", "132"), "sz": ("120", "123", "127", "128")}
_FUNDS = {"sh": ("50", "51", "52", "56", "58"), "sz": ("15", "16")}

_KIND_LABELS = {
    "etf": "fund (ETF/LOF)",
    "convertible": "convertible bond",
    "index": "index",
    "stock": "listed company",
}


def instrument_kind(digits: str, market: str) -> str:
    """What a quoted A-share code actually is: stock, etf, convertible or index."""
    if digits.startswith(_CONVERTIBLES.get(market, ())):
        return "convertible"
    if digits.startswith(_FUNDS.get(market, ())):
        return "etf"
    if market == "sh" and digits.startswith("000"):
        return "index"
    if market == "sz" and digits.startswith("399"):
        return "index"
    return "stock"


def require_quoted(symbol: str) -> tuple[str, str]:
    """``(prefix, digits)`` for anything this vendor can quote.

    Prices, indicators, news and the live quote work for funds and convertibles
    too — Tencent serves those the same way it serves shares.
    """
    parsed = parse_symbol(symbol)
    if parsed is None:
        raise VendorUnavailableError(
            f"{symbol!r} is not a Shanghai/Shenzhen/Beijing listed code; "
            "the a_stock vendor does not cover it"
        )
    return parsed


def require_a_share(symbol: str) -> tuple[str, str]:
    """``(prefix, digits)`` for an operating company, else step aside.

    A fund or a convertible has no income statement, balance sheet or
    shareholder register, so a statement request for one belongs to a vendor
    that carries fund-level data instead of being answered with an empty
    statement here.
    """
    prefix, digits = require_quoted(symbol)
    kind = instrument_kind(digits, prefix)
    if kind != "stock":
        article = "an" if kind == "index" else "a"
        raise VendorUnavailableError(
            f"{symbol!r} is {article} {_KIND_LABELS[kind]}, not a listed company; "
            "the a_stock vendor serves company statements only"
        )
    return prefix, digits


def cn_symbol(symbol: str) -> str:
    """``('sh', '600519')`` -> ``'sh600519'``, the prefix form Tencent/Sina want."""
    prefix, digits = require_quoted(symbol)
    return f"{prefix}{digits}"


def _http(url: str, *, params=None, headers=None, timeout=(8, 30), allow_status=()):
    merged = {"User-Agent": UA}
    merged.update(headers or {})
    try:
        response = _client.get(url, params=params, headers=merged, timeout=timeout)
        if response.status_code not in allow_status:
            response.raise_for_status()
    except requests.RequestException as exc:
        raise VendorUnavailableError(f"request to {url} failed: {type(exc).__name__}: {exc}") from exc
    return response


def get_json(url: str, *, params=None, headers=None, timeout=(8, 30)):
    response = _http(url, params=params, headers=headers, timeout=timeout)
    try:
        return response.json()
    except ValueError as exc:
        raise VendorUnavailableError(f"{url} did not answer with JSON (probably an error page)") from exc


@contextmanager
def em_gate():
    """Hold the shared Eastmoney throttle around exactly one request.

    Every eastmoney.com host shares one gate: the WAF counts connections per
    IP, so a datacenter call and a push2his call must never run concurrently,
    and spacing is measured across both. Callers keep the lock only for the
    duration of their request.
    """
    with _EM_LOCK:
        wait = _EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
        if wait > 0:
            time.sleep(wait + random.uniform(0.1, 0.4))
        try:
            yield
        finally:
            _em_last_call[0] = time.time()


def em_json(report: str, *, filter_str: str = "", columns: str = "ALL", page_size: int = 100,
            sort_columns: str = "", sort_types: str = "-1"):
    """One Eastmoney datacenter report, throttled and serial.

    A validation error (bad filter) comes back as code 9501 with an empty
    ``result``; that is a bug in the caller, not an outage, so it is surfaced as
    a plain ValueError the router will not retry.
    """
    params = {
        "reportName": report,
        "columns": columns,
        "filter": filter_str,
        "pageNumber": "1",
        "pageSize": str(page_size),
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }
    with em_gate():
        payload = get_json(EASTMONEY_DATACENTER, params=params, timeout=(8, 25))
    if not isinstance(payload, dict):
        raise VendorUnavailableError("Eastmoney datacenter answered with a non-object payload")
    code = payload.get("code")
    if code not in (0, None):
        message = payload.get("message") or ""
        if code == 9201:  # "query returned no data"
            return []
        raise ValueError(f"Eastmoney report {report} rejected the query: {code} {message}")
    result = payload.get("result") or {}
    rows = result.get("data") or []
    return [row for row in rows if isinstance(row, dict)]


# --- prices -----------------------------------------------------------------


_kline_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}
_KLINE_TTL = 600  # seconds; several analysts read the same window in one run


def daily_bars(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Unadjusted daily bars from Tencent, ``Date`` indexed on a plain column.

    Cached in-process: the analyst team runs four agents that each call
    ``get_stock_data`` for the same window, and Tencent starts throttling a host
    after roughly 600 requests.
    """
    prefix, digits = require_quoted(symbol)
    key = (prefix + digits, start_date, end_date)
    hit = _kline_cache.get(key)
    if hit and (time.time() - hit[0]) < _KLINE_TTL:
        return hit[1].copy()

    frame = _fetch_daily(prefix + digits, start_date, end_date)
    _kline_cache[key] = (time.time(), frame)
    return frame.copy()


def _fetch_daily(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if start > end:
        raise ValueError(f"start_date {start_date} is after end_date {end_date}")

    rows: dict[str, dict] = {}
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=SPAN_DAYS["day"] - 1), end)
        params = f"{symbol},day,{cursor.isoformat()},{stop.isoformat()},640,"
        payload = _tencent_kline(params)
        node = (payload.get("data") or {}).get(symbol)
        if not isinstance(node, dict):
            raise VendorUnavailableError(f"Tencent K-line returned nothing for {symbol} {cursor}..{stop}")
        items = node.get("day")
        if items is None:
            raise VendorUnavailableError(f"Tencent K-line response for {symbol} has no 'day' array")
        for item in items:
            date = str(item[0])[:10]
            if date in rows:
                continue
            rows[date] = {
                "Date": date,
                "Open": float(item[1]),
                "Close": float(item[2]),
                "High": float(item[3]),
                "Low": float(item[4]),
                "Volume": float(item[5]),
            }
        cursor = stop + timedelta(days=1)

    if not rows:
        raise NoMarketDataError(
            symbol, symbol, f"no daily bars between {start_date} and {end_date} (not listed yet, suspended, or a non-trading span)"
        )
    frame = pd.DataFrame(sorted(rows.values(), key=lambda r: r["Date"]))
    # Tencent reports 科创板 (688/689) in shares and everything else in lots.
    if not symbol[2:].startswith(("688", "689")):
        frame["Volume"] = frame["Volume"] * 100
    return frame


def _tencent_kline(param: str) -> dict:
    """Ask the three equivalent hosts in turn; a host that answers empty is cooling."""
    errors = []
    for host in TENCENT_KLINE_HOSTS:
        try:
            payload = get_json(
                f"{host}/appstock/app/fqkline/get",
                params={"param": param},
                headers={"Referer": "https://gu.qq.com/"},
                timeout=(8, 25),
            )
        except VendorUnavailableError as exc:
            errors.append(f"{host}: {exc}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{host}: non-object payload")
            continue
        if payload.get("msg") == "param error":
            raise ValueError(f"Tencent K-line rejected the request ({param}); the span or symbol is invalid")
        if isinstance(payload.get("data"), dict) and payload["data"]:
            return payload
        errors.append(f"{host}: empty response")
    raise VendorUnavailableError("no Tencent K-line host answered: " + "; ".join(errors))


def adjustment_factors(symbol: str) -> list[tuple[str, float]] | None:
    """Forward-adjustment factors from Sina as ``[(date, factor)]``, oldest first.

    The factor is the qfq divisor (adjusted = raw / factor). Two kinds of
    events ride in the series: dividend events carry only ``f``, while split
    events (a 1-to-3 ETF share split, for instance) carry the ratio in ``s``
    with ``f`` left at 1 — so the effective factor is ``f * s``. Verified live
    against 588200's 2026-07-21 split (s=3.0; 3.539 raw shows as 1.180
    forward-adjusted, matching the exchanges' own qfq display).

    The series is anchored at the latest trade date (factor 1.0 there), which is
    why the caller re-anchors it: analysing 2023 with today's factors would show
    2023 prices scaled by every dividend paid since.
    """
    cn = cn_symbol(symbol)
    try:
        response = _http(SINA_FACTOR_URL.format(symbol=cn, kind="qfq"),
                         headers={"Referer": "https://finance.sina.com.cn/"})
    except VendorUnavailableError:
        return None
    text = response.text
    brace = text.find("{")
    if brace < 0:
        return None
    try:
        # The body is `var sh600519qfq={...}` followed by a /* base64 */ comment,
        # so the JSON is decoded from the first brace rather than matched by regex.
        import json

        data, _ = json.JSONDecoder().raw_decode(text[brace:])
    except ValueError:
        return None
    series = []
    for item in data.get("data", []):
        if "d" not in item:
            continue
        try:
            divisor = float(item.get("f") or 1.0)
            split = float(item.get("s") or 1.0)
        except (TypeError, ValueError):
            continue
        factor = divisor * split
        if factor > 0:
            series.append((str(item["d"])[:10], factor))
    if not series:
        return None
    return sorted(series)


def apply_factors(frame: pd.DataFrame, factors: list[tuple[str, float]] | None,
                  anchor: str) -> tuple[pd.DataFrame, bool]:
    """Scale raw prices to a forward-adjusted series anchored at ``anchor``.

    Returns the frame and whether it was adjusted. Anchoring at the analysis end
    date (instead of today) keeps the price level a 2023 run sees equal to what
    the market quoted in 2023, while still removing ex-dividend jumps.
    """
    if not factors:
        return frame, False
    dates = [d for d, _ in factors]

    def factor_at(day) -> float | None:
        # Last factor whose effective date is on or before this bar. ``day``
        # may be a string or a Timestamp; normalize to the ISO date so the
        # string comparison always works.
        day = str(day)[:10]
        value = None
        for day_i, f in factors:
            if day_i <= day:
                value = f
            else:
                break
        return value

    anchor_factor = factor_at(anchor)
    if not anchor_factor:
        return frame, False

    scales = []
    for day in frame["Date"]:
        f = factor_at(day)
        if not f:
            return frame, False
        scales.append(anchor_factor / f)
    scale = pd.Series(scales, index=frame.index)

    adjusted = frame.copy()
    for column in ("Open", "High", "Low", "Close"):
        if column in adjusted.columns:
            adjusted[column] = adjusted[column] * scale
    return adjusted, True


def quote_snapshot(symbol: str) -> dict:
    """Live quote, valuation and limit prices for one share (Tencent).

    Field indexes follow the skill's calibration: 39 PE(TTM), 43 amplitude,
    44 circulating cap, 45 total cap, 46 PB, 47/48 limit up/down, 52 static PE.
    """
    cn = cn_symbol(symbol)
    response = _http(TENCENT_QUOTE_URL + cn, timeout=(5, 15))
    text = response.content.decode("gbk", "replace")
    if "v_pv_none_match" in text:
        raise NoMarketDataError(symbol, cn, "Tencent has no such code")
    match = re.search(rf'v_{cn}="([^"]*)"', text)
    if not match:
        raise VendorUnavailableError(f"Tencent quote response for {cn} has no v_{cn} variable")
    fields = match.group(1).split("~")
    if len(fields) < 53:
        raise VendorUnavailableError(f"Tencent quote response for {cn} has only {len(fields)} fields")

    def num(index: int) -> float | None:
        raw = fields[index].strip()
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    snapshot = {
        "name": fields[1],
        "price": num(3),
        "previous_close": num(4),
        "open": num(5),
        "change_pct": num(32),
        "high": num(33),
        "low": num(34),
        "amount_wan": num(37),
        "turnover_pct": num(38),
        "pe_ttm": num(39),
        "amplitude_pct": num(43),
        "float_mcap_yi": num(44),
        "mcap_yi": num(45),
        "pb": num(46),
        "limit_up": num(47),
        "limit_down": num(48),
        "volume_ratio": num(49),
        "pe_static": num(52),
    }
    # A retired Beijing code returns a frozen quote with zero volume; using it
    # for valuation is how a 100%-wrong price gets into a report.
    stale = bool(snapshot["amount_wan"] == 0 and snapshot["price"] == snapshot["previous_close"] and snapshot["price"])
    snapshot["is_stale"] = stale
    return snapshot


def tencent_reachable() -> bool:
    return vendor_reachable("https://qt.gtimg.cn/q=sh000001")
