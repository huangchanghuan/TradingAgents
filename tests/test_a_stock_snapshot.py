"""Tests for the A-share verification snapshot (Sina primary, Eastmoney backup)."""

from __future__ import annotations

import pandas as pd
import pytest

import tradingagents.dataflows.vendors.a_stock.snapshot as a_snap
import tradingagents.dataflows.vendors.yahoo.snapshot as yahoo_snapshot
from tradingagents.dataflows import router
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError


def _sample_frame(rows: int = 320, end: str = "2026-10-30") -> pd.DataFrame:
    dates = pd.bdate_range(end=end, periods=rows)
    closes = [1.0 + i / 1000 for i in range(rows)]
    return pd.DataFrame({
        "Date": dates,
        "Open": [c - 0.01 for c in closes],
        "High": [c + 0.02 for c in closes],
        "Low": [c - 0.02 for c in closes],
        "Close": closes,
        "Volume": [1_000_000 + i for i in range(rows)],
    })


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    a_snap._snapshot_cache.clear()
    yield
    a_snap._snapshot_cache.clear()


@pytest.fixture
def no_factors(monkeypatch):
    """No Sina factor series: the snapshot must degrade to unadjusted, not fail."""
    monkeypatch.setattr(a_snap, "adjustment_factors", lambda symbol: None)


@pytest.mark.unit
class TestAShareSnapshot:
    def test_builds_from_sina_and_names_the_source(self, monkeypatch, no_factors):
        monkeypatch.setattr(a_snap, "_fetch_sina_daily", lambda p, d, as_of: _sample_frame())
        out = a_snap.build_verified_market_snapshot("588200.SS", "2026-10-30")
        assert "Verified market data snapshot for 588200.SS" in out
        assert "Data source: Sina" in out
        assert "independent of the Tencent primary" in out
        assert "NOT adjusted" in out          # no factor series available
        assert "boll_lb" in out               # same indicator shape as the Yahoo snapshot

    def test_excludes_rows_after_the_analysis_date(self, monkeypatch, no_factors):
        frame = pd.concat([_sample_frame(), _sample_frame(rows=2, end="2026-11-13")])
        monkeypatch.setattr(a_snap, "_fetch_sina_daily", lambda p, d, as_of: frame)
        out = a_snap.build_verified_market_snapshot("588200.SS", "2026-10-30")
        assert "Latest trading row used: 2026-10-30" in out

    def test_weekend_analysis_date_uses_the_previous_trading_day(self, monkeypatch, no_factors):
        monkeypatch.setattr(a_snap, "_fetch_sina_daily", lambda p, d, as_of: _sample_frame())
        out = a_snap.build_verified_market_snapshot("588200.SS", "2026-11-01")  # Sunday
        assert "Latest trading row used: 2026-10-30" in out

    def test_no_rows_on_or_before_the_date_raises_no_data(self, monkeypatch, no_factors):
        monkeypatch.setattr(a_snap, "_fetch_sina_daily",
                            lambda p, d, as_of: _sample_frame(end="2026-10-30"))
        with pytest.raises(NoMarketDataError):
            a_snap.build_verified_market_snapshot("588200.SS", "2020-01-01")

    def test_stale_bars_raise_no_data(self, monkeypatch, no_factors):
        monkeypatch.setattr(a_snap, "_fetch_sina_daily",
                            lambda p, d, as_of: _sample_frame(end="2026-09-01"))
        with pytest.raises(NoMarketDataError, match="stale|before the requested"):
            a_snap.build_verified_market_snapshot("588200.SS", "2026-10-30")

    def test_forward_adjustment_is_reported_with_the_last_factor_event(self, monkeypatch):
        # 588200's split: factor 3.0 before 2026-07-21, 1.0 on and after it.
        factors = [("1900-01-01", 3.0), ("2026-07-21", 1.0)]
        monkeypatch.setattr(a_snap, "adjustment_factors", lambda symbol: factors)
        monkeypatch.setattr(a_snap, "_fetch_sina_daily", lambda p, d, as_of: _sample_frame())
        out = a_snap.build_verified_market_snapshot("588200.SS", "2026-10-30")
        assert "forward-adjusted" in out
        assert "last factor event 2026-07-21" in out

    def test_adjusted_prices_match_the_primary_rule(self, monkeypatch):
        """Pre-split bars scale by 1/3; post-split bars stay raw — one regime per side."""
        frame = _sample_frame(rows=250)  # spans the 2026-07-21 split
        factors = [("1900-01-01", 3.0), ("2026-07-21", 1.0)]
        monkeypatch.setattr(a_snap, "adjustment_factors", lambda symbol: factors)
        monkeypatch.setattr(a_snap, "_fetch_sina_daily", lambda p, d, as_of: frame)
        df, note = a_snap._verified_rows("588200.SS", "2026-10-30")
        # first bar (2025-11, pre-split): raw 1.000 -> 1.000/3
        assert abs(df["Close"].iloc[0] - 1.0 / 3) < 1e-9
        # last bar (2026-10-30, post-split): factor 1.0 there, so raw 1.249 stays
        assert abs(df["Close"].iloc[-1] - 1.249) < 1e-9
        assert "last factor event 2026-07-21" in note


