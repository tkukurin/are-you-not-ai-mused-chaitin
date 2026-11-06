"use client";

import Link from "next/link";
import { fullTime, shortHash, timeAgo } from "@/lib/api";
import type { Commit } from "@/lib/types";

const MAX_SHOWN = 15;

function classify(message: string): { icon: string; label: string } {
  const m = message.toLowerCase();
  if (m.includes("ai reply")) return { icon: "✦", label: "AI turn" };
  if (m.startsWith("chat:")) return { icon: "💬", label: "Message" };
  if (m.startsWith("ui:")) return { icon: "▦", label: "UI commit" };
  if (m.includes("prompt")) return { icon: "◈", label: "Prompt" };
  if (m.includes("bootstrap") || m.includes("initialize")) return { icon: "⑂", label: "Bootstrap" };
  return { icon: "·", label: "Commit" };
}

export default function CommitRail({
  commits, head, selectedRevision, onSelectRevision, viewer,
}: {
  commits: Commit[];
  head?: string | null;
  selectedRevision: string | null;
  onSelectRevision: (hash: string | null) => void;
  viewer: string;
}) {
  const shown = commits.slice(0, MAX_SHOWN);
  const selectedCommit = selectedRevision
    ? commits.find((c) => c.commit_hash === selectedRevision)
    : undefined;
  const pinned = selectedCommit && !shown.some((c) => c.commit_hash === selectedCommit.commit_hash)
    ? selectedCommit
    : undefined;
  const rows = pinned ? [pinned, ...shown] : shown;

  function renderRow(commit: Commit, isPinned = false) {
    const kind = classify(commit.message);
    const active = selectedRevision === commit.commit_hash;
    return (
      <button
        key={commit.commit_hash}
        className={`commit-row ${active ? "active" : ""}`}
        onClick={() => onSelectRevision(active ? null : commit.commit_hash)}
        title={commit.message}
      >
        <span className="commit-dot" />
        <span className="commit-row-main">
          <span className="commit-row-title">
            <span className="commit-kind">{kind.icon}</span>
            <span className="commit-msg">{commit.message}</span>
          </span>
          <span className="commit-row-sub">
            <span className="mono">{shortHash(commit.commit_hash)}</span> · {commit.committer || "—"} · <span title={fullTime(commit.date)}>{timeAgo(commit.date)}</span>
            {isPinned ? " · selected" : ""}
          </span>
        </span>
      </button>
    );
  }

  return (
    <div className="commit-rail" data-tour="history">
      <div className="commit-rail-head">
        <div>
          <div className="panel-title">History</div>
          <div className="panel-subtitle">Dolt commits · click to time-travel</div>
        </div>
        {selectedRevision && (
          <button className="button ghost small" onClick={() => onSelectRevision(null)}>← Back to HEAD</button>
        )}
      </div>
      <div className="commit-rail-list">
        <button
          className={`commit-row ${!selectedRevision ? "active" : ""}`}
          onClick={() => onSelectRevision(null)}
        >
          <span className="commit-dot head" />
          <span className="commit-row-main">
            <span className="commit-row-title">HEAD <span className="mono muted">{shortHash(head)}</span></span>
            <span className="commit-row-sub">current working view</span>
          </span>
          <span className="commit-row-badge live">live</span>
        </button>
        {rows.map((commit) => renderRow(commit, commit.commit_hash === pinned?.commit_hash))}
        {!commits.length && <div className="empty-state" style={{ padding: 16 }}>No commits yet.</div>}
      </div>
      <div className="commit-rail-foot">
        <span className="muted" style={{ fontSize: 10 }}>Last {Math.min(commits.length, MAX_SHOWN)} ·</span>
        <Link className="tiny-link" href={`/debug?tab=history&user=${viewer}`}>full history in Debug ↗</Link>
      </div>
    </div>
  );
}
