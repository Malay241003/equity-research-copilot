import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Loader2, MessageSquare, Send, User } from "lucide-react";

import { type ChatMessage, postChat, type Report } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * ChatPanel — follow-up Q&A grounded in the generated report.
 *
 * Visible only once the report has finished (parent passes `report` only
 * then). The conversation history lives in this component's local state —
 * if the user starts a new research run, the parent unmounts ReportView
 * (via key change in App.tsx) and this state resets implicitly.
 *
 * Backend is stateless: we send the full report + the entire message list
 * on each turn. Keeps the server simple; the user owns the conversation.
 */

interface ChatPanelProps {
  ticker: string;
  report: Report;
}

const SUGGESTED_QUESTIONS = [
  "What's the biggest risk highlighted?",
  "How does the valuation compare to peers?",
  "Summarise the bull and bear cases in one sentence each.",
];

export default function ChatPanel({ ticker, report }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollAnchorRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to the newest message whenever the list grows. The
  // `scrollAnchorRef` div sits at the bottom of the messages container;
  // scrollIntoView nudges the container to that anchor.
  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({
      behavior: "smooth",
      block: "end",
    });
  }, [messages, loading]);

  async function sendMessage(text: string) {
    const cleaned = text.trim();
    if (!cleaned || loading) return;

    const nextMessages: ChatMessage[] = [
      ...messages,
      { role: "user", content: cleaned },
    ];
    setMessages(nextMessages);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const res = await postChat(ticker, nextMessages, report);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: res.content },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chat failed");
      // Roll the user message back so retry resends it via the input box.
      setMessages((prev) => prev.slice(0, -1));
      setInput(cleaned);
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter inserts a newline. Mirrors most chat UIs.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <MessageSquare className="h-4 w-4" />
          Follow-up questions
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* ─── Suggested questions (only before any turns) ──────── */}
        {messages.length === 0 && !loading && (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">
              Ask anything about this report — the assistant answers grounded in
              the analysis above.
            </p>
            <div className="flex flex-wrap gap-2">
              {SUGGESTED_QUESTIONS.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => sendMessage(q)}
                  className={cn(
                    "rounded-full border border-input bg-background px-3 py-1 text-xs",
                    "hover:bg-accent hover:text-accent-foreground transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  )}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* ─── Message thread ───────────────────────────────────── */}
        {messages.length > 0 && (
          <div className="space-y-3 max-h-[420px] overflow-y-auto pr-1">
            {messages.map((msg, i) => (
              <MessageBubble key={i} message={msg} />
            ))}
            {loading && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground pl-1">
                <Loader2 className="h-4 w-4 animate-spin" />
                Thinking…
              </div>
            )}
            <div ref={scrollAnchorRef} />
          </div>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}

        {/* ─── Input box ────────────────────────────────────────── */}
        <div className="flex gap-2 items-end">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={loading}
            rows={2}
            placeholder="Ask a follow-up question…"
            className={cn(
              "flex-1 rounded-md border bg-background px-3 py-2 text-sm",
              "placeholder:text-muted-foreground focus-visible:outline-none",
              "focus-visible:ring-2 focus-visible:ring-ring",
              "disabled:opacity-50 resize-none",
            )}
          />
          <Button
            type="button"
            size="icon"
            onClick={() => sendMessage(input)}
            disabled={loading || input.trim().length === 0}
            className="h-10 w-10"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";

  return (
    <div className={cn("flex gap-2", isUser ? "flex-row-reverse" : "flex-row")}>
      <div
        className={cn(
          "shrink-0 h-7 w-7 rounded-full flex items-center justify-center",
          isUser ? "bg-foreground text-background" : "bg-muted text-foreground",
        )}
      >
        {isUser ? (
          <User className="h-3.5 w-3.5" />
        ) : (
          <MessageSquare className="h-3.5 w-3.5" />
        )}
      </div>
      <div
        className={cn(
          "rounded-lg px-3 py-2 text-sm max-w-[85%]",
          isUser ? "bg-foreground text-background" : "bg-muted text-foreground",
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap leading-relaxed">
            {message.content}
          </p>
        ) : (
          <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1.5 [&_ul]:my-1.5 [&_ol]:my-1.5">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}
