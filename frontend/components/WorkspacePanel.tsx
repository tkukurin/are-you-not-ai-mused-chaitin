"use client";

import { useMemo, type FormEvent } from "react";
import { shortHash } from "@/lib/api";
import type { Patch, UINode, Workspace } from "@/lib/types";
import { WorkspaceTree } from "./NodeView";

const BUILDERS: { type: string; icon: string; label: string }[] = [
  { type: "Avatar", icon: "🧬", label: "Avatar" },
  { type: "Note", icon: "✎", label: "Note" },
  { type: "Checklist", icon: "☑", label: "Checklist" },
  { type: "Comparison", icon: "⚖", label: "Comparison" },
  { type: "Map", icon: "⌖", label: "Map" },
  { type: "Chart", icon: "▂▅▇", label: "Chart" },
  { type: "Metric", icon: "#", label: "Metric" },
];

function defaultNode(type: string, id: string): UINode {
  const base: Record<string, any> = {
    Avatar: {
      title: "Custom Avatar Draft",
      subtitle: "Personal mascot take from the group chat",
      archetype: "street-creator",
      palette: { primary: "#2f6fd1", secondary: "#ffb703", accent: "#00a884", bg: "#eef5ff" },
      traits: {
        headgear: "Festoon-Light Crown",
        eyes: "Starry Visor",
        outfit: "Utility Cloak",
        held_item: "Glowing Megaphone",
        companion: "Lantern Sprite",
        aura: "Twilight String-Light Halo",
      },
      stats: [{ label: "Vibe", value: 90 }, { label: "Resilience", value: 82 }],
    },
    Note: { title: "New thought", body: "Edit me, or ask the AI in chat to reshape this." },
    Checklist: { title: "Next steps", items: ["Add a step from the chat"] },
    Comparison: { title: "Options to compare", columns: ["Option", "Cost", "Notes"], rows: [["Option A", "TBD", "From chat"], ["Option B", "TBD", "From chat"]] },
    Map: { title: "Places to explore", description: "Places from the conversation.", locations: ["Add a location"] },
    Chart: { title: "Chart", description: "Illustrative data.", values: [30, 55, 42, 70, 60, 85] },
    Metric: { label: "Metric", value: "0", delta: "from the conversation" },
  };
  return { id, type, props: base[type] || { title: type }, layout: { span: 2 }, order_index: 100, children: [] };
}

export function makeNode(type: string): UINode {
  const slug = type.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  return defaultNode(type, `${slug}_${Date.now().toString(36)}`);
}

