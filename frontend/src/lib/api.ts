/**
 * Backend API types and helpers.
 *
 * Mirrors the Pydantic models in `backend/app/agents/state.py` so the frontend
 * gets type-safety end-to-end. When the backend schema changes, edit BOTH
 * places (Phase 4-onward we may generate these from the FastAPI OpenAPI spec
 * to remove the manual sync, but for now hand-keeping it is fine).
 */

export const API_BASE = "http://localhost:8000";

export type SectionName =
  // Original 5 (Phase 2)
  | "financial_health"
  | "growth"
  | "risks"
  | "competition"
  | "valuation"
  // Phase 3.5 additions
  | "macro_context"
  | "sentiment_momentum"
  | "catalysts";

export const ALL_SECTIONS: SectionName[] = [
  "financial_health",
  "growth",
  "risks",
  "competition",
  "valuation",
  "macro_context",
  "sentiment_momentum",
  "catalysts",
];

export const SECTION_LABELS: Record<SectionName, string> = {
  financial_health: "Financial Health",
  growth: "Growth",
  risks: "Risks",
  competition: "Competition",
  valuation: "Valuation",
  macro_context: "Macro & Sector Context",
  sentiment_momentum: "Sentiment & Momentum",
  catalysts: "Catalysts & Recent Events",
};

export interface Plan {
  ticker: string;
  company_name: string | null;
  research_focus: string;
  sections_to_run: SectionName[];
}

export interface Citation {
  source_type: "filing" | "yfinance" | "news";
  source_id: string;
  quote: string | null;
}

export interface SectionOutput {
  section: SectionName;
  summary: string;
  key_points: string[];
  citations: Citation[];
}

export interface Report {
  ticker: string;
  company_name: string | null;
  generated_at: string; // ISO date
  headline: string;
  sections: Partial<Record<SectionName, SectionOutput>>;
  bottom_line: string;
  cost_usd: number;
}

// ─── Phase events emitted by the SSE endpoint ─────────────────────

export type PipelinePhase =
  | "planning"
  | "fetching"
  | "indexing"
  | "analyzing"
  | "synthesizing"
  | "complete";

export type FetcherSource = "yfinance" | "filings" | "news";

// ─── Citation detail (GET /citation/{source_id}) ──────────────────

export interface Threshold {
  min: number | null;
  max: number | null;
  label: string;
}

export interface MetricDefinition {
  name: string;
  definition: string;
  unit?: string | null;
  thresholds: Threshold[];
  note?: string | null;
}

export interface CitationDetail {
  source_type: "filing" | "yfinance" | "news";
  source_id: string;

  // Filing-only
  text?: string | null;
  ticker?: string | null;
  item_number?: string | null;
  section_title?: string | null;
  filing_date?: string | null;
  accession_number?: string | null;

  // yfinance-only
  metric_name?: string | null;

  // news-only
  url?: string | null;

  // Educational metadata for yfinance/technical metrics
  definition?: MetricDefinition | null;

  // Live-data snapshot — populated for metrics that move in real-time
  // (currently just vix_level). Lets the popover show "X.XX as of YYYY-MM-DD"
  // without needing a separate endpoint round-trip.
  live_value?: number | null;
  live_as_of?: string | null;
}

export async function fetchCitation(
  source_id: string,
): Promise<CitationDetail> {
  const res = await fetch(
    `${API_BASE}/citation/${encodeURIComponent(source_id)}`,
  );
  if (!res.ok) {
    const body = await res
      .json()
      .catch(() => ({ detail: `HTTP ${res.status}` }));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return (await res.json()) as CitationDetail;
}

// ─── Price history (GET /price/{ticker}?period=...) ───────────────

export interface PricePoint {
  date: string; // ISO date
  close: number;
  volume: number;
}

export interface PriceHistory {
  ticker: string;
  period: string;
  points: PricePoint[];
}

export type PricePeriod = "1mo" | "3mo" | "1y" | "5y";

export async function fetchPriceHistory(
  ticker: string,
  period: PricePeriod,
): Promise<PriceHistory> {
  const res = await fetch(
    `${API_BASE}/price/${encodeURIComponent(ticker)}?period=${period}`,
  );
  if (!res.ok) {
    const body = await res
      .json()
      .catch(() => ({ detail: `HTTP ${res.status}` }));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return (await res.json()) as PriceHistory;
}

// ─── Chat (POST /chat/{ticker}) ───────────────────────────────────

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatResponse {
  content: string;
  input_tokens: number | null;
  output_tokens: number | null;
}

export async function postChat(
  ticker: string,
  messages: ChatMessage[],
  report: Report,
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/chat/${encodeURIComponent(ticker)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, report }),
  });
  if (!res.ok) {
    const body = await res
      .json()
      .catch(() => ({ detail: `HTTP ${res.status}` }));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return (await res.json()) as ChatResponse;
}
