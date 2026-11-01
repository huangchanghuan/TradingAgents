"""News for A-shares: company news from Eastmoney's search index, market-wide
flash news from Cailianpress (财联社).

The Yahoo path returns articles written for an English-speaking audience about
an ADR; here the analyst gets the Chinese coverage that actually moves the
stock. Both feeds are sampled, so finding nothing in a window is reported as a
coverage gap, never as "there was no news".
"""

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Annotated

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.date_window import coverage_gap, in_window
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors.a_stock.common import (
    _http,
    cn_symbol,
    get_json,
    require_quoted,
)

logger = logging.getLogger(__name__)

EASTMONEY_SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
CLS_LIST_URL = "https://www.cls.cn/v1/roll/get_roll_list"
EASTMONEY_GLOBAL_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"


def _clean(text: str | None) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _parse_datetime(raw: str) -> datetime | None:
    text = (raw or "").strip()
    for fmt, width in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:width], fmt)
        except ValueError:
            continue
    try:  # Eastmoney sometimes returns an epoch in milliseconds
        return datetime.fromtimestamp(int(text) / 1000)
    except (ValueError, OSError):
        return None


def _shown_date(pub_dt: datetime | None) -> str:
    """A display timestamp that survives a feed's unparsable date.

    Feeds are sampled, so an article can arrive with a malformed or missing
    date; ``in_window`` then keeps it as undated, and it must render (marked,
    not silently dropped) instead of crashing the format call.
    """
    return pub_dt.strftime("%Y-%m-%d %H:%M") if pub_dt else "日期未解析（来源未标注）"


def _eastmoney_company_news(code: str, limit: int) -> list[dict]:
    inner = json.dumps(
        {
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "time",
                    "pageIndex": 1,
                    "pageSize": limit,
                    "preTag": "",
                    "postTag": "",
                }
            },
        },
        separators=(",", ":"),
    )
    response = _http(
        EASTMONEY_SEARCH_URL,
        params={"cb": "jQuery_news", "param": inner},
        headers={"Referer": "https://so.eastmoney.com/"},
        timeout=(8, 25),
    )
    text = response.text
    start, end = text.find("("), text.rfind(")")
    if start < 0 or end <= start:
        raise VendorUnavailableError("Eastmoney news search did not answer with JSONP")
    try:
        payload = json.loads(text[start + 1 : end])
    except ValueError as exc:
        raise VendorUnavailableError("Eastmoney news search answered with unparsable JSONP") from exc
    articles = ((payload or {}).get("result") or {}).get("cmsArticleWebOld") or []
    rows = []
    for article in articles:
        published = _parse_datetime(article.get("date"))
        rows.append(
            {
                "title": _clean(article.get("title")),
                "summary": _clean(article.get("content"))[:220],
                "source": article.get("mediaName") or "东方财富",
                "date": published,
                "url": article.get("url") or "",
            }
        )
    return rows


