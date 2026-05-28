import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { TrendingDown, TrendingUp } from "lucide-react";

import {
  fetchPriceHistory,
  type PriceHistory,
  type PricePeriod,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * PriceChart — area chart of daily close prices over a selectable period.
 *
 * Manages its own data fetching (independent of the SSE research run) so
 * users can flip 1M / 3M / 1Y / 5Y buttons without re-running the agent.
 * The backend caches yfinance results for 1 hour, so period switches are
 * effectively free after the first request.
 */

interface PriceChartProps {
  ticker: string;
}

const PERIODS: { value: PricePeriod; label: string }[] = [
  { value: "1mo", label: "1M" },
  { value: "3mo", label: "3M" },
  { value: "1y", label: "1Y" },
  { value: "5y", label: "5Y" },
];

export default function PriceChart({ ticker }: PriceChartProps) {
  const [period, setPeriod] = useState<PricePeriod>("1y");
  const [data, setData] = useState<PriceHistory | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchPriceHistory(ticker, period)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [ticker, period]);

  // First / last / pct change — derived for the header badge.
  const summary = useMemo(() => {
    if (!data || data.points.length < 2) return null;
    const first = data.points[0];
    const last = data.points[data.points.length - 1];
    const pct = ((last.close - first.close) / first.close) * 100;
    return { first, last, pct };
  }, [data]);

  const isUp = summary !== null && summary.pct >= 0;

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <CardTitle className="text-base flex items-center gap-3">
            Price
            {summary && (
              <span
                className={cn(
                  "inline-flex items-center gap-1 text-sm font-normal",
                  isUp
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-red-600 dark:text-red-400",
                )}
              >
                {isUp ? (
                  <TrendingUp className="h-3.5 w-3.5" />
                ) : (
                  <TrendingDown className="h-3.5 w-3.5" />
                )}
                {summary.pct >= 0 ? "+" : ""}
                {summary.pct.toFixed(1)}%
                <span className="text-muted-foreground ml-1">
                  · ${summary.last.close.toFixed(2)}
                </span>
              </span>
            )}
          </CardTitle>

          <div className="flex gap-1">
            {PERIODS.map((p) => (
              <Button
                key={p.value}
                type="button"
                variant={period === p.value ? "default" : "ghost"}
                size="sm"
                className="h-7 px-2.5 text-xs"
                onClick={() => setPeriod(p.value)}
              >
                {p.label}
              </Button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {error && (
          <p className="text-sm text-destructive py-8 text-center">{error}</p>
        )}
        {loading && !data && <Skeleton className="w-full h-[220px]" />}
        {data !== null && (
          <div
            className={cn(
              "h-[220px] w-full",
              loading && "opacity-50 transition-opacity",
            )}
          >
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart
                data={data.points}
                margin={{ top: 4, right: 8, left: 0, bottom: 4 }}
              >
                <defs>
                  <linearGradient
                    id="priceGradient"
                    x1="0"
                    y1="0"
                    x2="0"
                    y2="1"
                  >
                    <stop
                      offset="0%"
                      stopColor={isUp ? "var(--chart-2)" : "var(--chart-5)"}
                      stopOpacity={0.4}
                    />
                    <stop
                      offset="100%"
                      stopColor={isUp ? "var(--chart-2)" : "var(--chart-5)"}
                      stopOpacity={0}
                    />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--border)"
                  vertical={false}
                />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                  tickFormatter={formatDateTick(period)}
                  minTickGap={40}
                />
                <YAxis
                  tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                  tickFormatter={(v: number) => `$${v.toFixed(0)}`}
                  domain={["auto", "auto"]}
                  width={48}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--popover)",
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                  labelStyle={{ color: "var(--popover-foreground)" }}
                  formatter={(v) => [
                    `$${typeof v === "number" ? v.toFixed(2) : v}`,
                    "Close",
                  ]}
                />
                <Area
                  type="monotone"
                  dataKey="close"
                  stroke={isUp ? "var(--chart-2)" : "var(--chart-5)"}
                  strokeWidth={1.75}
                  fill="url(#priceGradient)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// Returns a tick-formatter appropriate for the period. Shorter labels for
// shorter windows (just month + day), longer for multi-year (month + year).
function formatDateTick(period: PricePeriod): (iso: string) => string {
  return (iso: string) => {
    const d = new Date(iso);
    if (period === "5y") {
      return d.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
    }
    if (period === "1y") {
      return d.toLocaleDateString("en-US", { month: "short" });
    }
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  };
}
