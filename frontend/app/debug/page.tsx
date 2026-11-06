"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { api, formatTime, shortHash, timeAgo } from "@/lib/api";
import type { TraceSummary as SharedTraceSummary } from "@/lib/types";
import TopBar, { useViewer } from "@/components/TopBar";
import { WorkspaceTree } from "@/components/NodeView";

type Tab = "prompts" | "traces" | "history" | "database";
type TraceSummary = SharedTraceSummary;
type Commit = { commit_hash: string; committer: string; date: string; message: string };

function pretty(value: unknown): string { return JSON.stringify(value, null, 2); }

export default function Page() {
  return <Suspense fallback={<div className="empty-state" style={{ padding: 60 }}>Loading debug console…</div>}><DebugPage /></Suspense>;
}

function DebugPage() {
  const [tab, setTab] = useState<Tab>("traces");
  const [actors, setActors] = useState<any[]>([]);
  const [activePrompt, setActivePrompt] = useState<any>(null);
  const [promptVersions, setPromptVersions] = useState<any[]>([]);
  const [promptText, setPromptText] = useState("");
  const [modelName, setModelName] = useState("muse-glimmer-30b");
  const [temperature, setTemperature] = useState("0.2");
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [selectedTraceId, setSelectedTraceId] = useState("");
  const [selectedTrace, setSelectedTrace] = useState<any>(null);
  const [commits, setCommits] = useState<Commit[]>([]);
  const [status, setStatus] = useState<any>(null);
  const [messageContext, setMessageContext] = useState<any>(null);
  const [historicalWorkspace, setHistoricalWorkspace] = useState<any>(null);
  const [historicalOwner, setHistoricalOwner] = useState("alice");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const searchParams = useSearchParams();
  const { viewer: viewingUser, setViewer: setViewingUser } = useViewer(actors);
  const [historicalRendered, setHistoricalRendered] = useState(false);
  const loadAll = useCallback(async () => {
    const [promptData, traceData, commitData, statusData, userData] = await Promise.all([
      api<any>("/api/debug/prompts"), api<any>("/api/debug/traces"),
      api<any>("/api/commits?limit=100"), api<any>("/api/debug/status"), api<any>("/api/users"),
    ]);
    setActors(userData.users);
    setActivePrompt(promptData.active);
    setPromptVersions(promptData.versions);
    setPromptText(promptData.active?.prompt_text || "");
    setModelName(promptData.active?.model_name || "muse-glimmer-30b");
    setTemperature(String(promptData.active?.temperature ?? 0.2));
    setTraces(traceData.traces);
    setCommits(commitData.commits);
    setStatus(statusData);
    if (traceData.traces.length) setSelectedTraceId((current) => current || traceData.traces[0].trace_id);
  }, []);

  useEffect(() => {
    loadAll().catch((err) => setError(err.message || "Could not load debug data."));
  }, [loadAll]);

  useEffect(() => {
    const initialTab = searchParams.get("tab") as Tab | null;
    const initialUser = searchParams.get("user");
    const commitParam = searchParams.get("commit");
    if (initialUser) setHistoricalOwner(initialUser);
    if (initialTab && ["prompts", "traces", "history", "database"].includes(initialTab)) setTab(initialTab);
    if (commitParam) {
      setTab("history");
      inspectCommit(commitParam);
    }
    const messageId = searchParams.get("message_id");
    if (messageId) {
      api<any>(`/api/debug/message/${messageId}`).then((data) => {
        setMessageContext(data);
        if (data.trace?.trace_id) { setSelectedTraceId(data.trace.trace_id); setTab("traces"); }
        else setTab("history");
      }).catch((err) => setError(err.message));
    }
  }, [searchParams]);

  useEffect(() => {
    if (!selectedTraceId) return;
    api<any>(`/api/debug/traces/${selectedTraceId}`).then((data) => setSelectedTrace(data.trace)).catch((err) => setError(err.message));
  }, [selectedTraceId]);

  async function savePrompt() {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api<any>("/api/debug/prompts", {
        method: "POST", body: JSON.stringify({ prompt_text: promptText, model_name: modelName, temperature: Number(temperature), created_by: "ai" }),
      });
      setActivePrompt(result.active); setPromptVersions(result.versions);
      setNotice(`Saved prompt version ${result.active.version_id} · commit ${shortHash(result.commit_hash)}`);
      setTab("prompts");
    } catch (err: any) { setError(err.message); }
    finally { setBusy(false); }
  }

  async function inspectCommit(commitHash: string) {
    setBusy(true); setError(""); setHistoricalWorkspace(null);
    try {
      const result = await api<any>(`/api/commits/${commitHash}/workspace?owner_id=${historicalOwner}`);
      setHistoricalWorkspace({ commit_hash: commitHash, ...result });
    } catch (err: any) { setError(err.message); }
    finally { setBusy(false); }
  }

  async function pushDoltHub() {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api<any>("/api/dolt/push", { method: "POST", body: "{}" });
      setNotice(`Pushed main to ${result.remote}.`);
      const nextStatus = await api<any>("/api/debug/status"); setStatus(nextStatus);
    } catch (err: any) { setError(err.message); }
    finally { setBusy(false); }
  }

  const tabMeta: Record<Tab, { title: string; subtitle: string }> = {
    prompts: { title: "Prompt editor", subtitle: "Edit the active system prompt and save an immutable version." },
    traces: { title: "LLM trace explorer", subtitle: "Inspect exact inputs, outputs, steps, and linked messages." },
    history: { title: "Dolt commit history", subtitle: "Every meaningful conversation or UI change creates a Dolt commit." },
    database: { title: "Runtime configuration", subtitle: "Connection status and optional DoltHub synchronization." },
  };

  useEffect(() => { setHistoricalOwner(viewingUser); }, [viewingUser]);

  return <>
    <TopBar viewer={viewingUser} />
    <main className="debug-shell">
      <div className="page-heading"><div><div className="eyebrow">Developer tools</div><h1>Debug console</h1><p>Prompt versions, model traces, conversation provenance, and Dolt history.</p></div><span className="status-pill"><span className="status-dot"/> {status?.database || "malleable"} · {shortHash(status?.head)}</span></div>
      {error && <div className="error-banner">{error}</div>}
      {notice && !error && <div className="success-banner">{notice}</div>}
      {messageContext && <div className="debug-card" style={{ borderColor: "#9fd8c6", background: "#fbfefd" }}>
        <div className="eyebrow">Opened from conversation</div>
        <h3 style={{ marginTop: 5 }}>{messageContext.message?.actor_name} · {messageContext.message?.message_id}</h3>
        <p style={{ color: "#414958", marginBottom: 8 }}>{messageContext.message?.content}</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <span className="commit-head">Message commit {shortHash(messageContext.commit?.commit_hash)}</span>
          {messageContext.trace?.trace_id && <span className="commit-head">Trace {messageContext.trace.trace_id}</span>}{messageContext.trace?.commit?.commit_hash && <span className="commit-head">Generation commit {shortHash(messageContext.trace.commit.commit_hash)}</span>}
          {!messageContext.trace && <span className="muted" style={{ fontSize: 10 }}>No generation trace is linked to this message.</span>}
        </div>
      </div>}
      <div className="debug-layout">
        <aside className="panel debug-nav">
          {([ ["prompts", "◈", "Prompts"], ["traces", "⌁", "Trace explorer"], ["history", "⑂", "Dolt history"], ["database", "▦", "Runtime & DB"] ] as [Tab, string, string][]).map(([key, icon, label]) =>
            <button key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}><span>{icon}</span>{label}</button>
          )}
          <div style={{ borderTop: "1px solid var(--line)", margin: "10px 5px", paddingTop: 12 }}>
            <div className="eyebrow" style={{ padding: "0 7px 7px" }}>Quick stats</div>
            <div style={{ padding: "5px 7px", fontSize: 10, color: "var(--muted)" }}>{traces.length} traces</div>
            <div style={{ padding: "5px 7px", fontSize: 10, color: "var(--muted)" }}>{commits.length} recent commits</div>
            <div style={{ padding: "5px 7px", fontSize: 10, color: "var(--muted)" }}>{promptVersions.length} prompt versions</div>
          </div>
        </aside>

        <section className="panel debug-content">
          <div className="panel-header"><div><div className="panel-title">{tabMeta[tab].title}</div><div className="panel-subtitle">{tabMeta[tab].subtitle}</div></div>
            <button className="button secondary small" onClick={() => loadAll().catch((err) => setError(err.message))}>↻ Refresh</button>
          </div>
          <div className="panel-body">
            {tab === "prompts" && <>
              <div className="debug-card">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 12 }}><h3 style={{ margin: 0 }}>Active system prompt</h3><span className="status-label">{activePrompt?.version_id || "unsaved"}</span></div>
                <label className="field-label">Prompt text</label>
                <textarea className="field-textarea" value={promptText} onChange={(event) => setPromptText(event.target.value)} />
                <div className="field-row" style={{ marginTop: 12 }}>
                  <div><label className="field-label">Model</label><input className="field" value={modelName} onChange={(event) => setModelName(event.target.value)} /></div>
                  <div><label className="field-label">Temperature</label><input className="field" type="number" min="0" max="2" step="0.1" value={temperature} onChange={(event) => setTemperature(event.target.value)} /></div>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 13, gap: 12 }}><span className="muted" style={{ fontSize: 10 }}>Saving creates a prompt version and a Dolt commit.</span><button className="button" onClick={savePrompt} disabled={busy || promptText.length < 20}>{busy ? "Saving…" : "Save new prompt version ↗"}</button></div>
              </div>
              <div className="debug-card"><h3>Prompt history</h3>
                {promptVersions.map((version) => <div className="history-row" key={version.version_id}>
                  <div className="history-hash">{version.version_id.slice(0, 12)}</div><div><div className="history-message">{version.model_name} · temp {version.temperature}</div><div className="history-date">{formatTime(version.created_at)} · by {version.created_by}</div></div>
                  <div>{version.is_active ? <span className="status-label">Active</span> : <button className="button ghost small" onClick={async () => { try { const data = await api<any>(`/api/debug/prompts/${version.version_id}`); setPromptText(data.prompt.prompt_text); setModelName(data.prompt.model_name); setTemperature(String(data.prompt.temperature)); setNotice(`Loaded ${version.version_id} into the editor. Save to create a new active version.`); setTab("prompts"); } catch (err: any) { setError(err.message); } }}>Load</button>}</div>
                </div>)}
              </div>
            </>}

            {tab === "traces" && <div className="debug-two-col">
              <div className="trace-list">
                {traces.length === 0 && <div className="empty-state">No model traces yet. Send a message in the workspace.</div>}
                {traces.map((trace) => <button key={trace.trace_id} className={`trace-item ${selectedTraceId === trace.trace_id ? "active" : ""}`} onClick={() => setSelectedTraceId(trace.trace_id)}>
                  <div><div className="trace-title">{trace.actor_name} · {trace.trace_id.slice(0, 13)}</div><div className="trace-meta">{formatTime(trace.created_at)} · {trace.model_name} · {trace.duration_ms}ms</div></div><span className={`status-label ${trace.status === "failed" ? "failed" : ""}`}>{trace.status}</span>
                </button>)}
              </div>
              <div>
                {!selectedTrace ? <div className="empty-state">Select a trace to inspect its full execution record.</div> : <>
                  <div className="debug-card"><div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 10 }}><div><h3>{selectedTrace.trace_id}</h3><p>{selectedTrace.provider} · {selectedTrace.model_name} · {selectedTrace.duration_ms}ms</p></div><span className={`status-label ${selectedTrace.status === "failed" ? "failed" : ""}`}>{selectedTrace.status}</span></div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}><Link className="commit-head" href={`/debug?message_id=${selectedTrace.triggering_message_id}&user=${viewingUser}`}>Trigger message ↗</Link>{selectedTrace.response_message_id && <Link className="commit-head" href={`/debug?message_id=${selectedTrace.response_message_id}&user=${viewingUser}`}>AI response ↗</Link>}{selectedTrace.commit?.commit_hash && <Link className="commit-head" href={`/debug?tab=history&commit=${selectedTrace.commit.commit_hash}&user=${viewingUser}`}>Generation commit {shortHash(selectedTrace.commit.commit_hash)} ↗</Link>}</div>
                  </div>
                  <div className="debug-card"><h3>Execution steps</h3>{(selectedTrace.steps || []).map((step: any, index: number) => <div key={index} style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "8px 0", borderBottom: "1px solid var(--line)" }}><span style={{ fontSize: 10 }}>{index + 1}. {step.name}</span><span className="muted" style={{ fontSize: 9 }}>{step.status}</span></div>)}</div>
                  <div className="debug-card"><h3>Input snapshot · exact model context</h3><pre className="code-block">{pretty(selectedTrace.input)}</pre></div>
                  <div className="debug-card"><h3>Model output</h3><pre className="code-block">{pretty(selectedTrace.output)}</pre></div>
                </>}
              </div>
            </div>}

            {tab === "history" && <>
              {messageContext?.commit && <div className="debug-card" style={{ borderColor: "#9fd8c6" }}><h3>Commit containing the selected message</h3><div className="history-row"><div className="history-hash">{shortHash(messageContext.commit.commit_hash)}</div><div><div className="history-message">{messageContext.message?.actor_name}: {messageContext.message?.content}</div><div className="history-date">{messageContext.commit.commit_date}</div></div><span className="status-label">Message</span></div></div>}
              <div className="debug-card"><h3>Commit log · main</h3>
                {commits.map((commit) => <div className="history-row" key={commit.commit_hash}>
                  <div className="history-hash">{shortHash(commit.commit_hash)}</div><div><div className="history-message">{commit.message}</div><div className="history-date">{commit.committer} · {commit.date}</div></div>
                  <button className="button secondary small" onClick={() => inspectCommit(commit.commit_hash)} disabled={busy}>Inspect {historicalOwner} UI</button>
                </div>)}
              </div>
              <div className="debug-card"><div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}><h3 style={{ margin: 0 }}>Historical workspace snapshot</h3><select className="field" style={{ width: 130 }} value={historicalOwner} onChange={(event) => setHistoricalOwner(event.target.value)}><option value="alice">Alice</option><option value="bob">Bob</option><option value="charlie">Charlie</option><option value="dana">Dana</option><option value="ai">AI</option></select></div>
                {historicalWorkspace ? <>
                  <p style={{ margin: "8px 0" }}>Revision <span className="mono">{shortHash(historicalWorkspace.commit_hash)}</span> · {historicalWorkspace.title} · {historicalWorkspace.flat_nodes?.length ?? 0} components</p>
                  <div style={{ display: "flex", gap: 6, margin: "8px 0" }}>
                    <button className={`button small ${historicalRendered ? "" : "secondary"}`} onClick={() => setHistoricalRendered(true)}>Rendered</button>
                    <button className={`button small ${historicalRendered ? "secondary" : ""}`} onClick={() => setHistoricalRendered(false)}>JSON</button>
                  </div>
                  {historicalRendered
                    ? <div className="historical-render"><WorkspaceTree nodes={historicalWorkspace.nodes} emptyText="No components at this revision." /></div>
                    : <pre className="code-block">{pretty(historicalWorkspace.nodes)}</pre>}
                </> : <p style={{ marginTop: 9 }}>Choose a commit above to query a workspace using Dolt's historical AS OF syntax.</p>}
              </div>
            </>}

            {tab === "database" && <>
              <div className="debug-card"><h3>Dolt SQL server</h3><div className="field-row">
                <div><div className="eyebrow">Database</div><div style={{ fontSize: 13, fontWeight: 700, marginTop: 4 }}>{status?.database || "malleable"}</div></div>
                <div><div className="eyebrow">Current HEAD</div><div className="mono" style={{ fontSize: 11, marginTop: 4 }}>{status?.head || "—"}</div></div>
              </div><div className="field-row" style={{ marginTop: 18 }}>
                <div><div className="eyebrow">LLM provider</div><div style={{ fontSize: 12, marginTop: 4 }}>{status?.model_provider || "—"}</div></div>
                <div><div className="eyebrow">Model</div><div style={{ fontSize: 12, marginTop: 4 }}>{status?.model_name || "—"}</div></div>
              </div></div>
              <div className="debug-card"><h3>DoltHub remote</h3><p>Configure <span className="mono">DOLTHUB_REMOTE=organization/repository</span> in the root <span className="mono">.env</span> and authenticate the Dolt CLI inside the Dolt container. The push action sends the current <span className="mono">main</span> branch to the configured remote.</p>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginTop: 14 }}><div><span className={`status-label ${status?.dolthub_remote_configured ? "" : "failed"}`}>{status?.dolthub_remote_configured ? `Configured: ${status.dolthub_remote}` : "Remote not configured"}</span></div><button className="button" onClick={pushDoltHub} disabled={busy || !status?.dolthub_remote_configured}>{busy ? "Pushing…" : "Push main to DoltHub ↗"}</button></div>
              </div>
              <div className="debug-card"><h3>Runtime safety notes</h3><p>This demo uses a single Dolt main-branch working set and serializes writes in the Python process. It is intended for a single-machine prototype, not multi-instance production use. Do not expose the demo root SQL credentials or store provider API keys in Dolt.</p></div>
            </>}
          </div>
        </section>
      </div>
    </main>
  </>;
}