def get_news(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Chinese-language company news published inside the window."""
    prefix, digits = require_quoted(ticker)
    limit = max(int(get_config()["news_article_limit"]), 20)
    articles = _eastmoney_company_news(digits, limit)
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

    in_range = [a for a in articles if in_window(a["date"], start_dt, end_dt)]
    label = f"{ticker}" if ticker.endswith((".SS", ".SZ", ".SH", ".BJ")) else cn_symbol(ticker)

    if in_range:
        body = "\n\n".join(
            f"### {a['title']}\n- 时间: {_shown_date(a['date'])}\n- 来源: {a['source']}\n"
            f"- 摘要: {a['summary']}\n- 链接: {a['url']}"
            for a in in_range
        )
        return (
            f"## {label} News, from {start_date} to {end_date} (Eastmoney search, Chinese press):\n\n{body}"
        )

    gap = coverage_gap(
        (a["date"] for a in articles),
        start_date,
        end_date,
        "Eastmoney news search",
        f"news for {label}",
    )
    return gap or f"No news found for {label} between {start_date} and {end_date}"


def _cls_flash(limit: int) -> list[dict]:
    """Cailianpress 7x24 flash news. The query is signed locally — no key involved."""
    params = {
        "appName": "CailianpressWeb",
        "os": "web",
        "sv": "7.7.5",
        "last_time": "",
        "refresh_type": "1",
        "rn": str(limit),
    }
    query = "&".join(f"{key}={params[key]}" for key in sorted(params))
    sign = hashlib.md5(hashlib.sha1(query.encode()).hexdigest().encode()).hexdigest()
    payload = get_json(
        f"{CLS_LIST_URL}?{query}&sign={sign}",
        headers={"Referer": "https://www.cls.cn/"},
        timeout=(6, 20),
    )
    rows = []
    for item in ((payload or {}).get("data") or {}).get("roll_data") or []:
        stamp = item.get("ctime")
        published = datetime.fromtimestamp(stamp) if stamp else None
        title = _clean(item.get("title") or item.get("brief"))
        if not title:
            continue
        rows.append(
            {
                "title": title,
                "summary": _clean(item.get("content") or item.get("brief"))[:220],
                "source": "财联社",
                "date": published,
            }
        )
    return rows


def _eastmoney_global(limit: int) -> list[dict]:
    payload = get_json(
        EASTMONEY_GLOBAL_URL,
        params={
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": str(limit),
            "req_trace": str(uuid.uuid4()),
        },
        headers={"Referer": "https://kuaixun.eastmoney.com/"},
        timeout=(6, 20),
    )
    rows = []
    for item in ((payload or {}).get("data") or {}).get("fastNewsList") or []:
        rows.append(
            {
                "title": _clean(item.get("title")),
                "summary": _clean(item.get("summary"))[:220],
                "source": "东方财富 7x24",
                "date": _parse_datetime(item.get("showTime")),
            }
        )
    return rows


def get_global_news(
    as_of_date: Annotated[str, "current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "days to look back"] = None,
    limit: Annotated[int, "max articles"] = None,
) -> str:
    """Market-wide flash news (macro, policy, index-moving headlines) for A-shares."""
    config = get_config()
    days = int(look_back_days if look_back_days is not None else config["global_news_lookback_days"])
    cap = int(limit if limit is not None else config["global_news_article_limit"])
    end_dt = datetime.strptime(as_of_date, "%Y-%m-%d") + timedelta(days=1)
    start_dt = end_dt - timedelta(days=days + 1)

    articles: list[dict] = []
    errors = []
    for name, fetch in (("财联社", _cls_flash), ("东方财富 7x24", _eastmoney_global)):
        try:
            articles.extend(fetch(max(cap * 4, 100)))
        except VendorUnavailableError as exc:
            errors.append(f"{name}: {exc}")
    if not articles:
        raise VendorUnavailableError("no flash-news feed answered: " + "; ".join(errors))

    in_range = [a for a in articles if in_window(a["date"], start_dt, end_dt)]
    if not in_range:
        gap = coverage_gap(
            (a["date"] for a in articles),
            as_of_date,
            as_of_date,
            "A-share flash news",
            "market news",
        )
        return gap or f"No market news found up to {as_of_date}"

    # Undated items (feed sent an unparsable timestamp) sort to the end instead
    # of raising TypeError against dated ones.
    in_range.sort(key=lambda a: a["date"] or datetime.min, reverse=True)
    in_range = in_range[:cap]
    body = "\n\n".join(
        f"### {a['title']}\n- 时间: {_shown_date(a['date'])}\n- 来源: {a['source']}\n- 摘要: {a['summary']}"
        for a in in_range
    )
    return (
        f"## A-share market news, {start_dt:%Y-%m-%d} to {as_of_date} "
        f"({len(in_range)} items, 财联社 + 东方财富 7x24):\n\n{body}"
    )
