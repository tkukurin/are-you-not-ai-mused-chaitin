"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Actor, Message, UINode, Workspace } from "@/lib/types";
import { WorkspaceTree } from "@/components/NodeView";

/**
 * Demo = watching real people talk. The 54-beat street-party history is
 * seeded in Dolt (pre-seeded history in .dolt-data/). This page just
 * REPLAYS it in sync: chat scrolls on the left; on the right you flip
 * between neighbours' apps and watch panels appear as the beats that
 * created them scroll past. AI is an aside — it only surfaces when a
 * build landed, as a tiny "built X into Y's app" line.
 */
type Beat = {
  step: number;
  actor: string;
  name: string;
  text: string;
  aiText: string | null;
  built: string[];
  builtIds: string[];
  builtInto: string | null;
  reasoning: string | null;
  reasoningTokens: number;
};

const ROLES: Record<string, string> = {
  alice: "Host & Producer",
  bob: "Budget & Weather Risk",
  charlie: "Street Layout & Sound",
  dana: "Corner Café & Bar",
  ai: "High-Thinking Synthesis of All 4 Apps",
};

const OWNER_ACCENT: Record<string, string> = {
  alice: "#2f6fd1",
  bob: "#b07d1e",
  charlie: "#1e8a5f",
  dana: "#c2457a",
  ai: "#008069",
};

function describeStep(step: Beat | undefined, nameOf: (id: string) => string): { label: string; headline: string } {
  if (!step) return { label: "Start", headline: "Four neighbours, one group chat, and no app yet — press Play or Next message." };
  if (step.builtInto === "ai" && step.built.length > 0) {
    return { label: "Merged apps", headline: `The model combined everyone's workspaces into one shared app (${step.built.length} cards).` };
  }
  if (step.built.length > 0) {
    const isSetOnly = step.built.every((op) => op === "set" || op.startsWith("↻"));
    const targetName = nameOf(step.builtInto || step.actor);
    return isSetOnly
      ? { label: "Updated an app", headline: `${targetName}'s workspace was updated: ${step.built.join(", ")}.` }
      : { label: "Grew an app", headline: `${targetName}'s workspace gained: ${step.built.join(" + ")}.` };
  }
  return { label: "Chat", headline: `${step.name} sent a message — the model just listened this time.` };
}

function visibleComponentCount(nodes: UINode[]): number {
  let count = 0;
  const walk = (list: UINode[]) => {
    for (const n of list) {
      if (n.type !== "Grid") count += 1;
      if (n.children?.length) walk(n.children);
    }
  };
  walk(nodes);
  return count;
}

