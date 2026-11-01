"""Unit tests for the southbound-flow and China-rates vendors and their tools."""
import pytest

from tradingagents.dataflows import router
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors.a_stock import fund_flow, rates


def _mutual_row(day: str, deal: float, net: float | None) -> dict:
    return {"TRADE_DATE": f"{day} 00:00:00", "DEAL_AMT": deal, "NET_DEAL_AMT": net,
            "BUY_AMT": None, "SELL_AMT": None}


@pytest.fixture
def mutual_rows(monkeypatch):
    """Two legs: SH with turnover only (net withheld upstream), SZ with net."""
    captured = {}

    def fake_em_json(report, *, filter_str="", columns="", page_size="100",
                     sort_columns="", sort_types="-1"):
        captured["report"] = report
        if '"003"' in filter_str:
            return [_mutual_row("2026-10-30", 142937.46, None),
                    _mutual_row("2026-10-29", 125841.0, None)]
        return [_mutual_row("2026-10-30", 26084.47, -1298.15),
                _mutual_row("2026-10-29", 25387.0, -4140.0)]

    monkeypatch.setattr(fund_flow, "em_json", fake_em_json)
    return captured


class TestSouthboundFlow:
    @pytest.mark.unit
    def test_renders_both_legs_and_flags_the_sh_net_gap(self, mutual_rows):
        out = fund_flow.get_southbound_flow("2026-10-30", 30)
        assert "| 2026-10-30 | 1,429.37 | 260.84 | -12.98 |" in out
        assert "港股通深 cumulative net buy: -54.38 亿" in out
        assert "沪端净买入上游未披露" in out
        assert "lower bound" in out

    @pytest.mark.unit
    def test_past_dated_run_never_sees_future_rows(self, monkeypatch):
        """Rows newer than as_of are dropped even when the feed returns them."""
        def leaky(report, *, filter_str="", columns="", page_size="100",
                  sort_columns="", sort_types="-1"):
            if '"003"' in filter_str:
                return [_mutual_row("2026-11-02", 999999.0, None),
                        _mutual_row("2026-10-30", 142937.46, None)]
            return [_mutual_row("2026-11-02", 999999.0, 1.0),
                    _mutual_row("2026-10-30", 26084.47, -1298.15)]

        monkeypatch.setattr(fund_flow, "em_json", leaky)
        out = fund_flow.get_southbound_flow("2026-10-30", 30)
        assert "2026-11-02" not in out
        assert "999,999" not in out and "1,429.37" in out

    @pytest.mark.unit
    def test_empty_window_raises_no_data(self, monkeypatch):
        monkeypatch.setattr(fund_flow, "em_json", lambda *a, **k: [])
        from tradingagents.dataflows.errors import NoMarketDataError
        with pytest.raises(NoMarketDataError):
            fund_flow.get_southbound_flow("2026-10-30", 30)

    @pytest.mark.unit
    def test_tool_routes_through_the_vendor(self, monkeypatch):
        from tradingagents.agents import tools

        monkeypatch.setitem(router.VENDOR_METHODS["get_southbound_flow"], "a_stock",
                            lambda as_of, look_back: "SB_FLOW")
        out = tools.get_southbound_flow.invoke({"curr_date": "2026-10-30",
                                                "trade_date": "2026-10-30"})
        assert out == "SB_FLOW"


_CHINABOND_HTML = """<html><!-- note --><body>
<table><tr><th>unrelated</th></tr></table>
<table>
<tr><th>曲线名称</th><th>日期</th><th>3月</th><th>6月</th><th>1年</th><th>3年</th><th>5年</th><th>7年</th><th>10年</th><th>30年</th></tr>
<tr><td>中债国债收益率曲线</td><td>2026-10-30</td><td>1.1</td><td>1.2</td><td>1.3</td><td>1.4</td><td>1.5</td><td>1.6</td><td>1.7</td><td>1.8</td></tr>
<tr><td>中债国债收益率曲线</td><td>2026-10-29</td><td>1.1</td><td>1.2</td><td>1.31</td><td>1.4</td><td>1.5</td><td>1.6</td><td>1.72</td><td>1.8</td></tr>
</table></body></html>"""


