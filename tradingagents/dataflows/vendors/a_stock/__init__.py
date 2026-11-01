# Aggregates the A-share (China) vendor implementations into one module the
# vendor router imports from. Data comes from mainland public endpoints that
# need no API key: Tencent quotes/K-lines, Sina statements and adjustment
# factors, Eastmoney news and holder trades, Cailianpress flash news. Sources
# and field layouts follow the `a-stock-data` skill (V3.10).
from tradingagents.dataflows.vendors.a_stock.fund_flow import get_southbound_flow
from tradingagents.dataflows.vendors.a_stock.fundamentals import (
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_insider_transactions,
)
from tradingagents.dataflows.vendors.a_stock.market import get_indicators, get_stock_data
from tradingagents.dataflows.vendors.a_stock.news import get_global_news, get_news
from tradingagents.dataflows.vendors.a_stock.rates import get_china_rates
from tradingagents.dataflows.vendors.a_stock.snapshot import build_verified_market_snapshot

__all__ = [
    "build_verified_market_snapshot",
    "get_balance_sheet",
    "get_cashflow",
    "get_china_rates",
    "get_fundamentals",
    "get_global_news",
    "get_income_statement",
    "get_indicators",
    "get_insider_transactions",
    "get_news",
    "get_southbound_flow",
    "get_stock_data",
]
