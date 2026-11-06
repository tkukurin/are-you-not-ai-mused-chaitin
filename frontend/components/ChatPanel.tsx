"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef } from "react";
import { colorForActor, formatTime } from "@/lib/api";
import type { Actor, Message, TraceSummary } from "@/lib/types";
import { Avatar } from "./ViewingSwitcher";

const SUGGESTIONS = [
  "Make a checklist for the street party",
  "Compare pavilion rental options",
  "Add a map of meeting points",
  "Chart expected attendance by hour",
  "Add a note with the rain plan",
  "I think we have a complete plan now",
];

function patchSummary(trace?: TraceSummary, traceDetail?: any): { label: string; title: string } | null {
  const patches = traceDetail?.output?.patches;
  if (!Array.isArray(patches) || !patches.length) return null;
  const first = patches[0];
  const nodeType = first?.node?.type;
  const label = patches.length === 1
    ? `✦ built ${nodeType ? `a ${nodeType}` : "UI"}${first?.node?.props?.title ? ` · “${first.node.props.title}”` : ""}`
    : `✦ built ${patches.length} UI patches`;
  return { label, title: patches.map((p: any) => `${p.op}: ${p.node?.type || p.id || "component"}`).join(" · ") };
}

export default function ChatPanel({
  messages, actors, viewer, isAI, loading, content, setContent, onSend,
  tracesByMessage, traceDetails, onSuggestion, conversationTitle,
}: {
  messages: Message[];
  actors: Actor[];
  viewer: string;
  isAI: boolean;
  loading: boolean;
  content: string;
  setContent: (v: string) => void;
  onSend: (e: FormEvent) => void;
  tracesByMessage: Map<string, TraceSummary>;
  traceDetails: Record<string, any>;
  onSuggestion: (text: string) => void;
  conversationTitle?: string;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const viewerActor = actors.find((a) => a.actor_id === viewer);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  return (
    <section className="panel chat-panel" data-tour="composer">
      <div className="panel-header">
        <div>
          <div className="panel-title">{conversationTitle || "Group chat"}</div>
          <div className="panel-subtitle">One shared conversation · the source every workspace is built from</div>
        </div>
        <span className="commit-head">{messages.length} messages</span>
      </div>

      <div className="chat-scroll" ref={scrollRef}>
        {messages.length === 0 && <div className="empty-state">No messages yet — say hello below.</div>}
        {messages.map((message) => {
          const mine = message.actor_id === viewer;
          const pending = message.message_id.startsWith("pending_");
          const trace = tracesByMessage.get(message.message_id);
          const detail = trace ? traceDetails[trace.trace_id] : undefined;
          const built = patchSummary(trace, detail);
          const targetOwnerId = trace?.workspace_id?.replace(/^workspace_/, "");
          const targetOwnerName = targetOwnerId === "ai"
            ? "the shared (✦ AI)"
            : actors.find((a) => a.actor_id === targetOwnerId)?.display_name
              || targetOwnerId
              || viewerActor?.display_name
              || "their";
          const sender = actors.find((a) => a.actor_id === message.actor_id);
          const color = colorForActor({ actor_id: message.actor_id, color: message.color || sender?.color });
          return (
            <div className={`message-row ${mine ? "mine" : ""}`} key={message.message_id} style={pending ? { opacity: 0.55 } : undefined}>
              <Avatar actorId={message.actor_id} name={message.display_name} color={message.color || sender?.color} />
              <div className="message-content">
                <div className="message-meta">
                  <strong style={{ color }}>{message.display_name}</strong>
                  <span>{formatTime(message.created_at)}</span>
                  {message.actor_id === "ai" && trace && (
                    <span className="trace-chip" title={`${trace.provider} · ${trace.model_name} · ${trace.duration_ms}ms`}>
                      {trace.provider} · {(trace.duration_ms / 1000).toFixed(1)}s
                    </span>
                  )}
                </div>
                <Link
                  href={`/debug?message_id=${message.message_id}&user=${viewer}`}
                  className={`bubble ${message.actor_id === "ai" ? "ai-bubble" : ""}`}
                  title="Open this message in Debug: trace + Dolt commit"
                >
                  {message.content}
                </Link>
                {built && !pending && (
                  <div className="built-chip-row">
                    <span className="built-chip" title={built.title}>{built.label}</span>
                    <span className="muted" style={{ fontSize: 9 }}>→ in {targetOwnerName}'s workspace</span>
                  </div>
                )}
                <div className="message-actions">
                  {pending
                    ? <span className="tiny-link">sending…</span>
                    : <Link className="tiny-link" href={`/debug?message_id=${message.message_id}&user=${viewer}`}>history · trace · commit ↗</Link>}
                </div>
              </div>
            </div>
          );
        })}
        {loading && (
          <div className="message-row">
            <Avatar actorId="ai" name="AI" />
            <div className="message-content">
              <div className="message-meta"><strong style={{ color: colorForActor({ actor_id: "ai", color: actors.find((a) => a.actor_id === "ai")?.color }) }}>AI</strong><span>thinking</span></div>
              <div className="bubble ai-bubble"><span className="spinner" /> Thinking… building a reply (and maybe UI) from the group chat.</div>
            </div>
          </div>
        )}
      </div>

      {!isAI && (
        <div className="suggestion-row" aria-label="Try asking the AI to build UI">
          <span className="suggestion-label">Try saying:</span>
          {SUGGESTIONS.map((s) => (
            <button key={s} className="suggestion-chip" onClick={() => onSuggestion(s)} disabled={loading}>{s}</button>
          ))}
        </div>
      )}

      <form className="chat-composer" onSubmit={onSend}>
        <div className="composer-box">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={isAI ? "The model is not a speaking identity — switch to a person above to join the chat." : `Message as ${viewerActor?.display_name || "…"} — chat normally; when you share something concrete, the model quietly builds cards into your workspace →`}
            disabled={isAI || loading}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); (e.currentTarget.form as HTMLFormElement)?.requestSubmit(); } }}
          />
          <div className="composer-footer">
            <span className="hint">Enter to send · every message is a Dolt commit</span>
            <button className="button small" type="submit" disabled={isAI || loading || !content.trim()}>
              {loading ? <><span className="spinner" /> Thinking</> : "Send ↑"}
            </button>
          </div>
        </div>
      </form>
    </section>
  );
}