export default function DemoPage() {
  const [actors, setActors] = useState<Actor[]>([]);
  const [beats, setBeats] = useState<Beat[]>([]);
  const [shown, setShown] = useState(0);
  const [viewing, setViewing] = useState("alice");
  const [viewAll, setViewAll] = useState(false);
  const [autoFollow, setAutoFollow] = useState(true);
  const [showReasoning, setShowReasoning] = useState(true);
  const [wsByUser, setWsByUser] = useState<Record<string, Workspace>>({});

  const [playing, setPlaying] = useState(false);
  const [err, setErr] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const wsBodyRef = useRef<HTMLDivElement | null>(null);

  const humans = useMemo(() => actors.filter(a => a.actor_type === "human"), [actors]);
  const nameOf = useCallback((id: string) => actors.find(a => a.actor_id === id)?.display_name || id, [actors]);

  const activeRoles = ROLES;

  // Build replay beats from the seeded history: each human message is a beat,
  // its AI reply (if any) is an aside, and applied patches are "built" chips.
  const loadDemoData = useCallback(async () => {
    try {
      const [users, conv, traces] = await Promise.all([
        api<{ users: Actor[] }>("/api/users"),
        api<{ messages: Message[] }>("/api/conversation"),
        api<{ traces: any[] }>("/api/debug/traces?limit=100&include_details=true"),
      ]);
      setActors(users.users);
      const traceByTrigger = new Map<string, any>();
      for (const t of traces.traces) {
        traceByTrigger.set(t.triggering_message_id, t);
      }
      const builtByStep: Record<string, { types: string[]; into: string }> = {};
      const out: Beat[] = [];
      let step = 0;
      for (const m of conv.messages) {
        if (m.actor_id === "ai") continue;
        step += 1;
        const t = traceByTrigger.get(m.message_id);
        let aiText: string | null = null;
        let built: string[] = [];
        let builtIds: string[] = [];
        let builtInto: string | null = null;
        let reasoning: string | null = null;
        let reasoningTokens = 0;
        if (t) {
          const patches = t.output?.patches || [];
          built = patches.map((p: any) => p.op === "set" ? `↻ ${p.node?.type || "update"}` : (p.node?.type || p.op)).filter(Boolean);
          builtIds = patches.map((p: any) => p.node?.id || p.id).filter(Boolean);
          aiText = t.output?.reply || null;
          builtInto = t.workspace_id?.replace("workspace_", "") || null;
          reasoning = t.output?.reasoning || null;
          reasoningTokens = t.input?.routing?.attempts?.[0]?.reasoning_tokens || 0;
          if (built.length) builtByStep[m.message_id] = { types: built, into: builtInto || m.actor_id };
        }
        out.push({ step, actor: m.actor_id, name: m.display_name, text: m.content, aiText, built, builtIds, builtInto, reasoning, reasoningTokens });
      }
      setBeats(out);
      const all: Record<string, Workspace> = {};
      await Promise.all(users.users.map(async (u) => {
        all[u.actor_id] = await api<Workspace>(`/api/workspaces/${u.actor_id}`);
      }));
      setWsByUser(all);
    } catch (e: any) {
      setErr(e.message || "Start the stack with `uv run demo` from the repo root.");
    }
  }, []);

  useEffect(() => {
    loadDemoData();
  }, [loadDemoData]);

  // Compute pruned workspace nodes & newly-grown IDs for any user at the current `shown` beat.
  const getWorkspaceStateAtBeat = useCallback((userId: string, atBeat: number): { nodes: UINode[]; addedIds: Set<string>; count: number } => {
    const ws = wsByUser[userId];
    if (!ws) return { nodes: [], addedIds: new Set(), count: 0 };
    // Collect all node IDs that were built in beats <= atBeat vs > atBeat for this user.
    const builtUpToNow = new Set<string>();
    const builtAfterNow = new Set<string>();
    let totalBuiltEver = 0;
    for (let i = 0; i < beats.length; i++) {
      const b = beats[i];
      if (b.builtInto === userId) {
        totalBuiltEver += b.builtIds.length;
        for (const id of b.builtIds) {
          if (i < atBeat) builtUpToNow.add(id);
          else builtAfterNow.add(id);
        }
      }
    }
    const currentBeat = atBeat > 0 ? beats[atBeat - 1] : null;
    const addedIds = new Set<string>(
      currentBeat && currentBeat.builtInto === userId ? currentBeat.builtIds : []
    );

    const seedBoilerplate = new Set([
      `${userId}_intro`, `${userId}_ideas`, `${userId}_activity`, `${userId}_next`,
    ]);

    const prune = (nodes: UINode[]): UINode[] =>
      nodes
        .filter((n) => {
          if (n.type === "Grid") return true;
          if (seedBoilerplate.has(n.id)) return false;
          if (atBeat === 0) return false;
          if (builtAfterNow.has(n.id) && !builtUpToNow.has(n.id)) return false;
          if (totalBuiltEver > 0 && builtUpToNow.size === 0) return false;
          return true;
        })
        .map((n) => ({ ...n, children: n.children ? prune(n.children) : n.children }));

    const pruned = prune(ws.nodes);
    return { nodes: pruned, addedIds, count: visibleComponentCount(pruned) };
  }, [wsByUser, beats]);

  // Auto-follow the neighbour (or merged AI app) whose workspace just grew on the current beat.
  useEffect(() => {
    if (!autoFollow || shown <= 0) return;
    const b = beats[shown - 1];
    if (b?.builtInto && b.built.length > 0) {
      setViewAll(false);
      setViewing(b.builtInto);
    }
  }, [shown, autoFollow, beats]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    // Also scroll workspace to newly added cards if any
    const wsEl = wsBodyRef.current;
    if (wsEl) {
      window.setTimeout(() => {
        const addedCard = wsEl.querySelector('[data-just-added="true"]') as HTMLElement | null;
        if (addedCard) addedCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 60);
    }
  }, [shown]);

  // Keyboard: arrows step through messages, Space plays/pauses.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "ArrowRight") {
        e.preventDefault();
        setShown((s) => Math.min(beats.length, s + 1));
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        setShown((s) => Math.max(0, s - 1));
      } else if (e.key === " ") {
        e.preventDefault();
        setPlaying((p) => !p);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [beats.length]);

  // Crisp autoplay: advance every 1.5s while playing.
  useEffect(() => {
    if (!playing) return;
    if (shown >= beats.length) { setPlaying(false); return; }
    const id = window.setTimeout(() => setShown((s) => Math.min(s + 1, beats.length)), 1500);
    return () => clearTimeout(id);
  }, [playing, shown, beats.length]);

  const jumpToNextGrowth = useCallback(() => {
    for (let i = shown; i < beats.length; i++) {
      if (beats[i].built.length > 0) {
        setShown(i + 1);
        return;
      }
    }
    setShown(beats.length);
  }, [shown, beats]);

  const visible = beats.slice(0, shown);
  const speakerOfBeat = beats[shown - 1];
  const currentState = getWorkspaceStateAtBeat(viewing, shown);
  const mergedState = getWorkspaceStateAtBeat("ai", shown);
  const mergeBeat = useMemo(() => beats.slice(0, shown).reverse().find((b) => b.builtInto === "ai" && b.reasoning), [beats, shown]);
  const stepStory = useMemo(() => describeStep(speakerOfBeat, nameOf), [speakerOfBeat, nameOf]);
  const ownerAccent = OWNER_ACCENT[viewing] || "#008069";

  return (
    <div className="demo-frame" style={{ "--owner-accent": ownerAccent } as React.CSSProperties}>
      <div className="demo-stage">
        <header className="demo-top">
          <div className="demo-title">
            <span className="brand-mark">🎪</span>
            <div>
              <strong>Watch an example</strong>
              <p>Four neighbours plan a street party in one group chat — watch a different app grow for each of them, then merge into one shared app.</p>
            </div>
          </div>
          <div className="demo-switch" role="tablist" aria-label="Whose app?">
            {humans.map(a => {
              const st = getWorkspaceStateAtBeat(a.actor_id, shown);
              const justGrew = speakerOfBeat?.builtInto === a.actor_id && speakerOfBeat.built.length > 0;
              return (
                <button key={a.actor_id} role="tab" aria-selected={!viewAll && viewing === a.actor_id}
                  className={`viewer-chip ${!viewAll && viewing === a.actor_id ? "active" : ""} ${justGrew ? "just-grew-tab" : ""}`}
                  onClick={() => { setViewAll(false); setViewing(a.actor_id); }}>
                  <span className={`demo-avatar ${a.actor_id}`}>{a.display_name[0]}</span>
                  <span>{a.display_name}</span>
                  <span className={`viewer-chip-count ${st.count > 0 ? "grown" : ""} ${justGrew ? "pulse" : ""}`}>{st.count}</span>
                </button>
              );
            })}
            <button
              className={`viewer-chip ${viewAll ? "active" : ""}`}
              onClick={() => setViewAll((v) => !v)}
              title="Compare everyone's apps side by side"
            >
              <span>▦ All {humans.length || 4}</span>
            </button>
            <button
              className={`viewer-chip merged-chip ${!viewAll && viewing === "ai" ? "active" : ""} ${speakerOfBeat?.builtInto === "ai" ? "just-grew-tab" : ""}`}
              onClick={() => { setViewAll(false); setViewing("ai"); }}
              title="View the merged app combining everyone's workspaces"
            >
              <span className="demo-avatar sm ai">✦</span>
              <span>Merged App</span>
              <span className={`viewer-chip-count ${mergedState.count > 0 ? "grown" : ""} ${speakerOfBeat?.builtInto === "ai" ? "pulse" : ""}`}>{mergedState.count}</span>
            </button>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10, cursor: "pointer", color: "rgba(255,255,255,.85)" }}>
              <input type="checkbox" checked={autoFollow} onChange={(e) => setAutoFollow(e.target.checked)} style={{ accentColor: "#25d366" }} />
              Follow whoever's app just grew
            </label>
            <Link href={`/?user=${viewing}`} className="nav-link" style={{ background: "rgba(255,255,255,.14)", padding: "4px 7px", fontSize: 10.5 }}>Try it yourself ↗</Link>
            <Link href={`/debug?user=${viewing}`} className="nav-link" style={{ background: "rgba(255,255,255,.14)", padding: "4px 7px", fontSize: 10.5 }}>Debug ↗</Link>
          </div>
        </header>

        <p className="demo-status-line">
          Message {shown} of {beats.length} — <span>{stepStory.label}: {stepStory.headline}</span>
        </p>

        <div className="demo-main">
          <section className="demo-chat panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Elm Street Courtyard Feast — group chat ({humans.length || 4} people)</div>
                <div className="panel-subtitle">The model mostly just listens, and builds cards into someone's app when a message asks for something concrete.</div>
              </div>
              <span className="commit-head">message {shown}/{beats.length}</span>
            </div>
            <div className="demo-messages" ref={scrollRef}>
              {visible.map(b => {
                const isLatest = b.step === shown;
                return (
                <div key={b.step} className={`demo-beat-group ${isLatest ? "is-latest-beat" : ""}`}>
                  <div className={`message-row ${b.actor === viewing ? "mine" : ""}`}>
                    <div className={`demo-avatar sm ${b.actor}`}>{(b.name || "?")[0]}</div>
                    <div className="message-content">
                      <div className="message-meta">
                        <span className="beat-num-chip">#{b.step}</span>
                        <strong>{b.name}</strong>
                        {activeRoles[b.actor] && <span className="muted">· {activeRoles[b.actor]}</span>}
                      </div>
                      <div className={`bubble ${isLatest ? "current-beat-bubble" : ""}`}>{b.text}</div>
                      {b.built.length > 0 && (
                        <button
                          type="button"
                          className="demo-bubble-built-btn"
                          onClick={() => { setViewAll(false); setViewing(b.builtInto || b.actor); }}
                        >
                          {b.builtInto === "ai"
                            ? `✦ Merged everyone's apps into one shared app (${b.built.length} cards) →`
                            : `✦ grew ${nameOf(b.builtInto || b.actor)}'s app (+${b.built.length}: ${b.built.join(", ")}) →`}
                        </button>
                      )}
                    </div>
                  </div>
                  {b.aiText && (
                    <div className="message-row">
                      <div className="demo-avatar sm ai">✦</div>
                      <div className="message-content">
                        <div className="bubble ai-bubble" style={{ opacity: .92 }}>{b.aiText}</div>
                      </div>
                    </div>
                  )}
                </div>
                );
              })}
              {!visible.length && (
                <div className="seed-canvas" style={{ margin: "24px 0" }}>
                  <div className="seed-canvas-icon">💬</div>
                  <div className="seed-canvas-title">A blank slate and a neighbourhood idea</div>
                  <p>Press <strong>Next growth ✦ →</strong> or <strong>▶ Play</strong> below. Four neighbours talk in one shared thread while a different app grows for each of them on the right.</p>
                </div>
              )}
            </div>
          </section>

          <section className="demo-workspace panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">
                  {viewAll
                    ? `All ${humans.length || 4} Personal Views — Grown from the Same Chat`
                    : viewing === "ai"
                      ? `✦ Shared Merged View · ${activeRoles.ai || ROLES.ai}`
                      : `${nameOf(viewing)}'s Workspace · ${activeRoles[viewing] || "Personal Workspace"}`}
                </div>
                <div className="panel-subtitle">
                  {viewAll
                    ? "Each neighbour's workspace sprouts components shaped to their role in the event."
                    : viewing === "ai"
                      ? "Synthesized by Muse Glimmer across Alice, Bob, Charlie, and Dana's workspaces."
                      : `Grown organically from shared chat into ${nameOf(viewing)}'s personal workspace.`}
                </div>
              </div>
              <span className="status-pill">
                <span className="status-dot" />
                {viewAll
                  ? `${humans.reduce((acc, h) => acc + getWorkspaceStateAtBeat(h.actor_id, shown).count, 0)} total components grown`
                  : `${currentState.count} components grown`}
              </span>
            </div>
            <div className="demo-ws-body" ref={wsBodyRef}>
              {viewAll ? (
                <div className="demo-all-grid">
                  {humans.map((h) => {
                    const st = getWorkspaceStateAtBeat(h.actor_id, shown);
                    return (
                      <div key={h.actor_id} className="demo-mini-ws" onClick={() => { setViewAll(false); setViewing(h.actor_id); }}>
                        <div className="demo-mini-ws-head">
                          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                            <span className={`demo-avatar sm ${h.actor_id}`}>{h.display_name[0]}</span>
                            <strong>{h.display_name}</strong>
                            <span className="muted" style={{ fontSize: 10 }}>· {ROLES[h.actor_id] || ""}</span>
                          </div>
                          <span className="commit-head">{st.count} components</span>
                        </div>
                        <div className="demo-mini-ws-body">
                          <WorkspaceTree
                            nodes={st.nodes}
                            addedIds={st.addedIds}
                            emptyText={`${h.display_name}'s seed canvas — waiting for their part of the plan.`}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : !wsByUser[viewing] ? (
                <div className="empty-state">Loading workspace…</div>
              ) : (
                <>
                  {viewing === "ai" && mergeBeat?.reasoning && (
                    <div className="reasoning-box">
                      <div className="reasoning-head" onClick={() => setShowReasoning((s) => !s)}>
                        <div>
                          <span className="reasoning-badge">✦ MERGE</span>
                          <strong>How the merged app was built</strong>
                          {mergeBeat.reasoningTokens > 0 && (
                            <span className="muted" style={{ marginLeft: 6 }}>· {mergeBeat.reasoningTokens} reasoning tokens</span>
                          )}
                        </div>
                        <button type="button" className="button ghost small">{showReasoning ? "Hide thought process ▲" : "Show thought process ▼"}</button>
                      </div>
                      {showReasoning && (
                        <pre className="reasoning-body">{mergeBeat.reasoning}</pre>
                      )}
                    </div>
                  )}
                  <WorkspaceTree
                    nodes={currentState.nodes}
                    addedIds={currentState.addedIds}
                    emptyText={
                      viewing === "ai"
                        ? "Keep stepping through the messages to the end, where everyone's apps are merged into one shared app."
                        : `${nameOf(viewing)}'s workspace is a blank seed. Step through the chat to watch their personal app grow from the group's ideas.`
                    }
                  />
                </>
              )}
            </div>
          </section>
        </div>

        <footer className="demo-control">
          <div className="demo-progress">
            <span>
              <strong>{shown === 0 ? "Start" : `Message ${shown} of ${beats.length}`}</strong>
              {speakerOfBeat ? ` — ${speakerOfBeat.name}: “${speakerOfBeat.text.slice(0, 72)}${speakerOfBeat.text.length > 72 ? "…" : ""}”` : ""}
            </span>
            <span>Use <strong>Next growth ✦</strong> to jump to the moments an app changes, or <strong>▦ All 4</strong> to compare everyone's apps. Keys: ← → step, Space play/pause.</span>
          </div>
          <div className="demo-actions">
            <button className="button ghost small" onClick={() => { setPlaying(false); setShown(0); }} disabled={shown === 0 && !playing}>⟲ Reset</button>
            <button className="button secondary small" onClick={() => setShown(s => Math.max(0, s - 1))} disabled={shown === 0}>← Back</button>
            <button className="button secondary small" onClick={() => setShown(s => Math.min(beats.length, s + 1))} disabled={shown >= beats.length}>Next message →</button>
            <button className="button secondary small" onClick={jumpToNextGrowth} disabled={shown >= beats.length} style={{ borderColor: "#008069", color: "#006a57", fontWeight: 700 }}>Next growth ✦ →</button>
            <button className="button" onClick={() => { if (shown >= beats.length) setShown(0); setPlaying(p => !p); }}>
              {playing ? "❚❚ Pause" : shown >= beats.length ? "▶ Replay" : "▶ Play"}
            </button>
          </div>
        </footer>
      </div>

      {err && <div className="error-banner">{err}</div>}
    </div>
  );
}
