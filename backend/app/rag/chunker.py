"""Section-aware chunker.

Slices each ParsedSection into ~chunk_size token chunks with chunk_overlap
overlap. Chunks never cross section boundaries — that's the "section-aware"
part. Token boundaries come from tiktoken (cl100k_base, OpenAI GPT-3.5/4's
tokenizer), used here purely as a stable token counter — not tied to any
specific embedding model.

Each chunk carries metadata (ticker, filing_type, filing_date, section,
item_number, chunk_index) so retrieval results can be attributed back to a
specific filing + section.
"""

from datetime import date
from functools import lru_cache

import tiktoken
from pydantic import BaseModel, Field

from app.config import settings
from app.rag.parser import ParsedFiling


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    """Lazily load the GPT-3.5/4 tokenizer once per process."""
    return tiktoken.get_encoding("cl100k_base")


class Chunk(BaseModel):
    """A retrievable chunk of text with full provenance metadata."""

    text: str = Field(..., description="The chunk's text content.")
    ticker: str = Field(..., description="Stock ticker, uppercase.")
    filing_type: str = Field(..., description="10-K or 10-Q.")
    filing_date: date
    accession_number: str
    section_title: str
    item_number: str
    chunk_index: int = Field(..., description="0-based index within the section.")
    content_hash: str = Field(..., description="Parent filing's content hash.")

    @property
    def metadata(self) -> dict[str, str | int]:
        """Flat metadata dict suitable for Chroma's `where` filtering."""
        return {
            "ticker": self.ticker,
            "filing_type": self.filing_type,
            "filing_date": self.filing_date.isoformat(),
            "accession_number": self.accession_number,
            "section_title": self.section_title,
            "item_number": self.item_number,
            "chunk_index": self.chunk_index,
            "content_hash": self.content_hash,
        }


def _split_into_token_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Slice a single text into token-bounded chunks with overlap."""
    if overlap >= chunk_size:
        raise ValueError(f"overlap ({overlap}) must be less than chunk_size ({chunk_size}).")

    encoding = _encoding()
    tokens = encoding.encode(text)
    if not tokens:
        return []
    if len(tokens) <= chunk_size:
        return [text]

    step = chunk_size - overlap
    chunks: list[str] = []
    for start in range(0, len(tokens), step):
        end = start + chunk_size
        chunks.append(encoding.decode(tokens[start:end]))
        if end >= len(tokens):
            break
    return chunks


def chunk_filing(
    parsed: ParsedFiling,
    *,
    ticker: str,
    filing_date: date,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[Chunk]:
    """Chunk every section of a parsed filing. Returns a flat list of Chunks."""
    size = chunk_size if chunk_size is not None else settings.chunk_size
    overlap = chunk_overlap if chunk_overlap is not None else settings.chunk_overlap

    all_chunks: list[Chunk] = []
    for section in parsed.sections:
        section_chunks = _split_into_token_chunks(section.text, size, overlap)
        for i, chunk_text in enumerate(section_chunks):
            all_chunks.append(
                Chunk(
                    text=chunk_text,
                    ticker=ticker.upper(),
                    filing_type=parsed.form,
                    filing_date=filing_date,
                    accession_number=parsed.accession_number,
                    section_title=section.title,
                    item_number=section.item_number,
                    chunk_index=i,
                    content_hash=parsed.content_hash,
                )
            )
    return all_chunks
