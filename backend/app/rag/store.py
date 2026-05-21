"""Chroma vector store for filing chunks.

- Persistent on-disk storage at settings.chroma_persist_dir
- Single collection ("filings") keyed by a deterministic chunk ID
- Idempotent: re-ingesting a filing with the same content_hash is a no-op
- Embedding generation happens here via app.rag.embeddings (Voyage)

Chroma's client is synchronous. We wrap its calls in asyncio.to_thread so
our async route handlers don't block the event loop.
"""

import asyncio
from typing import Final

import chromadb
from pydantic import BaseModel, Field

from app.config import settings
from app.rag.chunker import Chunk
from app.rag.embeddings import embed_documents_stream, embed_query

_COLLECTION_NAME: Final[str] = "filings"

_client: chromadb.api.ClientAPI | None = None
_collection: chromadb.Collection | None = None


def _get_collection() -> chromadb.Collection:
    """Lazily build (and cache) the on-disk client + collection."""
    global _client, _collection
    if _client is None:
        _client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    if _collection is None:
        _collection = _client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


class SearchResult(BaseModel):
    """One chunk returned by a vector search."""

    text: str = Field(..., description="The chunk text.")
    metadata: dict = Field(default_factory=dict, description="Chunk metadata.")
    distance: float = Field(..., description="Cosine distance (smaller = more similar).")


def _chunk_id(chunk: Chunk) -> str:
    return f"{chunk.ticker}_{chunk.accession_number}_{chunk.item_number}_{chunk.chunk_index}"


async def is_filing_ingested(content_hash: str) -> bool:
    """Check whether any chunk with this content_hash already exists."""
    collection = _get_collection()
    result = await asyncio.to_thread(
        collection.get,
        where={"content_hash": content_hash},
        limit=1,
    )
    return len(result["ids"]) > 0


async def ingest_chunks(chunks: list[Chunk]) -> int:
    """Embed and store chunks. Idempotent at the chunk level.

    Each batch commits to Chroma as soon as its embeddings come back, so a
    crash partway through preserves completed batches — re-running resumes
    from the first unembedded chunk.

    Returns the number of newly added chunks.
    """
    if not chunks:
        return 0

    collection = _get_collection()

    all_ids = [_chunk_id(c) for c in chunks]
    existing = await asyncio.to_thread(collection.get, ids=all_ids, include=[])
    existing_ids = set(existing["ids"])
    new_chunks = [c for c in chunks if _chunk_id(c) not in existing_ids]

    if not new_chunks:
        return 0

    if len(new_chunks) < len(chunks):
        print(
            f"  [chroma] resuming: {len(chunks) - len(new_chunks)} chunks already stored, "
            f"embedding {len(new_chunks)} new ones...",
            flush=True,
        )

    total_added = 0
    chunk_iter = iter(new_chunks)

    async for batch_texts, batch_vectors in embed_documents_stream([c.text for c in new_chunks]):
        batch_chunks = [next(chunk_iter) for _ in batch_texts]
        await asyncio.to_thread(
            collection.add,
            ids=[_chunk_id(c) for c in batch_chunks],
            embeddings=batch_vectors,
            documents=[c.text for c in batch_chunks],
            metadatas=[c.metadata for c in batch_chunks],
        )
        total_added += len(batch_chunks)
        print(
            f"  [chroma] committed ({total_added}/{len(new_chunks)} chunks now in store)",
            flush=True,
        )

    return total_added


async def search(
    query: str,
    *,
    k: int = 5,
    ticker: str | None = None,
    item_number: str | None = None,
) -> list[SearchResult]:
    """Search the vector store. Optional filters narrow by ticker / item_number."""
    collection = _get_collection()
    query_vector = await embed_query(query)

    filters: list[dict] = []
    if ticker is not None:
        filters.append({"ticker": ticker.upper()})
    if item_number is not None:
        filters.append({"item_number": item_number})

    where: dict | None = None
    if len(filters) == 1:
        where = filters[0]
    elif len(filters) > 1:
        where = {"$and": filters}

    raw = await asyncio.to_thread(
        collection.query,
        query_embeddings=[query_vector],
        n_results=k,
        where=where,
    )

    results: list[SearchResult] = []
    for doc, meta, dist in zip(
        raw["documents"][0], raw["metadatas"][0], raw["distances"][0], strict=True
    ):
        results.append(SearchResult(text=doc, metadata=dict(meta), distance=dist))
    return results
