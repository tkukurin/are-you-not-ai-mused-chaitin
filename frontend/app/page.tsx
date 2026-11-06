"use client";

import Link from "next/link";
import { FormEvent, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api, colorForActor, shortHash, timeAgo } from "@/lib/api";
import type { Actor, Commit, Conversation, Message, Patch, TraceSummary, UINode, Workspace } from "@/lib/types";
import TopBar, { useViewer } from "@/components/TopBar";
import ChatPanel from "@/components/ChatPanel";
import WorkspacePanel, { makeNode } from "@/components/WorkspacePanel";
import CommitRail from "@/components/CommitRail";
import TutorialTour from "@/components/TutorialTour";

const CHAT_KEY = "museable-chat";
const TOUR_KEY = "museable-tour-done";

function chatInitials(title: string) {
  const initials = title.trim().split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  return initials || "👥";
}

export default function Page() {
  return <Suspense fallback={<div className="empty-state" style={{ padding: 60 }}>Loading studio…</div>}><Studio /></Suspense>;
}

function Studio() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [actors, setActors] = useState<Actor[]>([]);
  const { viewer, setViewer } = useViewer(actors);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const activeConvRef = useRef<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [commits, setCommits] = useState<Commit[]>([]);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [traceDetails, setTraceDetails] = useState<Record<string, any>>({});
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [merging, setMerging] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [draftPatches, setDraftPatches] = useState<Patch[]>([]);
  const [previewNodes, setPreviewNodes] = useState<UINode[] | null>(null);
  const [previewCount, setPreviewCount] = useState<number | null>(null);
  const [selectedNode, setSelectedNode] = useState<UINode | null>(null);
  const [revision, setRevision] = useState<string | null>(null);
  const [historicalWorkspace, setHistoricalWorkspace] = useState<Workspace | null>(null);
  const [status, setStatus] = useState<any>(null);
  // Conversation-manager form state
  const [chatSearch, setChatSearch] = useState("");
  const [showNewConv, setShowNewConv] = useState(false);
  const [newConvTitle, setNewConvTitle] = useState("");
  const [newConvMembers, setNewConvMembers] = useState<string[]>([]);
  const [addMemberId, setAddMemberId] = useState("");
  const [showAddPerson, setShowAddPerson] = useState(false);
  const [newPersonName, setNewPersonName] = useState("");
  const [tourOpen, setTourOpen] = useState(false);
  const tourCheckedRef = useRef(false);


  const viewerActor = useMemo(() => actors.find((a) => a.actor_id === viewer), [actors, viewer]);
  const isAI = viewerActor?.actor_type === "ai";
  const head = commits[0]?.commit_hash || workspace?.head || status?.head || null;

  const activeConv = useMemo(
    () => conversations.find((c) => c.conversation_id === activeConvId) || null,
    [conversations, activeConvId],
  );
  const humans = useMemo(() => actors.filter((a) => a.actor_type === "human"), [actors]);
  const memberActors = useMemo(
    () => (activeConv ? actors.filter((a) => activeConv.member_ids.includes(a.actor_id)) : []),
    [actors, activeConv],
  );
  const humanMembers = useMemo(() => memberActors.filter((a) => a.actor_type === "human"), [memberActors]);
  const filteredConversations = useMemo(() => {
    const q = chatSearch.trim().toLowerCase();
    return q ? conversations.filter((c) => c.title.toLowerCase().includes(q)) : conversations;
  }, [conversations, chatSearch]);
  const nonMembers = useMemo(
    () => humans.filter((a) => !activeConv?.member_ids.includes(a.actor_id)),
    [humans, activeConv],
  );

  const isLiveModel = !!(status && status.model_provider !== "deterministic-demo" && status.llm_mode !== "demo");
  const modelLabel = !status
    ? "connecting…"
    : isLiveModel
      ? `✦ Muse Glimmer · local · ${status.model_name}`
      : "Demo replay · deterministic model";

  const tracesByMessage = useMemo(() => {
    const map = new Map<string, TraceSummary>();
    for (const t of traces) {
      if (t.response_message_id) map.set(t.response_message_id, t);
      if (t.triggering_message_id) map.set(t.triggering_message_id, t);
    }
    return map;
  }, [traces]);

  const setActiveConversation = useCallback((id: string | null) => {
    activeConvRef.current = id;
    setActiveConvId(id);
    if (!id) return;
    try { window.localStorage.setItem(CHAT_KEY, id); } catch { /* private mode */ }
    const params = new URLSearchParams(searchParams.toString());
    if (params.get("chat") !== id) {
      params.set("chat", id);
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    }
  }, [pathname, router, searchParams]);

  const refreshTraces = useCallback(async () => {
    const data = await api<{ traces: any[] }>("/api/debug/traces?limit=40&include_details=true");
    setTraces(data.traces);
    const detailsMap: Record<string, any> = {};
    for (const t of data.traces) detailsMap[t.trace_id] = t;
    setTraceDetails((old) => ({ ...old, ...detailsMap }));
    return data.traces;
  }, []);

  const refreshAll = useCallback(async (conversationId: string | null, ownerId: string) => {
    const [userData, convListData, commitData, traceData, statusData] = await Promise.all([
      api<{ users: Actor[] }>("/api/users"),
      api<{ conversations: Conversation[] }>("/api/conversations"),
      api<{ commits: Commit[] }>("/api/commits?limit=15"),
      api<{ traces: any[] }>("/api/debug/traces?limit=40&include_details=true"),
      api<any>("/api/debug/status"),
    ]);
    setActors(userData.users);
    setConversations(convListData.conversations);
    const list = convListData.conversations;
    let stored: string | null = null;
    try { stored = window.localStorage.getItem(CHAT_KEY); } catch { /* private mode */ }
    const explicit = conversationId || activeConvRef.current
      || new URLSearchParams(window.location.search).get("chat") || stored;
    let chosen = explicit;
    if (chosen && !list.some((c) => c.conversation_id === chosen)) chosen = null;
    // No explicit choice: start blank in the auto-created starter chat.
    if (!chosen) {
      chosen = list.some((c) => c.conversation_id === "starter-chat")
        ? "starter-chat"
        : list[0]?.conversation_id || "group-main";
    }
    if (chosen !== activeConvRef.current) setActiveConversation(chosen);
    const [conversationData, workspaceData] = await Promise.all([
      api<{ messages: Message[] }>(
        `/api/conversation?conversation_id=${encodeURIComponent(chosen)}&limit=100`,
      ),
      // Workspaces are per-conversation — pass conversation_id explicitly.
      api<Workspace>(`/api/workspaces/${ownerId}?conversation_id=${encodeURIComponent(chosen)}`),
    ]);
    setMessages(conversationData.messages);
    setWorkspace(workspaceData);
    setCommits(commitData.commits);
    setTraces(traceData.traces);
    const detailsMap: Record<string, any> = {};
    for (const t of traceData.traces) detailsMap[t.trace_id] = t;
    setTraceDetails((old) => ({ ...old, ...detailsMap }));
    setStatus(statusData);
    return chosen;
  }, [setActiveConversation]);

  useEffect(() => {
    refreshAll(activeConvRef.current, viewer).catch((err) => setError(err.message || "Could not connect to the API. Start the stack with `uv run app` from the repo root."));
  }, [viewer, refreshAll]);

  // Speaking as = a human member of the active chat; auto-switch otherwise.
  useEffect(() => {
    if (!activeConv || !actors.length || !humanMembers.length) return;
    if (!humanMembers.some((a) => a.actor_id === viewer)) setViewer(humanMembers[0].actor_id);
  }, [activeConv, actors.length, humanMembers, viewer, setViewer]);

  // Time travel: fetch the workspace AS OF the selected commit.
  useEffect(() => {
    if (!revision) { setHistoricalWorkspace(null); return; }
    let cancelled = false;
    api<Workspace>(`/api/commits/${revision}/workspace?owner_id=${viewer}${activeConvId ? `&conversation_id=${encodeURIComponent(activeConvId)}` : ""}`)
      .then((data) => { if (!cancelled) setHistoricalWorkspace(data); })
      .catch((err) => { if (!cancelled) setError(err.message); });
    return () => { cancelled = true; };
  }, [revision, viewer, activeConvId]);

  // Auto-open the tour once per user, shortly after conversations load.
  useEffect(() => {
    if (tourCheckedRef.current || !conversations.length) return;
    tourCheckedRef.current = true;
    let done = false;
    try { done = !!window.localStorage.getItem(TOUR_KEY); } catch { /* private mode */ }
    if (done) return;
    const t = window.setTimeout(() => setTourOpen(true), 700);
    return () => window.clearTimeout(t);
  }, [conversations.length]);

  const closeTour = useCallback(() => {
    setTourOpen(false);
    try { window.localStorage.setItem(TOUR_KEY, "1"); } catch { /* private mode */ }
  }, []);

  useEffect(() => {
    setDraftPatches([]); setPreviewNodes(null); setPreviewCount(null); setSelectedNode(null); setRevision(null);
  }, [viewer]);

  function openNewChat() {
    setShowNewConv(true);
    setNewConvMembers(humans.map((h) => h.actor_id));
  }

  function selectConversation(id: string) {
    if (id === activeConvId) return;
    setActiveConversation(id);
    setError("");
    refreshAll(id, viewer).catch((err) => setError(err.message));
  }

  async function createConversation(event: FormEvent) {
    event.preventDefault();
    const title = newConvTitle.trim();
    if (!title || !newConvMembers.length) return;
    setError("");
    try {
      const result = await api<any>("/api/conversations", {
        method: "POST",
        body: JSON.stringify({ title, member_ids: newConvMembers }),
      });
      const conv: Conversation = result.conversation || result;
      setChatSearch("");
      setShowNewConv(false);
      setNewConvTitle("");
      setActiveConversation(conv.conversation_id);
      if (!newConvMembers.includes(viewer)) {
        const firstHuman = newConvMembers.find((id) => humans.some((h) => h.actor_id === id));
        if (firstHuman) setViewer(firstHuman);
      }
      await refreshAll(conv.conversation_id, viewer);
      setNotice(`Created group chat “${conv.title}”.`);
    } catch (err: any) { setError(err.message || "Could not create the chat."); }
  }

  async function addMember(actorId: string) {
    if (!activeConvId || !actorId) return;
    setError("");
    try {
      await api(`/api/conversations/${activeConvId}/members`, {
        method: "POST", body: JSON.stringify({ actor_id: actorId }),
      });
      setAddMemberId("");
      await refreshAll(activeConvId, viewer);
    } catch (err: any) { setError(err.message || "Could not add that person."); }
  }

  async function removeMember(actorId: string) {
    if (!activeConvId) return;
    setError("");
    try {
      await api(`/api/conversations/${activeConvId}/members/${encodeURIComponent(actorId)}`, { method: "DELETE" });
      await refreshAll(activeConvId, viewer);
    } catch (err: any) { setError(err.message || "Could not remove that person."); }
  }

  async function addPerson(event: FormEvent) {
    event.preventDefault();
    const name = newPersonName.trim();
    if (!name) return;
    setError("");
    try {
      const result = await api<{ actor: Actor }>("/api/people", {
        method: "POST", body: JSON.stringify({ display_name: name }),
      });
      const actor = result.actor;
      if (activeConvId) {
        await api(`/api/conversations/${activeConvId}/members`, {
          method: "POST", body: JSON.stringify({ actor_id: actor.actor_id }),
        });
      }
      setNewPersonName("");
      setShowAddPerson(false);
      await refreshAll(activeConvId, viewer);
      setNotice(`Added ${actor.display_name} to this chat — switch “Viewing” above to speak as them.`);
    } catch (err: any) { setError(err.message || "Could not add that person."); }
  }

  async function mergeWorkspaces() {
    if (!activeConvId || isAI || merging || loading) return;
    setMerging(true); setError(""); setNotice("");
    try {
      await api<any>("/api/workspaces/merge", {
        method: "POST",
        body: JSON.stringify({ actor_id: viewer, conversation_id: activeConvId }),
      });
      await refreshAll(activeConvId, viewer);
      setNotice("Merge sent — the model is combining everyone's workspaces into the shared (✦ AI) workspace. (It also does this on its own when the group wraps up.) See the new messages above and the commit in History.");
    } catch (err: any) { setError(err.message || "Merge failed."); }
    finally { setMerging(false); }
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const text = content.trim();
    if (!text || isAI || loading) return;
    setLoading(true); setError(""); setNotice("");
    setContent("");
    setMessages((old) => [...old, {
      message_id: `pending_${Date.now()}`, conversation_id: activeConvId || "group-main", actor_id: viewer,
      display_name: viewerActor?.display_name || viewer, color: viewerActor?.color || "gray",
      content: text, message_type: "text", trace_id: null, created_at: new Date().toISOString(), metadata: {},
    }]);
    try {
      const result = await api<any>("/api/chat", {
        method: "POST",
        body: JSON.stringify({ actor_id: viewer, content: text, conversation_id: activeConvId }),
      });
      setMessages(result.messages);
      setWorkspace(result.workspace);
      setCommits((await api<{ commits: Commit[] }>("/api/commits?limit=15")).commits);
      await refreshTraces();
      // A chat can create commits / members can change counts — refresh the selector quietly.
      api<{ conversations: Conversation[] }>("/api/conversations").then((d) => setConversations(d.conversations)).catch(() => {});
      const built = result.merged
        ? " · AI merged everyone's workspaces into the shared app"
        : result.patches_applied?.length
          ? ` · AI built ${result.patches_applied.map((p: Patch) => p.node?.type || p.op).join(", ")}`
          : result.ai_silent
            ? " · saved to group chat (AI stays quiet on casual chat — share something concrete and it builds UI)"
            : "";
      setNotice(`Committed ${shortHash(result.commit_hash)}${built}`);
      if (result.patch_warning) setError(`AI replied, but its UI patch was rejected: ${result.patch_warning}`);
    } catch (err: any) {
      setError(err.message || "Message failed.");
      setMessages((old) => old.filter((m) => !m.message_id.startsWith("pending_")));
      setContent(text);
    } finally { setLoading(false); }
  }

  function addDraft(type: string) {
    if (!workspace || isAI || revision) return;
    const root = workspace.flat_nodes.find((n) => !n.parent_id);
    if (!root) return;
    const node = makeNode(type);
    setDraftPatches((old) => [...old, { op: "insert", parent_id: root.id, node }]);
    setSelectedNode(node);
    setPreviewNodes(null); setPreviewCount(null);
    setNotice(`${type} staged as a draft — preview, then commit it to Dolt. Or just ask the AI in chat.`);
  }

  function deleteSelected() {
    if (!selectedNode || isAI || revision) return;
    const wasDraftInsert = draftPatches.some((p) => p.op === "insert" && p.node?.id === selectedNode.id);
    setDraftPatches((old) =>
      wasDraftInsert
        ? old.filter((p) => !(p.op === "insert" && p.node?.id === selectedNode.id))
        : [...old, { op: "remove", id: selectedNode.id }]
    );
    setSelectedNode(null);
    setPreviewNodes(null); setPreviewCount(null);
    setNotice(
      wasDraftInsert
        ? `Removed uncommitted ${selectedNode.type} draft.`
        : `Removal of ${selectedNode.type} staged — commit to apply.`
    );
  }

  async function previewDraft() {
    if (!draftPatches.length || revision) return;
    try {
      const result = await api<any>(`/api/workspaces/${viewer}/preview`, {
        method: "POST", body: JSON.stringify({ actor_id: viewer, summary: "Preview workspace changes", patches: draftPatches, conversation_id: activeConvId }),
      });
      setPreviewCount(result.flat_nodes.length);
      setPreviewNodes(result.nodes);
      setNotice(`Preview valid · ${result.flat_nodes.length} components in the resulting tree. Commit when it looks right.`);
      setError("");
    } catch (err: any) { setError(err.message); }
  }

  async function commitDraft() {
    if (!draftPatches.length || revision) return;
    setLoading(true); setError("");
    const summary = draftPatches
      .map((p) => `${p.op} ${p.node?.type || p.id || "component"}`)
      .join(", ")
      .slice(0, 180);
    try {
      const result = await api<any>(`/api/workspaces/${viewer}/commit`, {
        method: "POST", body: JSON.stringify({ actor_id: viewer, summary, patches: draftPatches, conversation_id: activeConvId }),
      });
      setWorkspace(result.workspace);
      setCommits(result.commits?.length ? result.commits : (await api<{ commits: Commit[] }>("/api/commits?limit=15")).commits);
      setDraftPatches([]); setPreviewNodes(null); setPreviewCount(null); setSelectedNode(null);
      setNotice(`Committed to Dolt · ${shortHash(result.commit_hash)}`);
    } catch (err: any) { setError(err.message); }
    finally { setLoading(false); }
  }

  const shownWorkspace = revision ? historicalWorkspace : workspace;

  return (
    <>
      <TopBar viewer={viewer} />
      <main className="app-shell">
        <div className="tutorial">
          <div className="tutorial-main">
            <div className="eyebrow">Try it yourself</div>
            <h1>Try it: chat, and the model builds the app</h1>
          </div>
          <div className="tutorial-actions">
            <Link href="/demo" className="button">▶ Watch example</Link>
            <button type="button" className="button secondary" onClick={() => setTourOpen(true)}>↺ Tutorial</button>
            <span className="status-pill" title={status ? `${status.llm_mode} · ${status.model_provider} · ${status.model_name}` : undefined}>
              <span className="status-dot" /> {modelLabel}
            </span>
          </div>
        </div>
        <TutorialTour open={tourOpen} onClose={closeTour} />
        {error && <div className="error-banner">{error}</div>}
        {notice && !error && <div className="success-banner">{notice}</div>}

        <div className="app-layout" id="studio">
          <aside className="chat-rail" aria-label="Group chats" data-tour="chats">
            <div className="rail-head">
              <h2 className="rail-title">Chats</h2>
              <button
                type="button" className="rail-new-btn"
                onClick={openNewChat} aria-label="New group chat" title="New group chat"
              >＋</button>
            </div>
            {showNewConv ? (
              <form className="inline-form rail-form" onSubmit={createConversation}>
                <input
                  className="field" placeholder="Name the chat — e.g. Book club" value={newConvTitle}
                  onChange={(e) => setNewConvTitle(e.target.value)} autoFocus
                />
                <div className="member-checks">
                  {humans.map((h) => (
                    <label key={h.actor_id} className="member-check">
                      <input
                        type="checkbox" checked={newConvMembers.includes(h.actor_id)}
                        onChange={(e) => setNewConvMembers((old) => e.target.checked ? [...old, h.actor_id] : old.filter((id) => id !== h.actor_id))}
                      />
                      <span className="member-dot" style={{ background: colorForActor(h) }} />{h.display_name}
                    </label>
                  ))}
                </div>
                <div className="inline-form-actions">
                  <button className="button small" type="submit" disabled={!newConvTitle.trim() || !newConvMembers.length}>Create chat</button>
                  <button className="button ghost small" type="button" onClick={() => setShowNewConv(false)}>Cancel</button>
                </div>
              </form>
            ) : (
              <>
                <div className="rail-search">
                  <input
                    className="field" type="search" placeholder="Search chats" value={chatSearch}
                    onChange={(e) => setChatSearch(e.target.value)} aria-label="Search chats"
                  />
                </div>
                <div className="rail-list">
                  {!conversations.length ? (
                    <div className="rail-empty">
                      <p>No chats yet — start your first one.</p>
                      <button type="button" className="button small" onClick={openNewChat}>＋ New group chat</button>
                    </div>
                  ) : filteredConversations.length ? filteredConversations.map((c) => (
                    <button
                      key={c.conversation_id} type="button"
                      className={`rail-row ${c.conversation_id === activeConvId ? "active" : ""}`}
                      onClick={() => selectConversation(c.conversation_id)}
                      title={`${c.member_ids.length} members · ${c.message_count ?? 0} messages`}
                      aria-current={c.conversation_id === activeConvId ? "true" : undefined}
                    >
                      <span className="rail-avatar" aria-hidden>{chatInitials(c.title)}</span>
                      <span className="rail-row-main">
                        <strong>{c.title}</strong>
                        <span>{c.member_ids.length} people · {c.message_count ?? 0} messages{c.created_at ? ` · ${timeAgo(c.created_at)}` : ""}</span>
                      </span>
                    </button>
                  )) : (
                    <div className="rail-empty"><p>No chats match “{chatSearch.trim()}”.</p></div>
                  )}
                </div>
              </>
            )}
            <div className="rail-new-ui">
              <strong>＋ Start a new UI</strong>
              <p>Every chat starts blank. The model builds a different app for each person in it.</p>
              <button type="button" className="button small" onClick={openNewChat}>＋ New group chat</button>
            </div>
          </aside>
          <div className="app-main-col">
            {activeConv && (
              <div className="members-bar" data-tour="members">
                <div className="members-bar-head">
                  <span className="members-bar-label">Members — click to speak as:</span>
                  <button type="button" className="button secondary small" onClick={() => setShowAddPerson((v) => !v)}>＋ Add person</button>
                </div>
                {/* Tour: both [data-tour=members] (bar) and [data-tour=viewer] (row) land here. */}
                <div className="member-row" data-tour="viewer">
                  {memberActors.map((a) => {
                    const isSpeaking = a.actor_id === viewer;
                    const color = colorForActor(a);
                    return a.actor_type === "ai" ? (
                      <span key={a.actor_id} className="member-chip" title="The AI participant">
                        <span className="member-dot" style={{ background: color }} />
                        {a.display_name}
                        <span className="muted">✦ model</span>
                      </span>
                    ) : (
                      <span
                        key={a.actor_id}
                        className={`member-chip speaking-chip ${isSpeaking ? "active" : ""}`}
                        style={isSpeaking ? { borderColor: color, background: `color-mix(in srgb, ${color} 12%, white)`, boxShadow: `inset 0 0 0 1px ${color}` } : undefined}
                      >
                        <button
                          type="button" className="member-speak"
                          onClick={() => setViewer(a.actor_id)}
                          aria-pressed={isSpeaking}
                          title={isSpeaking ? `Speaking as ${a.display_name}` : `Speak as ${a.display_name}`}
                        >
                          <span className="member-dot" style={{ background: color }} />
                          {a.display_name}
                          {isSpeaking && <span className="speaking-dot" aria-hidden>● speaking</span>}
                        </button>
                        <button type="button" className="member-remove" title={`Remove ${a.display_name} from this chat`} onClick={() => removeMember(a.actor_id)}>×</button>
                      </span>
                    );
                  })}
                  {nonMembers.length > 0 && (
                    <span className="member-add">
                      <select className="field" value={addMemberId} onChange={(e) => setAddMemberId(e.target.value)} aria-label="Add an existing person">
                        <option value="">＋ Add member…</option>
                        {nonMembers.map((a) => <option key={a.actor_id} value={a.actor_id}>{a.display_name}</option>)}
                      </select>
                      <button type="button" className="button secondary small" disabled={!addMemberId} onClick={() => addMember(addMemberId)}>Add</button>
                    </span>
                  )}
                </div>
                {showAddPerson && (
                  <form className="inline-form" onSubmit={addPerson}>
                    <input
                      className="field" placeholder="New person's name — e.g. Sam" value={newPersonName}
                      onChange={(e) => setNewPersonName(e.target.value)} autoFocus
                    />
                    <div className="inline-form-actions">
                      <button className="button small" type="submit" disabled={!newPersonName.trim()}>Add person &amp; join chat</button>
                      <button className="button ghost small" type="button" onClick={() => setShowAddPerson(false)}>Cancel</button>
                    </div>
                  </form>
                )}
              </div>
            )}
            <ChatPanel
              messages={messages}
              actors={actors}
              viewer={viewer}
              isAI={isAI}
              loading={loading}
              content={content}
              setContent={setContent}
              onSend={sendMessage}
              tracesByMessage={tracesByMessage}
              traceDetails={traceDetails}
              onSuggestion={(text) => { setContent(text); }}
              conversationTitle={activeConv?.title}
            />
          </div>
          <div className="workspace-column">
            <div className="merge-bar" data-tour="merge">
              <button
                type="button"
                className="button ghost small"
                onClick={mergeWorkspaces}
                disabled={merging || loading || isAI || !activeConvId}
                title="The model also merges on its own when the group wraps up."
              >
                {merging ? <><span className="spinner" /> Merging…</> : "Merge now (optional)"}
              </button>
              <span className="muted" style={{ fontSize: 10.5 }}>The model also merges on its own when the group wraps up — this merges right now if you don't want to wait.</span>
            </div>
            <WorkspacePanel
              workspace={shownWorkspace}
              ownerName={viewerActor?.display_name || "User"}
              isAI={isAI}
              isHistorical={!!revision}
              revision={revision}
              draftPatches={draftPatches}
              previewNodes={previewNodes}
              previewCount={previewCount}
              onAddDraft={addDraft}
              onDiscardDraft={() => { setDraftPatches([]); setPreviewNodes(null); setPreviewCount(null); }}
              onPreviewDraft={previewDraft}
              onCommitDraft={commitDraft}
              selectedNode={selectedNode}
              onSelectNode={setSelectedNode}
              onDeleteSelected={deleteSelected}
              loading={loading}
            />
          </div>
          <CommitRail
            commits={commits}
            head={head}
            selectedRevision={revision}
            onSelectRevision={setRevision}
            viewer={viewer}
          />
        </div>

        <div className="studio-footer">
          <span>You are trying the live app — messages and built cards are saved as Dolt commits; use <strong>History</strong> to rewind. <strong>Viewing as</strong> = who you speak as; there are no accounts in this local app.</span>
          <span className="muted">One Dolt <span className="mono">main</span> branch · a personal workspace per person · click any chat bubble for its trace &amp; commit in Debug</span>
        </div>
      </main>
    </>
  );
}
