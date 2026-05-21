import { useState } from "react";

interface FilingSummary {
  accession_number: string;
  form: string;
  filing_date: string;
  sections: number;
  chunks_added: number;
}

interface IngestResponse {
  ticker: string;
  chunks_added: number;
  filings: FilingSummary[];
}

interface SearchHit {
  text: string;
  distance: number;
  item_number: string;
  section_title: string;
  filing_date: string;
  accession_number: string;
}

interface SearchResponse {
  query: string;
  ticker: string;
  results: SearchHit[];
}

const API_BASE = "http://localhost:8000";

export default function IngestSearchPanel() {
  const [ticker, setTicker] = useState<string>("AAPL");

  const [ingestLoading, setIngestLoading] = useState<boolean>(false);
  const [ingestResult, setIngestResult] = useState<IngestResponse | null>(null);
  const [ingestError, setIngestError] = useState<string | null>(null);

  const [query, setQuery] = useState<string>("What are the main risk factors?");
  const [searchLoading, setSearchLoading] = useState<boolean>(false);
  const [searchResult, setSearchResult] = useState<SearchResponse | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);

  async function handleIngest() {
    setIngestLoading(true);
    setIngestResult(null);
    setIngestError(null);
    try {
      const symbol = encodeURIComponent(ticker.trim().toUpperCase());
      const res = await fetch(`${API_BASE}/ingest/${symbol}`, {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res
          .json()
          .catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      const data: IngestResponse = await res.json();
      setIngestResult(data);
    } catch (err) {
      setIngestError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setIngestLoading(false);
    }
  }

  async function handleSearch() {
    setSearchLoading(true);
    setSearchResult(null);
    setSearchError(null);
    try {
      const symbol = encodeURIComponent(ticker.trim().toUpperCase());
      const params = new URLSearchParams({ q: query, k: "5" });
      const res = await fetch(`${API_BASE}/search/${symbol}?${params}`);
      if (!res.ok) {
        const body = await res
          .json()
          .catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      const data: SearchResponse = await res.json();
      setSearchResult(data);
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSearchLoading(false);
    }
  }

  return (
    <section style={{ marginTop: "2rem" }}>
      <h2>Ingest + Search demo</h2>
      <p style={{ color: "#666" }}>
        Hits <code>POST /ingest/{`{ticker}`}</code> to fetch + embed the latest
        10-K, then <code>GET /search/{`{ticker}`}?q=…</code> to vector-search
        it. First-time ingests for a fresh ticker take ~8 min on Voyage free
        tier; idempotent re-runs are instant.
      </p>

      {/* ─── Ticker + Ingest ───────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          gap: "1rem",
          alignItems: "center",
          marginTop: "1rem",
        }}
      >
        <label>
          Ticker:{" "}
          <input
            type="text"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            maxLength={5}
            style={{
              padding: "0.4rem",
              fontSize: "1rem",
              width: "6rem",
              fontFamily: "inherit",
            }}
          />
        </label>
        <button
          type="button"
          onClick={handleIngest}
          disabled={ingestLoading || ticker.trim().length === 0}
          style={{
            padding: "0.5rem 1rem",
            fontSize: "1rem",
            cursor: ingestLoading ? "wait" : "pointer",
          }}
        >
          {ingestLoading ? "Ingesting…" : "Ingest"}
        </button>
      </div>

      {ingestError && (
        <p style={{ color: "crimson", marginTop: "1rem" }}>
          Ingest failed: <code>{ingestError}</code>
        </p>
      )}

      {ingestResult && (
        <div
          style={{
            marginTop: "1rem",
            padding: "1rem",
            border: "1px solid #ddd",
            borderRadius: 6,
            background: "#fafafa",
          }}
        >
          <p style={{ margin: 0 }}>
            Ingested <strong>{ingestResult.chunks_added}</strong> new chunks for{" "}
            <strong>{ingestResult.ticker}</strong>.
            {ingestResult.chunks_added === 0 &&
              " (Already in the store — idempotent skip.)"}
          </p>
          <ul style={{ marginTop: "0.5rem", marginBottom: 0 }}>
            {ingestResult.filings.map((f) => (
              <li key={f.accession_number}>
                {f.form} — {f.filing_date} — {f.sections} sections,{" "}
                {f.chunks_added} chunks added
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ─── Query + Search ────────────────────────────────────── */}
      <div style={{ marginTop: "1.5rem" }}>
        <label style={{ display: "block" }}>
          Query:
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{
              padding: "0.4rem",
              fontSize: "1rem",
              width: "100%",
              fontFamily: "inherit",
              boxSizing: "border-box",
              marginTop: "0.25rem",
            }}
          />
        </label>
      </div>

      <button
        type="button"
        onClick={handleSearch}
        disabled={
          searchLoading ||
          query.trim().length === 0 ||
          ticker.trim().length === 0
        }
        style={{
          marginTop: "0.5rem",
          padding: "0.5rem 1rem",
          fontSize: "1rem",
          cursor: searchLoading ? "wait" : "pointer",
        }}
      >
        {searchLoading ? "Searching…" : "Search"}
      </button>

      {searchError && (
        <p style={{ color: "crimson", marginTop: "1rem" }}>
          Search failed: <code>{searchError}</code>
        </p>
      )}

      {searchResult && (
        <div style={{ marginTop: "1rem" }}>
          <p style={{ color: "#666" }}>
            {searchResult.results.length} result(s) for{" "}
            <code>{searchResult.query}</code>
          </p>
          {searchResult.results.map((hit, i) => (
            <div
              key={`${hit.accession_number}-${i}`}
              style={{
                marginBottom: "0.75rem",
                padding: "0.75rem",
                border: "1px solid #ddd",
                borderRadius: 6,
                background: "#fafafa",
              }}
            >
              <p style={{ margin: 0, fontSize: "0.875rem", color: "#666" }}>
                Item {hit.item_number} — {hit.section_title} · distance{" "}
                {hit.distance.toFixed(4)}
              </p>
              <p
                style={{
                  marginTop: "0.5rem",
                  marginBottom: 0,
                  whiteSpace: "pre-wrap",
                }}
              >
                {hit.text.slice(0, 500)}
                {hit.text.length > 500 ? "…" : ""}
              </p>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
