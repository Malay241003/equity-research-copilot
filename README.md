# Equity Research Copilot

> An agentic AI system that performs equity research on publicly traded
> companies. Enter a ticker, get a structured research report (financial
> health, growth, risks, competition, valuation, macro context, sentiment
> & momentum, recent catalysts) streamed live with cited sources, then chat
> with the system for grounded follow-ups.

> **Educational and research tool, NOT financial advice.** This is a
> portfolio/learning project, not a financial product.

---

## Status

In active development. Phases 0–3 complete; currently entering
**Phase 4 of 7 (Evaluation Framework)**.

Working today:

- FastAPI backend, React + Tailwind + shadcn/ui frontend, AWS Bedrock
  (Amazon Nova Pro / Lite) LLM calls, LangSmith tracing.
- End-to-end RAG pipeline: SEC EDGAR client, 10-K parser, section-aware
  chunker, Voyage `voyage-finance-2` embeddings, Chroma vector store.
- **LangGraph agent**: planner → 3 parallel fetchers → indexer → 8
  parallel analyzers → synthesizer. Eight sections: financial health,
  growth, risks, competition, valuation, macro context, sentiment &
  momentum, catalysts.
- **Streaming UI** via Server-Sent Events. Section cards reveal as
  analyzers complete; synthesizer headline + bottom line type out
  token-by-token; cost-per-report badge in the header.
- **Computed technical indicators** (MA20/50/200, ATR, momentum) from
  the price history, plus VIX, and the last 4 quarters of earnings
  beats/misses wired into the relevant analyzers.
- **Citation popovers** — clicking a metric badge shows a plain-English
  definition, interpretation thresholds, and (for live metrics like
  VIX) the current value with as-of timestamp.
- **Follow-up chat** grounded in the generated report.
- HTTP endpoints (see [`backend/CLAUDE.md`](./backend/CLAUDE.md) for
  the full table): `/health`, `/info`, `/generate`, `/ingest/{ticker}`,
  `/search/{ticker}`, `/research/{ticker}`,
  `/research/{ticker}/stream`, `/citation/{source_id}`,
  `/price/{ticker}`, `/chat/{ticker}`.

## Tech stack

