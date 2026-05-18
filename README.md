# Equity Research Copilot

> An agentic AI system that performs equity research on publicly traded
> companies. Enter a ticker, get a structured research report (financial
> health, growth, risks, competition, valuation) with cited sources, then chat
> with the system for grounded follow-ups.

> **Educational and research tool, NOT financial advice.** This is a
> portfolio/learning project, not a financial product.

---

## Status

In active development. Phase 1 complete (2026-05-21); currently in
Phase 2 of 7 (Core Agent Loop with LangGraph).

Working today:

- FastAPI backend, React frontend, AWS Bedrock (Claude Sonnet/Haiku) LLM
  calls, LangSmith tracing
- End-to-end RAG pipeline: SEC EDGAR client, 10-K parser, section-aware
  chunker, Voyage `voyage-finance-2` embeddings, Chroma vector store
- HTTP endpoints: `POST /ingest/{ticker}` and `GET /search/{ticker}?q=...`
  with idempotency (re-ingesting the same filing is a no-op)
- yfinance wrapper for prices, fundamentals, and news (with TTL caches)
- Frontend Ingest + Search demo panel at `http://localhost:5173`
- Voyage free-tier rate-limit handling: token-aware batching, inter-batch
  sleeps, retry on rate-limit and network errors, per-batch commit to Chroma so
  partial failures do not lose work

## Tech stack

| Layer           | Choice                                                              |
| --------------- | ------------------------------------------------------------------- |
| Backend         | FastAPI on Python 3.13, deps via [`uv`](https://docs.astral.sh/uv/) |
| Agent framework | [LangGraph](https://langchain-ai.github.io/langgraph/)              |
| Primary LLM     | Anthropic Claude via AWS Bedrock                                    |
| Vector DB       | [Chroma](https://docs.trychroma.com/) (added Phase 1)               |
| Embeddings      | Voyage `voyage-finance-2` / OpenAI fallback (Phase 1)               |
| Frontend        | React + Vite + TypeScript                                           |
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

UI runs on **http://localhost:5173**. Open it and you should see three
panels: backend status, LLM smoke test, and the Ingest + Search demo
(enter a ticker, click Ingest, then run a vector search).

> First-time ingest of a new ticker takes about 8 minutes on Voyage's free tier
> due to its 3 RPM / 10K TPM rate limit. Idempotent re-runs are instant.

## Project layout

```
equity-research-copilot/
├── README.md
├── backend/                 # FastAPI + Python 3.13 (uv)
│   ├── app/
│   │   ├── main.py          # routes (health, /info, /generate, /ingest, /search)
│   │   ├── config.py        # typed settings (pydantic-settings)
│   │   ├── llm.py           # LLM provider abstraction (Bedrock)
│   │   ├── tools/           # EDGAR, yfinance, news clients (Phase 1)
│   │   └── rag/             # parser, chunker, embeddings, Chroma store (Phase 1)
│   └── pyproject.toml
├── frontend/                # React + Vite + TypeScript
│   ├── src/
│   │   ├── App.tsx
│   │   └── components/      # IngestSearchPanel.tsx (Phase 1)
│   └── package.json
├── evals/                   # gold-standard test set + runner (Phase 4)
└── infra/                   # deployment configs (Phase 6)
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
