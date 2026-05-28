import { useEffect, useMemo, useRef, useState } from "react";
import { Search } from "lucide-react";

import sp500 from "@/data/sp500.json";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/**
 * TickerInput — the entry point for a new research run.
 *
 * Wraps a controlled `<Input>` with a filtered autocomplete dropdown of
 * S&P 500 names. Selecting a row (mouse OR keyboard) calls `onSubmit`
 * with the ticker symbol; the optional `query` textarea below it lets
 * the user focus the research run on a specific angle (e.g.
 * "How exposed to China supply-chain risk?").
 *
 * Keyboard model:
 *   Up / Down  — move the highlighted suggestion
 *   Enter      — submit (uses the highlighted suggestion if dropdown is open)
 *   Escape     — close the dropdown without submitting
 *
 * The dropdown closes on outside-click (we listen on `mousedown` so it
 * fires before any inner button receives its click event).
 */

interface Ticker {
  symbol: string;
  name: string;
}

interface TickerInputProps {
  onSubmit: (ticker: string, query: string | null) => void;
  disabled?: boolean;
}

const TICKERS: Ticker[] = sp500 as Ticker[];
const MAX_SUGGESTIONS = 7;

export default function TickerInput({
  onSubmit,
  disabled = false,
}: TickerInputProps) {
  const [tickerText, setTickerText] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  // Filter the list as the user types. We match on either the symbol or the
  // company name, case-insensitively. Memoised so we don't re-filter on
  // every render when the input is unchanged.
  const suggestions = useMemo(() => {
    const needle = tickerText.trim().toUpperCase();
    if (!needle) return TICKERS.slice(0, MAX_SUGGESTIONS);
    return TICKERS.filter(
      (t) =>
        t.symbol.startsWith(needle) || t.name.toUpperCase().includes(needle),
    ).slice(0, MAX_SUGGESTIONS);
  }, [tickerText]);

  // Reset the highlight whenever the suggestion list changes shape — a
  // stale index would point past the end of the new list.
  useEffect(() => {
    setHighlight(0);
  }, [suggestions]);

  // Outside-click closes the dropdown. `mousedown` (not `click`) so the
  // event fires BEFORE a click on a suggestion would have completed, which
  // matters when the wrapper itself is removed/re-rendered.
  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (!wrapperRef.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    window.addEventListener("mousedown", onMouseDown);
    return () => window.removeEventListener("mousedown", onMouseDown);
  }, []);

  function submit(symbol: string) {
    if (disabled) return;
    const cleanSymbol = symbol.trim().toUpperCase();
    if (!cleanSymbol) return;
    setOpen(false);
    onSubmit(cleanSymbol, query.trim() || null);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      // If the dropdown is open AND a suggestion is highlighted, use it.
      // Otherwise fall back to the literal text the user typed — supports
      // tickers outside our curated list (BRK.B, etc. they may type free-form).
      if (open && suggestions[highlight]) {
        submit(suggestions[highlight].symbol);
      } else {
        submit(tickerText);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  const canSubmit = !disabled && tickerText.trim().length > 0;

  return (
    <div className="flex flex-col gap-3 w-full max-w-2xl">
      <div ref={wrapperRef} className="relative">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              type="text"
              placeholder="Enter a ticker (e.g. AAPL, MSFT, NVDA)…"
              value={tickerText}
              onChange={(e) => {
                setTickerText(e.target.value.toUpperCase());
                setOpen(true);
              }}
              onFocus={() => setOpen(true)}
              onKeyDown={onKeyDown}
              disabled={disabled}
              className="pl-9 h-11 text-base"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <Button
            type="button"
            size="lg"
            onClick={() => submit(tickerText)}
            disabled={!canSubmit}
            className="h-11 px-6"
          >
            Research
          </Button>
        </div>

        {open && suggestions.length > 0 && (
          <ul
            role="listbox"
            className={cn(
              "absolute z-20 mt-1 w-full overflow-hidden rounded-md border bg-popover shadow-md",
              "max-h-72 overflow-y-auto",
            )}
          >
            {suggestions.map((t, i) => (
              <li
                key={t.symbol}
                role="option"
                aria-selected={i === highlight}
                onMouseEnter={() => setHighlight(i)}
                onMouseDown={(e) => {
                  // mousedown so we submit before the wrapper's outside-click
                  // handler can close us first.
                  e.preventDefault();
                  submit(t.symbol);
                }}
                className={cn(
                  "flex items-center justify-between px-3 py-2 cursor-pointer text-sm",
                  i === highlight
                    ? "bg-accent text-accent-foreground"
                    : "hover:bg-accent/50",
                )}
              >
                <span className="font-mono font-medium">{t.symbol}</span>
                <span className="text-muted-foreground truncate ml-3 max-w-[60%] text-right">
                  {t.name}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <textarea
        placeholder="Optional: narrow the research focus (e.g. 'How exposed to China supply-chain risk?')"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        disabled={disabled}
        rows={2}
        className={cn(
          "w-full rounded-md border bg-background px-3 py-2 text-sm",
          "placeholder:text-muted-foreground focus-visible:outline-none",
          "focus-visible:ring-2 focus-visible:ring-ring",
          "disabled:opacity-50",
          "resize-none",
        )}
      />
    </div>
  );
}