| Layer           | Choice                                                              |
| --------------- | ------------------------------------------------------------------- |
| Backend         | FastAPI on Python 3.13, deps via [`uv`](https://docs.astral.sh/uv/) |
| Agent framework | [LangGraph](https://langchain-ai.github.io/langgraph/)              |
| Primary LLM     | Amazon Nova Pro / Lite via AWS Bedrock                              |
| Vector DB       | [Chroma](https://docs.trychroma.com/)                               |
| Embeddings      | Voyage `voyage-finance-2`                                           |
| Frontend        | React 19 + Vite + TypeScript + Tailwind v4 + shadcn/ui + Recharts   |
| Streaming       | Server-Sent Events (SSE)                                            |
| Tracing         | [LangSmith](https://smith.langchain.com/)                           |
| Deploy          | Docker + Railway (Phase 6)                                          |

## Prerequisites

| Tool                               | Minimum version                      | Install (Windows / `winget`)       |
| ---------------------------------- | ------------------------------------ | ---------------------------------- |
| Python                             | 3.13                                 | bundled via `uv` (next row)        |
| [`uv`](https://docs.astral.sh/uv/) | latest                               | `winget install astral-sh.uv`      |
| Node.js                            | 20.19 LTS or newer (24+ recommended) | `winget install OpenJS.NodeJS.LTS` |
| `git`                              | any recent                           | `winget install Git.Git`           |

> Windows PowerShell users: run
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` once
> so that `npm.ps1` is allowed to execute. Answer `Y` when prompted.

## Setup

### 1. Clone

```bash
git clone https://github.com/Malay241003/equity-research-copilot.git
cd equity-research-copilot
```

### 2. Configure secrets

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env` and fill in:

- `LLM_PROVIDER=bedrock` (or `ollama` for free local fallback)
- AWS credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`)
- `LANGSMITH_API_KEY`: get one at [smith.langchain.com](https://smith.langchain.com/)
- `VOYAGE_API_KEY`: get one at [voyageai.com](https://www.voyageai.com/)
  (free tier: 50M tokens for `voyage-finance-2`, no credit card required)
- `EDGAR_USER_AGENT`: SEC requires this in the literal format
  `"Your Name your.email@example.com"`; they 403 requests without it

The `.env` file is gitignored; it lives only on your machine.

### 3. Backend

```bash
cd backend
uv sync              # creates .venv and installs deps from uv.lock
uv run uvicorn app.main:app --reload
```

Server runs on **http://localhost:8000**. OpenAPI docs at **/docs**.

### 4. Frontend (new terminal)

```bash
cd frontend
npm install
npm run dev
```

UI runs on **http://localhost:5173**. Enter an S&P 500 ticker (try AAPL,
NVDA, MSFT) and watch the report stream in.

> First-time ingest of a new ticker takes ~8 minutes on Voyage's free
> tier due to its 3 RPM / 10K TPM rate limit. Idempotent re-runs are
> instant. Subsequent research runs on the same ticker take ~25 s
> (filings already in Chroma; only LLM calls).

## Project layout

```
equity-research-copilot/
├── README.md
├── ARCHITECTURE.md           # 8-week build plan + design principles
├── CLAUDE.md                 # Active-phase context for Claude
├── docs/
│   └── PHASE_NOTES.md        # Live notes for the current phase
├── studyMaterial/            # Per-phase concept/learning notes
│   ├── README.md
│   └── phase3.md
├── backend/                  # FastAPI + Python 3.13 (uv)
│   ├── CLAUDE.md             # Backend-scoped context
│   ├── pyproject.toml
│   └── app/
│       ├── main.py           # all HTTP routes
│       ├── config.py         # typed settings (pydantic-settings)
│       ├── llm.py            # Bedrock provider abstraction
│       ├── citation_definitions.py  # metric definitions + thresholds
│       ├── agents/
│       │   ├── state.py      # AgentState (TypedDict) + Plan/Report/Citation
│       │   ├── graph.py      # StateGraph wiring; exports research_graph
│       │   ├── streaming.py  # SSE event translator
│       │   ├── cost.py       # per-call $ estimation
│       │   ├── prompts.py    # load_prompt() helper
│       │   └── nodes/        # planner, fetchers, indexer, analyzers, synthesizer
│       ├── tools/            # EDGAR, yfinance, newsapi, technicals
│       ├── rag/              # parser, chunker, embeddings, Chroma store
│       └── prompts/          # .md prompt templates (incl. 8 analyzer prompts)
├── frontend/                 # React 19 + Vite + TypeScript + Tailwind + shadcn
│   ├── CLAUDE.md             # Frontend-scoped context
│   ├── components.json       # shadcn CLI config
│   └── src/
│       ├── App.tsx           # idle hero → TickerInput → ReportView
│       ├── index.css         # Tailwind + shadcn theme tokens
│       ├── components/
│       │   ├── TickerInput.tsx
│       │   ├── ReportView.tsx          # SSE consumer, sections, headline streaming
│       │   ├── CitationSheet.tsx       # source-detail side panel
│       │   ├── PriceChart.tsx          # Recharts area chart with period switch
│       │   ├── ChatPanel.tsx           # follow-up Q&A
│       │   └── ui/                     # shadcn primitives (10 components)
│       ├── lib/                        # api.ts (typed fetch helpers), utils.ts (cn)
│       └── data/sp500.json             # autocomplete list
├── evals/                    # gold-standard test set + runner (Phase 4)
└── infra/                    # deployment configs (Phase 6)
```

## Development workflow

- Backend changes: server auto-reloads (`--reload` is on by default in dev).
- Frontend changes: Vite hot-reloads via HMR.
- Run `pre-commit run --all-files` before pushing if you want to check
  formatting locally (it also runs automatically on `git commit`).

## License

(TBD: will pick MIT or Apache-2.0 in Phase 7.)

---

_Educational project, not affiliated with the SEC, Anthropic, or any
companies analyzed. Filings sourced from public SEC EDGAR data._
