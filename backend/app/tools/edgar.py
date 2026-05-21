"""SEC EDGAR client.

Two-step lookup:
  1. Resolve ticker -> CIK using SEC's company_tickers.json (cached after first call).
  2. Fetch the company's submissions JSON, which has the full filing history.

Primary HTML documents are then fetched on demand from the SEC archive.

Every request must include a User-Agent in the literal format
"Name email@example.com"; we send the value from settings.edgar_user_agent.
"""

from datetime import date
from typing import Final

import httpx
from pydantic import BaseModel, Field

from app.config import settings

TICKER_MAP_URL: Final[str] = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TEMPLATE: Final[str] = "https://data.sec.gov/submissions/CIK{cik}.json"
DOCUMENT_URL_TEMPLATE: Final[str] = (
    "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_no_dashes}/{filename}"
)
DEFAULT_TIMEOUT: Final[float] = 30.0


class Filing(BaseModel):
    """A single SEC filing for a company."""

    cik: str = Field(..., description="Central Index Key, zero-padded to 10 digits.")
    ticker: str = Field(..., description="Stock ticker, uppercase.")
    accession_number: str = Field(
        ..., description='SEC accession number, e.g. "0000320193-25-000067".'
    )
    form: str = Field(..., description='Filing form type, e.g. "10-K", "10-Q".')
    filing_date: date = Field(..., description="Date SEC accepted the filing.")
    report_date: date | None = Field(
        default=None, description="Period the filing covers (None if not provided)."
    )
    primary_document: str = Field(
        ..., description='Filename of the primary document, e.g. "aapl-20240928.htm".'
    )

    @property
    def document_url(self) -> str:
        """URL to fetch this filing's primary HTML document."""
        cik_int = int(self.cik)
        accession_no_dashes = self.accession_number.replace("-", "")
        return DOCUMENT_URL_TEMPLATE.format(
            cik_int=cik_int,
            accession_no_dashes=accession_no_dashes,
            filename=self.primary_document,
        )


# Cached ticker -> CIK lookup. Populated on first call; None means not yet loaded.
# Single-process cache only; restart the server to refresh.
_ticker_to_cik: dict[str, str] | None = None


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": settings.edgar_user_agent,
        "Accept-Encoding": "gzip, deflate",
    }


async def _get_cik(client: httpx.AsyncClient, ticker: str) -> str:
    """Resolve a ticker to its zero-padded CIK. Caches the full map after first call."""
    global _ticker_to_cik

    if _ticker_to_cik is None:
        response = await client.get(TICKER_MAP_URL)
        response.raise_for_status()
        raw = response.json()
        _ticker_to_cik = {
            entry["ticker"].upper(): str(entry["cik_str"]).zfill(10) for entry in raw.values()
        }

    ticker_upper = ticker.upper()
    if ticker_upper not in _ticker_to_cik:
        raise ValueError(f"Ticker {ticker!r} not found in SEC ticker map.")
    return _ticker_to_cik[ticker_upper]


async def list_filings(
    ticker: str,
    form_types: tuple[str, ...] = ("10-K", "10-Q"),
    limit: int = 10,
) -> list[Filing]:
    """List recent filings for a ticker, filtered to form_types, newest first.

    Raises ValueError if the ticker is not registered with SEC.
    Raises httpx.HTTPStatusError on non-2xx responses from SEC.
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=_request_headers()) as client:
        cik = await _get_cik(client, ticker)
        url = SUBMISSIONS_URL_TEMPLATE.format(cik=cik)
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    # SEC returns parallel arrays under filings.recent — zip them ourselves.
    recent = data.get("filings", {}).get("recent", {})
    accession_numbers = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    forms = recent.get("form", [])
    primary_documents = recent.get("primaryDocument", [])

    filings: list[Filing] = []
    for i, form in enumerate(forms):
        if form not in form_types:
            continue
        # reportDate can be empty string when SEC didn't record one.
        rd_raw = report_dates[i] if i < len(report_dates) else ""
        report_date_parsed = date.fromisoformat(rd_raw) if rd_raw else None
        filings.append(
            Filing(
                cik=cik,
                ticker=ticker.upper(),
                accession_number=accession_numbers[i],
                form=form,
                filing_date=date.fromisoformat(filing_dates[i]),
                report_date=report_date_parsed,
                primary_document=primary_documents[i],
            )
        )
        if len(filings) >= limit:
            break

    return filings


async def fetch_filing_html(filing: Filing) -> str:
    """Fetch the raw HTML of a filing's primary document.

    Raises httpx.HTTPStatusError on non-2xx responses from SEC.
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=_request_headers()) as client:
        response = await client.get(filing.document_url)
        response.raise_for_status()
        return response.text
