"""News client.

Uses yfinance's news widget (no API key, no signup, no rate-limit contract).
Cached in-process for 24h per ARCHITECTURE.md.

Quality caveat: Yahoo's widget is "stories interesting to an AAPL viewer,"
not strictly "stories about AAPL" — so general market chatter leaks in. We
filter post-fetch by `relatedTickers`: only articles Yahoo tagged with the
requested ticker survive. The remaining set is still loose (a VTI ETF
article tagged with AAPL because Apple is the top holding will pass) but
much cleaner than the raw widget. For real-quality news in Phase 2+,
consider switching to NewsAPI or Marketaux — see [[yfinance-fragility-risk]].

yfinance's news payload shape has shifted between versions — newer releases
wrap each item in a `content` object, older releases return fields flat.
The parser tolerates both shapes.
"""

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, Final

import yfinance as yf
from pydantic import BaseModel, Field

_CACHE_TTL_SECONDS: Final[float] = 86400.0  # 24 hours

_news_cache: dict[tuple[str, str], tuple[float, list["NewsArticle"]]] = {}


class NewsArticle(BaseModel):
    title: str
    publisher: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    related_tickers: list[str] = Field(default_factory=list)


def _coerce_url(value: Any) -> str | None:
    """yfinance sometimes nests URLs under `canonicalUrl.url` or `clickThroughUrl.url`."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("url")
    return None


def _coerce_publisher(item: dict, content: dict) -> str | None:
    """Newer payloads use `provider.displayName`; older ones use `publisher`."""
    provider = content.get("provider")
    if isinstance(provider, dict):
        name = provider.get("displayName")
        if name:
            return name
    return item.get("publisher") or content.get("publisher")


def _coerce_published_at(content: dict) -> datetime | None:
    """Try ISO-string fields first, fall back to epoch-seconds field."""
    iso = content.get("pubDate") or content.get("displayTime")
    if isinstance(iso, str) and iso:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except ValueError:
            pass
    epoch = content.get("providerPublishTime")
    if isinstance(epoch, int | float):
        return datetime.fromtimestamp(epoch, tz=UTC)
    return None


def _parse_article(item: dict) -> NewsArticle | None:
    """Parse one raw yfinance news item, tolerating both old and new shapes."""
    content = item.get("content") if isinstance(item.get("content"), dict) else item

    title = content.get("title") or item.get("title")
    if not title:
        return None

    url = (
        _coerce_url(content.get("canonicalUrl"))
        or _coerce_url(content.get("clickThroughUrl"))
        or item.get("link")
        or content.get("link")
    )

    related = content.get("relatedTickers") or item.get("relatedTickers") or []
    if not isinstance(related, list):
        related = []

    return NewsArticle(
        title=title,
        publisher=_coerce_publisher(item, content),
        url=url,
        published_at=_coerce_published_at(content),
        related_tickers=[str(t) for t in related],
    )


def _is_relevant(article: NewsArticle, ticker: str, name: str | None) -> bool:
    """Keep articles tagged with our ticker, OR with the ticker symbol in the
    title, OR with the company name in the title. Drops generic market chatter."""
    target_ticker = ticker.upper()
    title_upper = article.title.upper()
    if article.related_tickers and target_ticker in [rt.upper() for rt in article.related_tickers]:
        return True
    if target_ticker in title_upper:
        return True
    return bool(name and name.upper() in title_upper)


def _fetch_news_sync(ticker: str, name: str | None) -> list[NewsArticle]:
    """Sync worker — call via asyncio.to_thread."""
    handle = yf.Ticker(ticker)
    raw_news = handle.news or []
    articles: list[NewsArticle] = []
    for item in raw_news:
        if not isinstance(item, dict):
            continue
        article = _parse_article(item)
        if article is not None and _is_relevant(article, ticker, name):
            articles.append(article)
    return articles


async def get_news(ticker: str, *, name: str | None = None) -> list[NewsArticle]:
    """Fetch recent news articles for a ticker. Cached in-process for 24h.

    Pass `name` (company short name, e.g. "Apple") for better filtering —
    catches articles that mention the company by name without the ticker.
    """
    key = (ticker.upper(), name or "")
    now = time.time()
    cached = _news_cache.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    result = await asyncio.to_thread(_fetch_news_sync, ticker, name)
    _news_cache[key] = (now, result)
    return result
