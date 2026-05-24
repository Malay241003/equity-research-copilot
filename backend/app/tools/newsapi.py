"""NewsAPI client.

Phase 2 upgrade from the yfinance news widget (kept as legacy in `news.py`).
NewsAPI is a real search API with quality-ranked results — far cleaner than
scraping Yahoo's "stories that might interest a {ticker} viewer" widget.

Free tier: 100 requests/day, results delayed 24h, dev/local use only.
Sign up at https://newsapi.org/ and set NEWSAPI_KEY in backend/.env.

Cached in-process for 24 hours (matches the project-wide news cache policy)
so a single research run never burns more than one quota slot per ticker.
"""

import time
from datetime import datetime
from typing import Any, Final

import httpx
from pydantic import BaseModel, Field

from app.config import settings

NEWSAPI_BASE_URL: Final[str] = "https://newsapi.org/v2/everything"
DEFAULT_TIMEOUT: Final[float] = 30.0
_CACHE_TTL_SECONDS: Final[float] = 86400.0  # 24 hours

# Cache key = (ticker, name) so two callers querying the same ticker but
# with/without the company name share results when they should.
_news_cache: dict[tuple[str, str], tuple[float, list["NewsArticle"]]] = {}


class NewsAPIError(Exception):
    """Raised when NewsAPI returns an error response.

    Common causes: missing/invalid key (401), daily quota exhausted (429),
    or a 5xx from their side. Callers decide whether to surface or fall back.
    """


class NewsArticle(BaseModel):
    """One article from NewsAPI, normalised for analyzer consumption."""

    title: str
    publisher: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    description: str | None = Field(
        default=None,
        description="One-paragraph snippet NewsAPI returns alongside the title.",
    )


def _parse_published(value: Any) -> datetime | None:
    """NewsAPI sends ISO-8601 with a trailing Z; fromisoformat wants +00:00."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_article(raw: dict) -> NewsArticle | None:
    """Build a NewsArticle from one raw NewsAPI item. Skip 'removed' placeholders."""
    title = raw.get("title")
    if not title or title == "[Removed]":
        return None
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    return NewsArticle(
        title=title,
        publisher=source.get("name"),
        url=raw.get("url"),
        published_at=_parse_published(raw.get("publishedAt")),
        description=raw.get("description"),
    )


def _build_query(ticker: str, name: str | None) -> str:
    """Compose a NewsAPI `q` parameter.

    NewsAPI does not understand tickers — it's a plain keyword search. When
    we have the company name we OR them together so a story about "Apple's
    new iPhone" matches even if it never types "AAPL" explicitly. Quotes
    around the name keep multi-word names from being tokenised.
    """
    if name:
        return f'"{name}" OR {ticker}'
    return ticker


async def get_news(
    ticker: str,
    *,
    name: str | None = None,
    page_size: int = 20,
) -> list[NewsArticle]:
    """Fetch recent news for a ticker. Cached in-process for 24h.

    Pass `name` (e.g. "Apple") whenever you have it — articles that reference
    the company by name but not the ticker symbol are otherwise missed.

    Raises NewsAPIError on auth / rate-limit / API failures so the caller
    can decide whether to surface the error or fall back silently.
    """
    if not settings.newsapi_key:
        raise NewsAPIError(
            "NEWSAPI_KEY is not set in backend/.env. Get a free key at https://newsapi.org/."
        )

    ticker_upper = ticker.upper()
    cache_key = (ticker_upper, name or "")
    now = time.time()
    cached = _news_cache.get(cache_key)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    params = {
        "q": _build_query(ticker_upper, name),
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": page_size,
    }
    headers = {"X-Api-Key": settings.newsapi_key}

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.get(NEWSAPI_BASE_URL, params=params, headers=headers)

    if response.status_code == 401:
        raise NewsAPIError("NewsAPI rejected the API key (401). Check NEWSAPI_KEY in .env.")
    if response.status_code == 429:
        raise NewsAPIError("NewsAPI rate limit hit (429). Free tier allows 100 requests/day.")
    response.raise_for_status()

    payload = response.json()
    if payload.get("status") != "ok":
        raise NewsAPIError(
            f"NewsAPI returned status={payload.get('status')!r}: {payload.get('message')!r}"
        )

    articles: list[NewsArticle] = []
    for raw in payload.get("articles", []):
        if not isinstance(raw, dict):
            continue
        parsed = _parse_article(raw)
        if parsed is not None:
            articles.append(parsed)

    _news_cache[cache_key] = (now, articles)
    return articles
