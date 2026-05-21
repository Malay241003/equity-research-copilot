import { useEffect, useState } from "react";
import IngestSearchPanel from "./components/IngestSearchPanel";
import "./App.css";

interface BackendInfo {
  llm_provider: string;
  aws_region: string;
  has_aws_creds: boolean;
  has_langsmith_key: boolean;
  langsmith_tracing: boolean;
  langsmith_project: string;
  cors_origins: string[];
}

interface GenerateResponse {
  content: string;
  model_used: string;
  input_tokens: number | null;
  output_tokens: number | null;
}

type Tier = "fast" | "synthesis";

const API_BASE = "http://localhost:8000";

function App() {
  // ─── Connection check ────────────────────────────────────────────
  const [info, setInfo] = useState<BackendInfo | null>(null);
  const [infoError, setInfoError] = useState<string | null>(null);

  // ─── LLM smoke test state ────────────────────────────────────────
  const [prompt, setPrompt] = useState<string>(
    "In one sentence, what is an equity research report?",
  );
  const [tier, setTier] = useState<Tier>("fast");
  const [loading, setLoading] = useState<boolean>(false);
  const [result, setResult] = useState<GenerateResponse | null>(null);
  const [genError, setGenError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/info`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: BackendInfo) => setInfo(data))
      .catch((err: Error) => setInfoError(err.message));
  }, []);

  async function handleGenerate() {
    setLoading(true);
    setResult(null);
    setGenError(null);
    try {
      const res = await fetch(`${API_BASE}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, model: tier }),
      });
      if (!res.ok) {
        const errBody = await res
          .json()
          .catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(errBody.detail ?? `HTTP ${res.status}`);
      }
      const data: GenerateResponse = await res.json();
      setResult(data);
    } catch (err) {
      setGenError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main
      style={{
        padding: "2rem",
        fontFamily: "system-ui, sans-serif",
        maxWidth: 900,
        margin: "0 auto",
        lineHeight: 1.5,
      }}
    >
      <h1>Equity Research Copilot</h1>
      <p style={{ color: "#666" }}>Phase 1 — data layer + RAG smoke test</p>

      <section style={{ marginTop: "2rem" }}>
        <h2>Backend status</h2>
        {infoError && (
          <p style={{ color: "crimson" }}>
            Failed to reach backend: <code>{infoError}</code>
          </p>
        )}
        {!info && !infoError && <p>Loading…</p>}
        {info && (
          <ul>
            <li>
              LLM provider: <strong>{info.llm_provider}</strong>
            </li>
            <li>
              AWS region: <strong>{info.aws_region}</strong>
            </li>
            <li>
              AWS credentials configured:{" "}
              <strong>{info.has_aws_creds ? "yes" : "no"}</strong>
            </li>
            <li>
              LangSmith API key configured:{" "}
              <strong>{info.has_langsmith_key ? "yes" : "no"}</strong>
            </li>
            <li>
              LangSmith tracing enabled:{" "}
              <strong>{info.langsmith_tracing ? "yes" : "no"}</strong>
            </li>
            <li>
              LangSmith project: <code>{info.langsmith_project}</code>
            </li>
          </ul>
        )}
      </section>

      <section style={{ marginTop: "2rem" }}>
        <h2>LLM smoke test</h2>
        <p style={{ color: "#666" }}>
          Sends a prompt to Bedrock through <code>POST /generate</code>. Use the
          fast tier (Haiku) for testing — pennies per call.
        </p>

        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={4}
          style={{
            width: "100%",
            padding: "0.5rem",
            fontFamily: "inherit",
            fontSize: "1rem",
            boxSizing: "border-box",
          }}
        />

        <div
          style={{
            marginTop: "0.5rem",
            display: "flex",
            gap: "1rem",
            alignItems: "center",
          }}
        >
          <label>
            Tier:{" "}
            <select
              value={tier}
              onChange={(e) => setTier(e.target.value as Tier)}
              disabled={loading}
            >
              <option value="fast">fast (Haiku — cheap)</option>
              <option value="synthesis">synthesis (Sonnet — quality)</option>
            </select>
          </label>

          <button
            type="button"
            onClick={handleGenerate}
            disabled={loading || prompt.trim().length === 0}
            style={{
              padding: "0.5rem 1.25rem",
              fontSize: "1rem",
              cursor: loading ? "wait" : "pointer",
            }}
          >
            {loading ? "Calling…" : "Test LLM"}
          </button>
        </div>

        {genError && (
          <p style={{ color: "crimson", marginTop: "1rem" }}>
            Generate failed: <code>{genError}</code>
          </p>
        )}

        {result && (
          <div
            style={{
              marginTop: "1rem",
              padding: "1rem",
              border: "1px solid #ddd",
              borderRadius: 6,
              background: "#fafafa",
            }}
          >
            <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>
              {result.content}
            </p>
            <p
              style={{
                color: "#888",
                fontSize: "0.875rem",
                marginTop: "1rem",
                marginBottom: 0,
              }}
            >
              Model: <code>{result.model_used}</code>
              {result.input_tokens != null && (
                <>
                  {" · "}in: {result.input_tokens} tok · out:{" "}
                  {result.output_tokens} tok
                </>
              )}
            </p>
          </div>
        )}
      </section>

      <IngestSearchPanel />
    </main>
  );
}

export default App;
