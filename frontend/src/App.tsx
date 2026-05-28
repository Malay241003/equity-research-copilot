import { useState } from "react";

import ReportView from "@/components/ReportView";
import TickerInput from "@/components/TickerInput";

/**
 * App — single-page shell.
 *
 * Two modes:
 *   1. Idle: show the TickerInput hero.
 *   2. Researching: show the ReportView, which opens an SSE stream to the
 *      backend and renders the report as it materialises.
 *
 * Switching back to idle is just clearing the active ticker. State lives
 * here (not in URL params yet — that's a Phase 6 "shareable links" task).
 */

interface ActiveResearch {
  ticker: string;
  query: string | null;
  // Bumping `nonce` forces a fresh SSE connection even for the same ticker
  // — useful if the user retries after a transient backend hiccup.
  nonce: number;
}

function App() {
  const [active, setActive] = useState<ActiveResearch | null>(null);

  function handleSubmit(ticker: string, query: string | null) {
    setActive({ ticker, query, nonce: Date.now() });
  }

  function handleReset() {
    setActive(null);
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold">Equity Research Copilot</h1>
            <p className="text-xs text-muted-foreground">
              Agentic equity research over SEC filings, market data, and news
            </p>
          </div>
          <span className="text-xs text-muted-foreground hidden sm:inline">
            Phase 3 — streaming demo
          </span>
        </div>
      </header>

      <main className="px-4 py-8">
        {active === null ? (
          <div className="max-w-4xl mx-auto flex flex-col items-center text-center pt-12">
            <h2 className="text-4xl font-semibold tracking-tight mb-3">
              Research any S&amp;P 500 company
            </h2>
            <p className="text-muted-foreground mb-8 max-w-xl">
              Enter a ticker and get a structured report with citations across
              eight dimensions — financial health, growth, risks, competition,
              valuation, macro &amp; sector context, sentiment &amp; momentum,
              and recent catalysts — streamed live as the agent works.
            </p>
            <TickerInput onSubmit={handleSubmit} />
            <p className="text-xs text-muted-foreground mt-6 max-w-md">
              First-time ingest for a ticker takes a few minutes (filings get
              embedded). Already-indexed tickers stream in ~20 seconds.
            </p>
          </div>
        ) : (
          <ReportView
            key={`${active.ticker}-${active.nonce}`}
            ticker={active.ticker}
            query={active.query}
            onReset={handleReset}
          />
        )}
      </main>
    </div>
  );
}

export default App;
