import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle,
  CheckCircle2,
  DollarSign,
  Loader2,
  RotateCcw,
} from "lucide-react";

import {
  ALL_SECTIONS,
  API_BASE,
  type FetcherSource,
  type PipelinePhase,
  type Plan,
  type Report,
  type SectionName,
  type SectionOutput,
  SECTION_LABELS,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import ChatPanel from "@/components/ChatPanel";
import CitationSheet from "@/components/CitationSheet";
import PriceChart from "@/components/PriceChart";

/**
 * ReportView — opens an SSE connection to `/research/{ticker}/stream` and
 * renders the report progressively as events arrive.
 *
 * State model is event-driven: one piece of state per SSE event class. The
 * effect tears down the EventSource on unmount, on a `complete`/`error`
 * event, OR when the (ticker, query) inputs change (re-run from the
 * parent triggers a fresh connection).
 */

interface ReportViewProps {
  ticker: string;
  query: string | null;
  onReset: () => void;
}

interface PhaseEvent {
  phase: PipelinePhase;
  label: string;
}

interface FetchedEvent {
  source: FetcherSource;
  summary: string;
}

const PHASE_ORDER: PipelinePhase[] = [
  "planning",
  "fetching",
  "indexing",
  "analyzing",
  "synthesizing",
  "complete",
];

const PHASE_DISPLAY: Record<PipelinePhase, string> = {
  planning: "Planning",
  fetching: "Fetching",
  indexing: "Indexing",
  analyzing: "Analyzing",
  synthesizing: "Synthesizing",
  complete: "Done",
};

export default function ReportView({
  ticker,
  query,
  onReset,
}: ReportViewProps) {
  // ─── Streaming state ───────────────────────────────────────────
  const [phase, setPhase] = useState<PipelinePhase>("planning");
  const [phaseLabel, setPhaseLabel] = useState("Connecting…");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [fetched, setFetched] = useState<
    Partial<Record<FetcherSource, string>>
  >({});
  const [sections, setSections] = useState<
    Partial<Record<SectionName, SectionOutput>>
  >({});
  const [synthBuffer, setSynthBuffer] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Active citation source_id for the slide-out Sheet. null = closed.
  const [activeCitation, setActiveCitation] = useState<string | null>(null);

  // Hold the EventSource in a ref so the effect can close it on cleanup
  // without re-opening on every render.
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    // Reset state for a new run.
    setPhase("planning");
    setPhaseLabel("Connecting…");
    setPlan(null);
    setFetched({});
    setSections({});
    setSynthBuffer("");
    setReport(null);
    setError(null);

    const params = new URLSearchParams();
    if (query) params.set("query", query);
    const url = `${API_BASE}/research/${encodeURIComponent(ticker)}/stream${
      params.toString() ? `?${params}` : ""
    }`;

    const es = new EventSource(url);
    esRef.current = es;

    // Each SSE event type gets its own listener. EventSource routes by the
    // `event:` line in the wire format. Anything not matched falls through
    // to the default `message` handler (which we ignore — the server only
    // emits named events).

    es.addEventListener("phase", (e) => {
      const { phase: p, label } = JSON.parse(
        (e as MessageEvent).data,
      ) as PhaseEvent;
      setPhase(p);
      setPhaseLabel(label);
    });

    es.addEventListener("plan", (e) => {
      setPlan(JSON.parse((e as MessageEvent).data) as Plan);
    });

    es.addEventListener("fetched", (e) => {
      const { source, summary } = JSON.parse(
        (e as MessageEvent).data,
      ) as FetchedEvent;
      setFetched((prev) => ({ ...prev, [source]: summary }));
    });

    es.addEventListener("section", (e) => {
      const section = JSON.parse((e as MessageEvent).data) as SectionOutput;
      setSections((prev) => ({ ...prev, [section.section]: section }));
    });

    es.addEventListener("synth_token", (e) => {
      const { text } = JSON.parse((e as MessageEvent).data) as { text: string };
      setSynthBuffer((prev) => prev + text);
    });

    es.addEventListener("complete", (e) => {
      const { report: r } = JSON.parse((e as MessageEvent).data) as {
        report: Report;
      };
      setReport(r);
      // The server emits `phase: complete` AFTER this event, but `es.close()`
      // below will sever the connection before that phase event arrives —
      // so we set the terminal phase ourselves rather than relying on a
      // trailing message we'll never receive.
      setPhase("complete");
      setPhaseLabel("Report complete.");
      es.close();
    });

    es.addEventListener("error", (e) => {
      // The server emits a custom `error` event before disconnecting; OR
      // we get a transport-level error with no payload. Distinguish by
      // checking for a data field.
      const data = (e as MessageEvent).data;
      if (typeof data === "string") {
        try {
          const { detail } = JSON.parse(data) as { detail: string };
          setError(detail);
        } catch {
          setError("Stream error");
        }
      } else if (es.readyState === EventSource.CLOSED) {
        // Already closed — likely just the normal end-of-stream after
        // our `complete` handler ran `close()`. Don't surface as an error.
      } else {
        setError("Lost connection to the research stream.");
      }
      es.close();
    });

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [ticker, query]);

  // Parse the streaming synth buffer into headline + bottom_line as they
  // arrive. The synthesizer emits in the form `HEADLINE: ...\n\nBOTTOM LINE: ...`
  // so a split on the BOTTOM LINE marker gives us either (full headline,
  // partial bottom_line) mid-stream, or (full headline, full bottom_line)
  // at the end.
  const liveHeadline = useMemo(
    () => extractPart(synthBuffer, "HEADLINE", "BOTTOM"),
    [synthBuffer],
  );
  const liveBottomLine = useMemo(
    () => extractPart(synthBuffer, "BOTTOM LINE", null),
    [synthBuffer],
  );

  const displayHeadline = report?.headline ?? liveHeadline;
  const displayBottomLine = report?.bottom_line ?? liveBottomLine;
  const companyName = report?.company_name ?? plan?.company_name ?? null;

  return (
    <div className="flex flex-col gap-6 w-full max-w-4xl mx-auto pb-12">
      {/* ─── Header ─────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4 pt-2">
        <div>
          <div className="flex items-baseline gap-3">
            <h1 className="text-3xl font-semibold tracking-tight">{ticker}</h1>
            {companyName && (
              <span className="text-lg text-muted-foreground">
                {companyName}
              </span>
            )}
          </div>
          {plan?.research_focus && (
            <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
              Focus: {plan.research_focus}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {report && (
            <Badge
              variant="secondary"
              className="font-mono text-xs"
              title="Estimated LLM cost for this run (Bedrock on-demand rates). User runs on Free Tier credits."
            >
              <DollarSign className="h-3 w-3 mr-0.5" />
              {report.cost_usd.toFixed(4)}
            </Badge>
          )}
          <Button variant="outline" size="sm" onClick={onReset}>
            <RotateCcw className="h-4 w-4 mr-1.5" />
            New search
          </Button>
        </div>
      </div>

      {/* ─── Progress strip ─────────────────────────────────────── */}
      <ProgressStrip
        phase={phase}
        phaseLabel={phaseLabel}
        fetched={fetched}
        sectionsDoneCount={Object.keys(sections).length}
        hasError={error !== null}
      />

      {error && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="pt-6 flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
            <div>
              <p className="font-medium text-destructive">Stream failed</p>
              <p className="text-sm text-muted-foreground mt-1">{error}</p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* ─── Price chart (fetched independently — appears as soon as
              ticker is known, doesn't wait for the report) ──────── */}
      <PriceChart ticker={ticker} />

      {/* ─── Headline + bottom line (live as tokens arrive) ─────── */}
      {(displayHeadline || displayBottomLine) && (
        <Card>
          <CardContent className="pt-6 space-y-3">
            {displayHeadline && (
              <h2 className="text-xl font-medium leading-snug">
                {displayHeadline}
              </h2>
            )}
            {displayBottomLine && (
              <p className="text-base text-muted-foreground leading-relaxed">
                {displayBottomLine}
                {phase === "synthesizing" && (
                  <span
                    className="inline-block w-1.5 h-4 ml-0.5 bg-foreground/60 align-text-bottom animate-pulse"
                    aria-hidden
                  />
                )}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* ─── Section cards ──────────────────────────────────────── */}
      <div className="grid gap-4">
        {ALL_SECTIONS.map((name) => (
          <SectionCard
            key={name}
            name={name}
            section={sections[name]}
            onCitationClick={setActiveCitation}
          />
        ))}
      </div>

      {/* ─── Follow-up chat (only once report is done) ──────────── */}
      {report !== null && <ChatPanel ticker={ticker} report={report} />}

      {/* ─── Citation side panel ─────────────────────────────────── */}
      <CitationSheet
        sourceId={activeCitation}
        onClose={() => setActiveCitation(null)}
      />

      {/* ─── Disclaimer ─────────────────────────────────────────── */}
      <div className="text-xs text-muted-foreground text-center pt-4">
        Educational and research tool — <strong>not financial advice</strong>.
        Data from SEC EDGAR, yfinance, and NewsAPI. Synthesised by Amazon Nova
        Pro via AWS Bedrock.
      </div>
    </div>
  );
}

// ─── Sub-components ────────────────────────────────────────────────

interface ProgressStripProps {
  phase: PipelinePhase;
  phaseLabel: string;
  fetched: Partial<Record<FetcherSource, string>>;
  sectionsDoneCount: number;
  hasError: boolean;
}

function ProgressStrip({
  phase,
  phaseLabel,
  fetched,
  sectionsDoneCount,
  hasError,
}: ProgressStripProps) {
  const currentIndex = PHASE_ORDER.indexOf(phase);

  return (
    <Card>
      <CardContent className="pt-6">
        {/* Phase dots */}
        <div className="flex items-center gap-2 mb-4">
          {PHASE_ORDER.filter((p) => p !== "complete").map((p, i) => {
            const isDone = i < currentIndex || phase === "complete";
            const isCurrent = i === currentIndex && phase !== "complete";
            return (
              <div key={p} className="flex items-center gap-2 flex-1">
                <div
                  className={cn(
                    "flex items-center justify-center h-7 w-7 rounded-full text-xs font-medium",
                    isDone && "bg-foreground text-background",
                    isCurrent &&
                      "bg-foreground/10 text-foreground ring-2 ring-foreground/40",
                    !isDone && !isCurrent && "bg-muted text-muted-foreground",
                  )}
                >
                  {isDone ? (
                    <CheckCircle2 className="h-4 w-4" />
                  ) : isCurrent ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    i + 1
                  )}
                </div>
                <span
                  className={cn(
                    "text-xs hidden sm:inline",
                    isCurrent
                      ? "text-foreground font-medium"
                      : "text-muted-foreground",
                  )}
                >
                  {PHASE_DISPLAY[p]}
                </span>
                {i < PHASE_ORDER.length - 2 && (
                  <Separator className="flex-1 ml-1" />
                )}
              </div>
            );
          })}
        </div>

        {/* Active phase label + per-fetcher / per-section detail */}
        <div className="text-sm text-muted-foreground">
          {hasError ? (
            <span className="text-destructive">Stream failed.</span>
          ) : phase === "complete" ? (
            <span className="text-foreground">Report complete.</span>
          ) : (
            <span>{phaseLabel}</span>
          )}
        </div>

        {/* Fetcher checklist (visible once fetchers start landing) */}
        {Object.keys(fetched).length > 0 && (
          <div className="flex flex-wrap gap-2 mt-3">
            {(["yfinance", "filings", "news"] as FetcherSource[]).map((src) => (
              <Badge
                key={src}
                variant={fetched[src] ? "secondary" : "outline"}
                className={cn("font-normal", !fetched[src] && "opacity-50")}
              >
                {fetched[src] ? (
                  <CheckCircle2 className="h-3 w-3 mr-1" />
                ) : (
                  <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                )}
                {fetched[src] ?? `${src}…`}
              </Badge>
            ))}
          </div>
        )}

        {/* Analyzer progress (shows once we hit the analyzing phase) */}
        {(phase === "analyzing" ||
          phase === "synthesizing" ||
          phase === "complete") && (
          <div className="text-xs text-muted-foreground mt-3">
            Sections analysed:{" "}
            <strong className="text-foreground">{sectionsDoneCount}</strong> of{" "}
            {ALL_SECTIONS.length}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface SectionCardProps {
  name: SectionName;
  section: SectionOutput | undefined;
  onCitationClick: (sourceId: string) => void;
}

function SectionCard({ name, section, onCitationClick }: SectionCardProps) {
  if (!section) {
    return (
      <Card className="opacity-60">
        <CardHeader>
          <CardTitle className="text-base text-muted-foreground">
            {SECTION_LABELS[name]}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-11/12" />
          <Skeleton className="h-4 w-9/12" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <CheckCircle2 className="h-4 w-4 text-foreground/60" />
          {SECTION_LABELS[name]}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="prose prose-sm dark:prose-invert max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {section.summary}
          </ReactMarkdown>
        </div>

        {section.key_points.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              Key points
            </p>
            <ul className="space-y-1.5 text-sm list-disc pl-5 marker:text-muted-foreground">
              {section.key_points.map((point, i) => (
                <li key={i}>{point}</li>
              ))}
            </ul>
          </div>
        )}

        {section.citations.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              Sources ({section.citations.length})
            </p>
            <div className="flex flex-wrap gap-1.5">
              {section.citations.map((c, i) => (
                <CitationBadge
                  key={`${c.source_id}-${i}`}
                  sourceId={c.source_id}
                  quote={c.quote}
                  onClick={onCitationClick}
                />
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Citation badge ────────────────────────────────────────────────
// Two variants depending on what the source_id represents:
//   - URL    → render as <a target="_blank"> so the news article opens
//              directly in a new tab on click. One click, no side panel.
//   - other  → render as a button that opens the side-panel CitationSheet
//              (filings, yfinance metrics, technicals, earnings, VIX).

interface CitationBadgeProps {
  sourceId: string;
  quote: string | null;
  onClick: (sourceId: string) => void;
}

function CitationBadge({ sourceId, quote, onClick }: CitationBadgeProps) {
  const isUrl =
    sourceId.startsWith("http://") || sourceId.startsWith("https://");
  const baseClasses = cn(
    "inline-flex items-center gap-1 rounded-md border border-input",
    "bg-background hover:bg-accent hover:text-accent-foreground",
    "px-2 py-0.5 text-xs font-mono",
    "transition-colors focus-visible:outline-none",
    "focus-visible:ring-2 focus-visible:ring-ring",
    "max-w-full",
  );

  if (isUrl) {
    // Show the domain rather than the full URL — keeps badges compact and
    // gives users a recognisable source label (e.g. "macdailynews.com")
    // instead of a 100-char URL that overflows the card.
    const label = shortDomain(sourceId);
    return (
      <a
        href={sourceId}
        target="_blank"
        rel="noopener noreferrer"
        title={quote ?? `Open ${label} in new tab`}
        className={baseClasses}
      >
        {label}
        <ExternalLinkIcon />
      </a>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onClick(sourceId)}
      title={quote ?? "Click for source detail"}
      className={cn(baseClasses, "truncate")}
    >
      {sourceId}
    </button>
  );
}

function shortDomain(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

// Small inline icon — avoids pulling another lucide import in the badge
// hot path. Pixel-tuned to align with monospace text baseline.
function ExternalLinkIcon() {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M15 3h6v6" />
      <path d="M10 14L21 3" />
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    </svg>
  );
}

// ─── Helpers ───────────────────────────────────────────────────────

/**
 * Extract a labelled section from the streaming synthesizer buffer.
 *
 * The synthesizer outputs `HEADLINE: <line>\n\nBOTTOM LINE: <paragraph>`.
 * As tokens arrive, the buffer might look like:
 *   "HEAD"
 *   "HEADLINE: Apple Inc."
 *   "HEADLINE: Apple Inc. shows strong financial...\n\nBOTTOM LINE: The"
 *
 * Returns the text between `startLabel:` and `endLabel:` (or end of buffer
 * if endLabel is null). Returns empty string if startLabel hasn't arrived
 * yet. Handles partial label tokens by requiring the colon.
 */
function extractPart(
  buffer: string,
  startLabel: string,
  endLabel: string | null,
): string {
  // Case-insensitive match for the start marker. Look for `LABEL:` (with
  // optional whitespace before the colon — Nova sometimes emits "HEADLINE :").
  const startRegex = new RegExp(`${startLabel}\\s*:`, "i");
  const startMatch = buffer.match(startRegex);
  if (!startMatch || startMatch.index === undefined) return "";

  const afterStart = buffer.slice(startMatch.index + startMatch[0].length);

  if (endLabel === null) {
    return cleanFences(afterStart.trim());
  }

  const endRegex = new RegExp(`${endLabel}\\s*(LINE)?\\s*:`, "i");
  const endMatch = afterStart.match(endRegex);
  if (!endMatch || endMatch.index === undefined) {
    return cleanFences(afterStart.trim());
  }

  return cleanFences(afterStart.slice(0, endMatch.index).trim());
}

function cleanFences(text: string): string {
  // Strip rogue code fences the LLM may emit despite prompt instructions.
  let t = text;
  if (t.startsWith("```")) {
    const newline = t.indexOf("\n");
    t = newline === -1 ? "" : t.slice(newline + 1);
  }
  if (t.endsWith("```")) {
    t = t.slice(0, -3).trimEnd();
  }
  return t;
}
