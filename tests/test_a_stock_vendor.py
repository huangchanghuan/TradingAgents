"""The a_stock vendor's offline contracts: which symbols it claims, and how the
price series is adjusted. Network calls are out of scope here — the vendor is
also exercised against the live endpoints by hand, and a test that depends on
Tencent being up would fail for the wrong reason.
"""

import pandas as pd
import pytest

from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors.a_stock.common import apply_factors, parse_symbol


@pytest.mark.parametrize(
    "symbol, expected",
    [
        ("600519", ("sh", "600519")),
        ("600519.SS", ("sh", "600519")),   # Yahoo's spelling for Shanghai
        ("600519.SH", ("sh", "600519")),
        ("sh600519", ("sh", "600519")),
        ("000001.SZ", ("sz", "000001")),
        ("300750.SZ", ("sz", "300750")),
        ("688981.SS", ("sh", "688981")),
        ("920982.BJ", ("bj", "920982")),
    ],
)
def test_parse_symbol_accepts_a_shares(symbol, expected):
    assert parse_symbol(symbol) == expected


@pytest.mark.parametrize(
    "symbol",
    [
        "NVDA",       # US
        "0700.HK",    # Hong Kong
        "7203.T",     # Tokyo
        "BTC-USD",    # crypto
        "SZ600519",   # market contradicts the code range
        "6005190",    # seven digits
        "000300.SS",  # a Shanghai index: 000xxx + explicit sh can only be an index
        "600519.XSHG",  # JoinQuant spelling, not this vendor's
    ],
)
def test_parse_symbol_declines_anything_else(symbol):
    assert parse_symbol(symbol) is None


def test_apply_factors_anchors_at_the_analysis_date():
    """Prices before the anchor are scaled by the ratio of the two factors.

    Sina's factor series is anchored at today (1.0 there), so using it as-is
    would show a 2023 run rescaled by every dividend since. Anchoring at the
    last bar keeps the level the market quoted on that date.
    """
    frame = pd.DataFrame(
        {"Date": ["2024-01-02", "2024-06-03", "2024-07-01"], "Close": [100.0, 90.0, 95.0]}
    )
    factors = [("2020-01-01", 1.10), ("2024-06-20", 1.05), ("2024-12-31", 1.0)]

    adjusted, applied = apply_factors(frame, factors, anchor="2024-07-01")

    assert applied is True
    # 2024-07-01 sits on the 1.05 step, so it is the anchor: unchanged.
    assert adjusted["Close"].iloc[-1] == pytest.approx(95.0)
    # 2024-06-03 (before the 06-20 ex-date) scales by 1.05/1.10.
    assert adjusted["Close"].iloc[1] == pytest.approx(90.0 * 1.05 / 1.10)
    assert adjusted["Close"].iloc[0] == pytest.approx(100.0 * 1.05 / 1.10)


def test_apply_factors_passes_through_without_a_series():
    frame = pd.DataFrame({"Date": ["2024-01-02"], "Close": [100.0]})
    adjusted, applied = apply_factors(frame, None, anchor="2024-01-02")
    assert applied is False
    assert adjusted["Close"].iloc[0] == 100.0


def test_non_a_share_is_a_vendor_miss_not_an_error():
    """The router relies on this type to fall through to the next vendor."""
    from tradingagents.dataflows.vendors.a_stock.common import require_a_share

    with pytest.raises(VendorUnavailableError):
        require_a_share("0700.HK")


def test_the_default_chain_keeps_us_tickers_on_yahoo(monkeypatch):
    """a_stock sits at the head of the shipped chain, so it must decline a US
    ticker *before* touching the network and let the router move on. The real
    a_stock implementation stays in place; only the vendor behind it is faked."""
    import copy

    from tradingagents import default_config
    from tradingagents.dataflows import router
    from tradingagents.dataflows.config import set_config

    set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))
    served = []
    monkeypatch.setitem(router.VENDOR_METHODS["get_stock_data"], "yfinance",
                        lambda *a, **k: served.append("yfinance") or "prices")

    assert router.route_to_vendor("get_stock_data", "NVDA", "2026-01-05", "2026-10-30") == "prices"
    assert served == ["yfinance"]


@pytest.mark.parametrize(
    "symbol, kind",
    [
        ("600519.SS", "stock"),
        ("000001.SZ", "stock"),
        ("300750.SZ", "stock"),
        ("920982.BJ", "stock"),
        ("510300.SS", "etf"),     # 沪深300ETF: Shanghai, 5xx
        ("588000.SS", "etf"),     # 科创50ETF
        ("159915.SZ", "etf"),     # 创业板ETF: Shenzhen, 15x
        ("113050.SS", "convertible"),
        ("128136.SZ", "convertible"),
        ("399001.SZ", "index"),
    ],
)
def test_instrument_kind_separates_funds_bonds_and_companies(symbol, kind):
    from tradingagents.dataflows.vendors.a_stock.common import instrument_kind, parse_symbol

    prefix, digits = parse_symbol(symbol)
    assert instrument_kind(digits, prefix) == kind


def test_shanghai_convertibles_are_not_mistaken_for_shenzhen():
    """113xxx is listed in Shanghai. Reading the leading "1" as Shenzhen made the
    vendor reject 113050.SS with a wrong reason ("belongs to sz")."""
    from tradingagents.dataflows.vendors.a_stock.common import parse_symbol

    assert parse_symbol("113050.SS") == ("sh", "113050")
    assert parse_symbol("110059.SS") == ("sh", "110059")
    assert parse_symbol("128136.SZ") == ("sz", "128136")


def test_a_fund_is_quoted_but_has_no_statements():
    """An ETF has prices and indicators but no income statement; the two requests
    must not be gated the same way, or the run either loses the prices or asks a
    fund for a balance sheet."""
    from tradingagents.dataflows.vendors.a_stock.common import require_a_share, require_quoted

    assert require_quoted("510300.SS") == ("sh", "510300")
    with pytest.raises(VendorUnavailableError, match="fund"):
        require_a_share("510300.SS")


def test_an_unparsable_article_date_does_not_crash_the_feeds(monkeypatch):
    """Feeds are sampled and can return malformed dates; ``in_window`` keeps such
    items as undated when the window reaches the present, so rendering and
    sorting must survive None instead of TypeError-ing the whole vendor into a
    silent fallback."""
    from datetime import datetime, timedelta
    from unittest import mock

    from tradingagents.dataflows.vendors.a_stock import news

    dated = {"title": "旧闻", "summary": "", "source": "东财",
             "date": datetime.now() - timedelta(days=1), "url": ""}
    undated = {"title": "日期坏了的文章", "summary": "", "source": "东财",
               "date": None, "url": ""}

    # Company news: the window touches the present, so the undated item is kept.
    with mock.patch.object(news, "_eastmoney_company_news",
                           return_value=[dated, undated]):
        out = news.get_news("600519.SS", str(datetime.now().date()),
                            str(datetime.now().date()))
    assert "日期坏了的文章" in out
    assert "日期未解析" in out

    # Global news: sorting mixes undated with dated without raising.
    with mock.patch.object(news, "_cls_flash", return_value=[undated]), \
         mock.patch.object(news, "_eastmoney_global", return_value=[dated]):
        out = news.get_global_news(str(datetime.now().date() - timedelta(days=1)), 7, 10)
    assert "日期坏了的文章" in out
    assert out.index("旧闻") < out.index("日期坏了的文章")  # undated sorts last
