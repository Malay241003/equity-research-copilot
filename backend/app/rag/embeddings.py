"""Voyage AI embedding pipeline.

Free-tier rate limits: 3 RPM and 10K TPM. We:
- token-count each text and build batches that stay under 5k tokens
- sleep ~70s between batches to refill the rolling-minute budget
- disable the SDK's own 6 internal retries (they burn RPM in a burst)
- catch RateLimitError ourselves and cool down 75s before retrying
- catch APIConnectionError (Windows kills idle sockets across the long sleeps)
  and drop the client cache so the next attempt opens fresh sockets

After upgrading to a payment method, the caps lift to ~300 RPM / 1M TPM —
zero out the _FREE_TIER_* constants and bump max_retries below.

Entry points:
- embed_documents(texts)        — returns all vectors as a flat list
- embed_documents_stream(texts) — yields (batch_texts, batch_vectors) per batch
                                   for incremental ingestion
- embed_query(query)            — for searching
"""

import asyncio
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Final

import tiktoken
import voyageai
from voyageai.error import APIConnectionError, RateLimitError

from app.config import settings

_VOYAGE_MAX_BATCH_TEXTS: Final[int] = 128
_FREE_TIER_TOKENS_PER_BATCH: Final[int] = 5000
_FREE_TIER_INTER_BATCH_SLEEP: Final[float] = 70.0
_RATE_LIMIT_COOLDOWN_SLEEP: Final[float] = 75.0
_NETWORK_RETRY_SLEEP: Final[float] = 5.0
_MAX_RATE_LIMIT_RETRIES: Final[int] = 4

_client: voyageai.AsyncClient | None = None


def _get_client() -> voyageai.AsyncClient:
    """Lazily build (and cache) the Voyage async client with SDK retries OFF."""
    global _client
    if _client is None:
        if settings.voyage_api_key is None:
            raise RuntimeError("VOYAGE_API_KEY is not set. Add it to backend/.env.")
        _client = voyageai.AsyncClient(
            api_key=settings.voyage_api_key,
            max_retries=0,
        )
    return _client


@lru_cache(maxsize=1)
def _token_counter() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def _make_token_batches(texts: list[str]) -> list[list[str]]:
    """Group texts into batches under both the token-per-batch and text-count caps."""
    encoder = _token_counter()
    batches: list[list[str]] = []
    current_batch: list[str] = []
    current_tokens = 0

    for text in texts:
        text_tokens = len(encoder.encode(text))
        would_exceed_tokens = current_tokens + text_tokens > _FREE_TIER_TOKENS_PER_BATCH
        would_exceed_count = len(current_batch) >= _VOYAGE_MAX_BATCH_TEXTS
        if current_batch and (would_exceed_tokens or would_exceed_count):
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0
        current_batch.append(text)
        current_tokens += text_tokens

    if current_batch:
        batches.append(current_batch)
    return batches


async def _embed_batch_with_retry(batch: list[str], input_type: str) -> list[list[float]]:
    """Send one batch, retrying on rate-limit or transient network errors."""
    global _client
    last_error: Exception | None = None
    for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
        try:
            client = _get_client()
            result = await client.embed(
                batch,
                model=settings.voyage_embedding_model,
                input_type=input_type,
            )
            return result.embeddings
        except RateLimitError as exc:
            last_error = exc
            if attempt == _MAX_RATE_LIMIT_RETRIES:
                break
            print(
                f"  [rate limited] cooling down {_RATE_LIMIT_COOLDOWN_SLEEP:.0f}s "
                f"(retry {attempt + 1}/{_MAX_RATE_LIMIT_RETRIES})...",
                flush=True,
            )
            await asyncio.sleep(_RATE_LIMIT_COOLDOWN_SLEEP)
        except APIConnectionError as exc:
            last_error = exc
            _client = None  # force fresh sockets on retry
            if attempt == _MAX_RATE_LIMIT_RETRIES:
                break
            print(
                f"  [connection error] reconnecting in {_NETWORK_RETRY_SLEEP:.0f}s "
                f"(retry {attempt + 1}/{_MAX_RATE_LIMIT_RETRIES})...",
                flush=True,
            )
            await asyncio.sleep(_NETWORK_RETRY_SLEEP)
    assert last_error is not None
    raise last_error


async def _embed_stream(
    texts: list[str], *, input_type: str
) -> AsyncIterator[tuple[list[str], list[list[float]]]]:
    """Internal generator: yields (batch_texts, batch_vectors) one batch at a time."""
    global _client
    if not texts:
        return
    batches = _make_token_batches(texts)
    for i, batch in enumerate(batches):
        if i > 0:
            _client = None
            print(
                f"  [voyage free tier] waiting {_FREE_TIER_INTER_BATCH_SLEEP:.0f}s "
                f"before batch {i + 1}/{len(batches)}...",
                flush=True,
            )
            await asyncio.sleep(_FREE_TIER_INTER_BATCH_SLEEP)
        else:
            print(f"  [voyage free tier] sending batch 1/{len(batches)}...", flush=True)
        vectors = await _embed_batch_with_retry(batch, input_type)
        print(f"  [voyage free tier] batch {i + 1}/{len(batches)} embedded", flush=True)
        yield batch, vectors


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed chunk texts for indexing. Convenience wrapper — collects all batches."""
    all_vectors: list[list[float]] = []
    async for _, vectors in _embed_stream(texts, input_type="document"):
        all_vectors.extend(vectors)
    return all_vectors


async def embed_documents_stream(
    texts: list[str],
) -> AsyncIterator[tuple[list[str], list[list[float]]]]:
    """Yields (batch_texts, batch_vectors) per batch — for incremental ingestion."""
    async for batch in _embed_stream(texts, input_type="document"):
        yield batch


async def embed_query(query: str) -> list[float]:
    """Embed a single search query."""
    all_vectors: list[list[float]] = []
    async for _, vectors in _embed_stream([query], input_type="query"):
        all_vectors.extend(vectors)
    return all_vectors[0]