export default function WorkspacePanel({
  workspace, ownerName, isAI, isHistorical, revision,
  draftPatches, previewNodes, previewCount,
  onAddDraft, onDiscardDraft, onPreviewDraft, onCommitDraft,
  selectedNode, onSelectNode, onDeleteSelected, loading,
}: {
  workspace: Workspace | null;
  ownerName: string;
  isAI: boolean;
  isHistorical: boolean;
  revision: string | null;
  draftPatches: Patch[];
  previewNodes: UINode[] | null;
  previewCount: number | null;
  onAddDraft: (type: string) => void;
  onDiscardDraft: () => void;
  onPreviewDraft: () => void;
  onCommitDraft: (e?: FormEvent) => void;
  selectedNode: UINode | null;
  onSelectNode: (node: UINode | null) => void;
  onDeleteSelected: () => void;
  loading: boolean;
}) {
  const draftIds = new Set(draftPatches.filter((p) => p.op === "insert" && p.node).map((p) => p.node!.id));
  const displayNodes = useMemo(() => {
    if (previewNodes) return previewNodes;
    const base = workspace?.nodes || [];
    if (!draftPatches.length) return base;
    const removedIds = new Set(draftPatches.filter((p) => p.op === "remove" && p.id).map((p) => p.id!));
    const insertsByParent = new Map<string | null, UINode[]>();
    for (const p of draftPatches) {
      if (p.op === "insert" && p.node && !removedIds.has(p.node.id)) {
        const key = p.parent_id ?? null;
        insertsByParent.set(key, [...(insertsByParent.get(key) || []), p.node]);
      }
    }
    const applyTree = (nodes: UINode[]): UINode[] =>
      nodes
        .filter((n) => !removedIds.has(n.id))
        .map((n) => ({
          ...n,
          children: [...applyTree(n.children || []), ...(insertsByParent.get(n.id) || [])],
        }));
    return [...applyTree(base), ...(insertsByParent.get(null) || [])];
  }, [previewNodes, workspace?.nodes, draftPatches]);

  return (
    <section className={`panel workspace-panel ${isHistorical ? "is-historical" : ""}`} data-tour="workspace">
      <div className="panel-header">
        <div>
          <div className="panel-title">{ownerName}'s workspace</div>
          <div className="panel-subtitle">
            {isHistorical
              ? <>Historical snapshot · AS OF <span className="mono">{shortHash(revision)}</span></>
              : "Personal interface · built jointly from the shared conversation"}
          </div>
        </div>
        <div className="workspace-head-actions">
          {isHistorical
            ? <span className="status-pill historical"><span className="status-dot" /> viewing history</span>
            : <span className="status-pill"><span className="status-dot" /> live · HEAD {shortHash(workspace?.head)}</span>}
        </div>
      </div>

      <div className="workspace-body">
        {isHistorical && (
          <div className="historical-banner">
            ⑂ You're viewing {ownerName}'s workspace as of commit <span className="mono">{shortHash(revision)}</span>.
            Pick <strong>HEAD</strong> in History to return to the live view. Editing is disabled in the past.
          </div>
        )}
        <div className="workspace-title-row">
          <div>
            <h2>{workspace?.title || "Loading workspace…"}</h2>
            <p>{isAI
              ? "The AI participant's own workspace — it builds one for itself too. Read-only while viewing as AI."
              : `This is ${ownerName}'s personal view. Ask in chat and AI reshapes it; or build by hand below. Every change is a Dolt commit.`}</p>
          </div>
          <span className="commit-head">{workspace ? `${workspace.flat_nodes.filter((n) => n.parent_id !== null).length} components` : "…"}</span>
        </div>

        {!workspace
          ? <div className="empty-state">Connecting to the versioned workspace…</div>
          : <WorkspaceTree
              nodes={displayNodes}
              onSelect={isHistorical ? undefined : onSelectNode}
              selectedId={selectedNode?.id}
              onAdd={isHistorical || isAI ? undefined : onAddDraft}
              draftIds={draftIds}
              emptyText="No components yet. Ask the AI in chat, or add one below."
            />}

        {!isHistorical && !isAI && (
          <>
            <div className="builder-bar">
              <span className="builder-label">Build it yourself:</span>
              {BUILDERS.map((b) => (
                <button key={b.type} className="builder-chip" onClick={() => onAddDraft(b.type)}>
                  <span>{b.icon}</span> {b.label}
                </button>
              ))}
            </div>
            {selectedNode && (
              <div className="selection-bar">
                <span>Selected: <strong>{selectedNode.type}</strong> <span className="mono muted">{selectedNode.id}</span></span>
                <span className="selection-actions">
                  <button className="button ghost small" onClick={() => onSelectNode(null)}>Clear</button>
                  <button className="button danger small" onClick={onDeleteSelected}>Remove component</button>
                </span>
              </div>
            )}
            {draftPatches.length > 0 && (
              <div className="draft-strip">
                <div>
                  <strong>{draftPatches.length} uncommitted change{draftPatches.length > 1 ? "s" : ""}</strong>
                  <p>
                    {draftPatches.map((p) => `${p.op}: ${p.node?.type || p.id || "component"}`).join(" · ")}
                    {previewCount === null ? " · preview to validate against the real tree." : ` · preview valid · ${previewCount} components result.`}
                  </p>
                </div>
                <div className="draft-actions">
                  <button className="button secondary small" onClick={onPreviewDraft}>Preview</button>
                  <button className="button small" onClick={() => onCommitDraft()} disabled={loading}>Commit to Dolt ↗</button>
                  <button className="button ghost small" onClick={onDiscardDraft}>Discard</button>
                </div>
              </div>
            )}
          </>
        )}
        {!isHistorical && isAI && (
          <p className="muted" style={{ fontSize: 11, marginTop: 14 }}>
            Switch “Viewing” to Alice, Bob, or Charlie to edit a workspace or send chat messages.
          </p>
        )}
      </div>
    </section>
  );
}