@pytest.mark.unit
class TestFallbackAndGuards:
    def test_sina_failure_falls_back_to_eastmoney(self, monkeypatch, no_factors):
        def boom(prefix, digits, as_of):
            raise VendorUnavailableError("sina down")

        monkeypatch.setattr(a_snap, "_fetch_sina_daily", boom)
        monkeypatch.setattr(a_snap, "_fetch_em_daily", lambda p, d, as_of: _sample_frame())
        out = a_snap.build_verified_market_snapshot("588200.SS", "2026-10-30")
        assert "Data source: Eastmoney" in out

    def test_both_sources_down_raises_unavailable(self, monkeypatch, no_factors):
        def boom(prefix, digits, as_of):
            raise VendorUnavailableError("down")

        monkeypatch.setattr(a_snap, "_fetch_sina_daily", boom)
        monkeypatch.setattr(a_snap, "_fetch_em_daily", boom)
        with pytest.raises(VendorUnavailableError):
            a_snap.build_verified_market_snapshot("588200.SS", "2026-10-30")

    def test_a_no_data_verdict_survives_the_fallback(self, monkeypatch, no_factors):
        """'Not listed yet' from both sources is an answer about the symbol."""
        def no_rows(prefix, digits, as_of):
            raise NoMarketDataError("sh588200", "sh588200", "no rows")

        monkeypatch.setattr(a_snap, "_fetch_sina_daily", no_rows)
        monkeypatch.setattr(a_snap, "_fetch_em_daily", no_rows)
        with pytest.raises(NoMarketDataError):
            a_snap.build_verified_market_snapshot("588200.SS", "2020-01-01")

    def test_eastmoney_identity_mismatch_is_discarded(self, monkeypatch, no_factors):
        """push2his has answered with another instrument before; never quote it."""
        def boom(prefix, digits, as_of):
            raise VendorUnavailableError("sina down")

        monkeypatch.setattr(a_snap, "_fetch_sina_daily", boom)

        real_fetch = a_snap._fetch_em_daily

        def lying_em(prefix, digits, as_of):
            # The guard lives inside _fetch_em_daily; drive it with a mocked
            # transport whose code belongs to another instrument.
            monkeypatch.setattr(
                a_snap, "get_json",
                lambda url, **k: {"data": {"code": "600519", "name": "贵州茅台",
                                           "klines": ["2026-10-30,1.0,1.0,1.0,1.0,100,100"]}})
            return real_fetch(prefix, digits, as_of)

        monkeypatch.setattr(a_snap, "_fetch_em_daily", lying_em)
        with pytest.raises(VendorUnavailableError, match="mismatch|no A-share snapshot"):
            a_snap.build_verified_market_snapshot("588200.SS", "2026-10-30")


@pytest.mark.unit
class TestRouting:
    def test_tool_routes_a_share_to_the_a_stock_impl(self, monkeypatch):
        from tradingagents.agents.tools import get_verified_market_snapshot

        def fake(symbol, as_of_date, look_back_days=30, indicators=None):
            return "A_SHARE_SNAPSHOT"

        monkeypatch.setitem(
            router.VENDOR_METHODS["get_verified_market_snapshot"], "a_stock", fake)
        out = get_verified_market_snapshot.invoke(
            {"symbol": "588200.SS", "curr_date": "2026-10-30"})
        assert out == "A_SHARE_SNAPSHOT"

    def test_tool_routes_us_symbol_to_the_yahoo_impl(self, monkeypatch):
        from tradingagents.agents.tools import get_verified_market_snapshot

        def fake(symbol, as_of_date, look_back_days=30, indicators=None):
            return "YAHOO_SNAPSHOT"

        from tradingagents.dataflows.errors import VendorNotApplicableError

        monkeypatch.setitem(
            router.VENDOR_METHODS["get_verified_market_snapshot"], "a_stock",
            lambda *a, **k: (_ for _ in ()).throw(VendorNotApplicableError("not an A-share")))
        monkeypatch.setitem(
            router.VENDOR_METHODS["get_verified_market_snapshot"], "yfinance", fake)
        out = get_verified_market_snapshot.invoke({"symbol": "COF", "curr_date": "2026-05-20"})
        assert out == "YAHOO_SNAPSHOT"

    def test_non_applicable_vendor_does_not_poison_a_no_data_verdict(self, monkeypatch):
        """An invalid symbol stays NO_DATA even though a_stock skipped first."""
        from tradingagents.agents.tools import get_verified_market_snapshot
        from tradingagents.dataflows.errors import VendorNotApplicableError

        def not_applicable(*a, **k):
            raise VendorNotApplicableError("not an A-share")

        def no_rows(*a, **k):
            raise NoMarketDataError("ZZZZ", "ZZZZ", "no price rows")

        monkeypatch.setitem(
            router.VENDOR_METHODS["get_verified_market_snapshot"], "a_stock", not_applicable)
        monkeypatch.setitem(
            router.VENDOR_METHODS["get_verified_market_snapshot"], "yfinance", no_rows)
        out = get_verified_market_snapshot.invoke({"symbol": "ZZZZ", "curr_date": "2026-05-20"})
        assert out.startswith("NO_DATA_AVAILABLE"), out

    def test_yahoo_builder_still_patched_via_load_ohlcv(self, monkeypatch):
        """The 5 existing test files patch yahoo.snapshot.load_ohlcv; keep that seam."""
        frame = _sample_frame(rows=60, end="2026-05-20")
        monkeypatch.setattr(yahoo_snapshot, "load_ohlcv",
                            lambda s, d, fill_gaps=True: frame)
        out = yahoo_snapshot.build_verified_market_snapshot("COF", "2026-05-20")
        assert "Verified market data snapshot for COF" in out