@pytest.fixture
def lpr_rows(monkeypatch):
    def fake_em_json(report, *, filter_str="", columns="", page_size="100",
                     sort_columns="", sort_types="-1"):
        assert "TRADE_DATE<='2026-10-30'" in filter_str
        return [{"TRADE_DATE": "2026-10-20 00:00:00", "LPR1Y": 3.0, "LPR5Y": 3.5},
                {"TRADE_DATE": "2026-09-21 00:00:00", "LPR1Y": 3.02, "LPR5Y": 3.52},
                {"TRADE_DATE": "1991-04-21 00:00:00", "LPR1Y": None, "LPR5Y": None,
                 "RATE_1": 8.64}]

    monkeypatch.setattr(rates, "em_json", fake_em_json)


@pytest.fixture
def chinabond_rows(monkeypatch):
    def fake_http(url, *, params=None, headers=None, timeout=None, allow_status=()):
        class R:
            content = _CHINABOND_HTML.encode()
        return R()

    monkeypatch.setattr(rates, "_http", fake_http)


class TestChinaRates:
    @pytest.mark.unit
    def test_renders_lpr_filtered_and_cgb_spread(self, lpr_rows, chinabond_rows):
        out = rates.get_china_rates("2026-10-30", 90)
        assert "| 2026-10-20 | 3.00 | 3.50 |" in out
        assert "8.64" not in out  # legacy benchmark rows never render as LPR
        assert "2026-11" not in out  # anti-lookahead within fixtures
        assert "| 2026-10-30 | 1.300 | 1.700 | 40.0 |" in out
        assert "10Y-1Y spread 40bp" in out
        assert "10Y 1.700% as of 2026-10-30" in out  # newest-first feed re-sorted

    @pytest.mark.unit
    def test_chinabond_failure_degrades_to_lpr_only(self, lpr_rows, monkeypatch):
        def boom(url, **kwargs):
            raise VendorUnavailableError("chinabond down")

        monkeypatch.setattr(rates, "_http", boom)
        out = rates.get_china_rates("2026-10-30", 90)
        assert "UNAVAILABLE" in out and "chinabond down" in out
        assert "| 2026-10-20 | 3.00 | 3.50 |" in out  # LPR survives

    @pytest.mark.unit
    def test_header_drift_degrades_with_an_explicit_note(self, lpr_rows, monkeypatch):
        drifted = _CHINABOND_HTML.replace("10年", "20年")
        monkeypatch.setattr(rates, "_http", lambda url, **k: type(
            "R", (), {"content": drifted.encode()})())
        out = rates.get_china_rates("2026-10-30", 90)
        assert "UNAVAILABLE" in out and "header drifted" in out
        assert "| 2026-10-20 | 3.00 | 3.50 |" in out  # LPR survives
        assert "10Y-1Y利差" not in out  # no curve values fabricated

    @pytest.mark.unit
    def test_tool_routes_through_the_vendor(self, monkeypatch):
        from tradingagents.agents import tools

        monkeypatch.setitem(router.VENDOR_METHODS["get_china_rates"], "a_stock",
                            lambda as_of, look_back: "CN_RATES")
        out = tools.get_china_rates.invoke({"curr_date": "2026-10-30",
                                            "trade_date": "2026-10-30"})
        assert out == "CN_RATES"


class TestWiring:
    @pytest.mark.unit
    def test_both_methods_are_registered_in_optional_categories(self):
        assert router.get_category_for_method("get_southbound_flow") == "fund_flow_data"
        assert router.get_category_for_method("get_china_rates") == "cn_rates_data"
        assert {"fund_flow_data", "cn_rates_data"} <= router.OPTIONAL_CATEGORIES
        assert set(router.VENDOR_METHODS["get_southbound_flow"]) == {"a_stock"}
        assert set(router.VENDOR_METHODS["get_china_rates"]) == {"a_stock"}

    @pytest.mark.unit
    def test_analysts_offer_the_new_tools(self):
        from tradingagents.agents.analysts import market_analyst, news_analyst

        assert "get_southbound_flow" in tools_names(market_analyst.TOOLS)
        assert "get_china_rates" in tools_names(news_analyst.TOOLS)
        assert "get_southbound_flow" not in tools_names(news_analyst.TOOLS)


def tools_names(tools):
    return {t.name: t for t in tools}
