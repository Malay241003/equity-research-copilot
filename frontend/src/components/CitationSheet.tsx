import { useEffect, useState } from "react";
import { ExternalLink, FileText, LineChart, Newspaper } from "lucide-react";

import {
  type CitationDetail,
  fetchCitation,
  type MetricDefinition,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * CitationSheet — slide-out panel showing the full source behind a citation.
 *
 * One <CitationSheet> instance lives at the ReportView level and is opened
 * with a non-null `source_id`. When that prop changes, we re-fetch the
 * detail from `GET /citation/{source_id}`. The Sheet is shadcn's wrapper
 * around Radix Dialog with a side-slide animation.
 *
 * Three rendering paths depending on `source_type`:
 *   - filing   → Item header + filing date + accession + the full chunk text
 *   - yfinance → ticker + metric name + explanation
 *   - news     → the URL + an "open article" button
 */

interface CitationSheetProps {
  sourceId: string | null;
  onClose: () => void;
}

export default function CitationSheet({
  sourceId,
  onClose,
}: CitationSheetProps) {
  const [detail, setDetail] = useState<CitationDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (sourceId === null) {
      // Sheet closed — clear stale data so the next open is fresh.
      setDetail(null);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    setDetail(null);

    fetchCitation(sourceId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    // If the user clicks a different citation before this one resolves,
    // discard the in-flight response.
    return () => {
      cancelled = true;
    };
  }, [sourceId]);

  return (
    <Sheet
      open={sourceId !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="w-full sm:max-w-xl overflow-y-auto px-6 pb-6">
        <SheetHeader className="px-0">
          <SheetTitle className="flex items-center gap-2">
            {detail?.source_type === "filing" && (
              <FileText className="h-4 w-4" />
            )}
            {detail?.source_type === "yfinance" && (
              <LineChart className="h-4 w-4" />
            )}
            {detail?.source_type === "news" && (
              <Newspaper className="h-4 w-4" />
            )}
            Source detail
          </SheetTitle>
          <SheetDescription>
            <span className="font-mono text-xs break-all">{sourceId}</span>
          </SheetDescription>
        </SheetHeader>

        <div className="mt-4">
          {loading && (
            <div className="space-y-3">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-11/12" />
              <Skeleton className="h-4 w-9/12" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-10/12" />
            </div>
          )}

          {error && (
            <div className="text-sm text-destructive">
              <p className="font-medium">Couldn't load citation.</p>
              <p className="text-muted-foreground mt-1">{error}</p>
            </div>
          )}

          {detail !== null && !loading && <CitationBody detail={detail} />}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function CitationBody({ detail }: { detail: CitationDetail }) {
  if (detail.source_type === "filing") {
    return (
      <div className="space-y-4">
        <div className="flex flex-wrap gap-2">
          {detail.ticker && <Badge variant="secondary">{detail.ticker}</Badge>}
          {detail.item_number && (
            <Badge variant="outline">Item {detail.item_number}</Badge>
          )}
          {detail.filing_date && (
            <Badge variant="outline">Filed {detail.filing_date}</Badge>
          )}
        </div>
        {detail.section_title && (
          <p className="text-sm font-medium text-foreground">
            {detail.section_title}
          </p>
        )}
        {detail.text && (
          <div className="rounded-md border bg-muted/30 px-4 py-3">
            <p className="text-sm leading-relaxed whitespace-pre-wrap">
              {detail.text}
            </p>
          </div>
        )}
        {detail.accession_number && (
          <p className="text-xs text-muted-foreground">
            SEC accession:{" "}
            <code className="font-mono">{detail.accession_number}</code>
          </p>
        )}
      </div>
    );
  }

  if (detail.source_type === "yfinance") {
    const isTechnical = detail.metric_name?.startsWith("tech_") ?? false;
    return (
      <div className="space-y-4">
        <div className="flex flex-wrap gap-2 items-center">
          {detail.ticker && <Badge variant="secondary">{detail.ticker}</Badge>}
          {detail.metric_name && (
            <Badge variant="outline" className="font-mono">
              {detail.metric_name}
            </Badge>
          )}
        </div>

        {/* Live-value badge for metrics that move in real-time (VIX, etc.) */}
        {detail.live_value !== undefined && detail.live_value !== null && (
          <div className="rounded-md border bg-foreground/5 px-4 py-3">
            <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">
              Current value
            </p>
            <p className="text-2xl font-semibold tabular-nums">
              {detail.live_value.toFixed(2)}
            </p>
            {detail.live_as_of && (
              <p className="text-xs text-muted-foreground mt-1">
                as of {detail.live_as_of} · cached 15&nbsp;min — re-open this
                panel for a fresher fetch
              </p>
            )}
          </div>
        )}

        {/* Definition block (only if backend has one for this metric) */}
        {detail.definition ? (
          <DefinitionBlock def={detail.definition} />
        ) : (
          <p className="text-sm text-muted-foreground leading-relaxed">
            This citation references a market-data metric. No educational
            metadata is registered for{" "}
            <code className="font-mono">{detail.metric_name}</code> yet.
          </p>
        )}

        <p className="text-xs text-muted-foreground border-t pt-3">
          {isTechnical
            ? "Computed locally from the 1-year price history fetched via yfinance. Re-runs reflect that day's price action."
            : "Retrieved from Yahoo Finance via the yfinance Python library. yfinance scrapes Yahoo's public widgets — values may drift if Yahoo changes the page."}
        </p>
      </div>
    );
  }

  // news shown below.
  return _newsBody(detail);
}

// ─── Educational definition block ──────────────────────────────────

function DefinitionBlock({ def }: { def: MetricDefinition }) {
  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-semibold text-foreground">{def.name}</p>
        {def.unit && (
          <p className="text-xs text-muted-foreground mt-0.5">
            Unit: <span className="font-mono">{def.unit}</span>
          </p>
        )}
      </div>
      <p className="text-sm leading-relaxed text-foreground">
        {def.definition}
      </p>
      {def.thresholds.length > 0 && (
        <div className="rounded-md border bg-muted/30 overflow-hidden">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider px-3 pt-2.5 pb-1.5">
            Reading the number
          </p>
          <div className="divide-y divide-border">
            {def.thresholds.map((t, i) => (
              <div
                key={i}
                className="flex items-center justify-between gap-3 px-3 py-1.5 text-xs"
              >
                <span
                  className={cn(
                    "font-mono whitespace-nowrap text-muted-foreground",
                  )}
                >
                  {formatRange(t.min, t.max, def.unit ?? null)}
                </span>
                <span className="text-foreground text-right">{t.label}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {def.note && (
        <p className="text-xs text-muted-foreground italic leading-relaxed">
          Note: {def.note}
        </p>
      )}
    </div>
  );
}

function formatRange(
  min: number | null,
  max: number | null,
  unit: string | null,
): string {
  const fmt = (n: number) => {
    if (unit === "$") {
      // Compact for big numbers (market cap), full for small.
      if (Math.abs(n) >= 1_000_000_000)
        return `$${(n / 1_000_000_000).toFixed(0)}B`;
      if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(0)}M`;
      return `$${n.toFixed(2)}`;
    }
    if (unit === "%") return `${n}%`;
    if (unit === "days") return `${n}d`;
    return n.toString();
  };

  if (min === null && max !== null) return `< ${fmt(max)}`;
  if (min !== null && max === null) return `≥ ${fmt(min)}`;
  if (min !== null && max !== null) return `${fmt(min)} – ${fmt(max)}`;
  return "any";
}

// ─── News body (extracted so CitationBody stays readable) ─────────

function _newsBody(detail: CitationDetail) {
  return (
    <div className="space-y-4">
      <Badge variant="secondary">News article</Badge>
      <p className="text-sm text-muted-foreground leading-relaxed">
        This citation references an article retrieved from NewsAPI. The full
        text isn't cached server-side; click below to read it at the source.
      </p>
      {detail.url && (
        <Button asChild variant="outline" className="w-full">
          <a href={detail.url} target="_blank" rel="noopener noreferrer">
            <ExternalLink className="h-4 w-4 mr-1.5" />
            Open article
          </a>
        </Button>
      )}
    </div>
  );
}
