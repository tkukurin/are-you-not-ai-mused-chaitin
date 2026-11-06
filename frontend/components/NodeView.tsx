"use client";

import { useMemo, useState } from "react";
import type { UINode } from "@/lib/types";

const TYPE_ICON: Record<string, string> = {
  Grid: "▦", Section: "▤", Heading: "H", Note: "✎", Metric: "#", Chart: "▂▅▇",
  Table: "☰", Map: "⌖", Comparison: "⚖", Checklist: "☑", Avatar: "🧬",
};

function normalizeChartPoints(props: Record<string, any>): { label: string; value: number }[] {
  if (Array.isArray(props.data) && props.data.length) {
    return props.data.map((item: any, idx: number) => {
      if (typeof item === "number") return { label: String(idx + 1), value: item };
      if (item && typeof item === "object") {
        const xKey = props.xKey && item[props.xKey] !== undefined ? props.xKey : Object.keys(item).find((k) => typeof item[k] === "string");
        const yKey = props.yKey && typeof item[props.yKey] === "number" ? props.yKey : Object.keys(item).find((k) => typeof item[k] === "number");
        const label = xKey ? String(item[xKey]) : String(item.label ?? item.hour ?? item.time ?? item.name ?? idx + 1);
        const rawVal = yKey ? Number(item[yKey]) : Number(item.value ?? item.attendees ?? item.count ?? item.y ?? 0);
        return { label, value: Number.isFinite(rawVal) ? rawVal : 0 };
      }
      return { label: String(idx + 1), value: 0 };
    });
  }
  const values: number[] = Array.isArray(props.values) ? props.values.map((v: any) => Number(v) || 0) : [28, 52, 95, 140, 110, 60];
  const labels: string[] = Array.isArray(props.labels) ? props.labels.map(String) : values.map((_, i) => `#${i + 1}`);
  return values.map((v, i) => ({ label: labels[i] ?? `#${i + 1}`, value: v }));
}

function normalizeTable(props: Record<string, any>): { columns: string[]; rows: string[][] } {
  // Handle the model's { criteria: [...], options: [{ name: "...", ... }] } shape
  if (Array.isArray(props.options) && props.options.length) {
    const firstOpt = props.options[0];
    if (firstOpt && typeof firstOpt === "object" && !Array.isArray(firstOpt)) {
      const nameKey = Object.keys(firstOpt).find((k) => ["name", "option", "venue", "title", "label"].includes(k.toLowerCase())) || Object.keys(firstOpt)[0];
      const otherKeys = Object.keys(firstOpt).filter((k) => k !== nameKey);
      const criteriaLabels: string[] = Array.isArray(props.criteria) && props.criteria.length === otherKeys.length
        ? props.criteria.map(String)
        : otherKeys.map((k) => k.replace(/_/g, " "));
      const columns = ["Option", ...criteriaLabels];
      const rows = props.options.map((opt: any) => [
        String(opt?.[nameKey] ?? "—"),
        ...otherKeys.map((k) => String(opt?.[k] ?? "—")),
      ]);
      return { columns, rows };
    }
  }
  const rawCols: string[] = Array.isArray(props.columns) && props.columns.length
    ? props.columns.map(String)
    : ["Option", "Details", "Status"];
  if (Array.isArray(props.rows) && props.rows.length) {
    const rows = props.rows.map((row: any) => {
      if (Array.isArray(row)) return row.map((c) => String(c ?? "—"));
      if (row && typeof row === "object") {
        return rawCols.map((col) => {
          const matchKey = Object.keys(row).find((k) => k.toLowerCase() === col.toLowerCase() || k.toLowerCase().replace(/_/g, " ") === col.toLowerCase());
          return String(matchKey ? row[matchKey] : Object.values(row)[0] ?? "—");
        });
      }
      return [String(row)];
    });
    return { columns: rawCols, rows };
  }
  if (Array.isArray(props.items) && props.items.length) {
    return {
      columns: rawCols,
      rows: props.items.map((item: any) => [typeof item === "string" ? item : String(item?.label ?? item?.name ?? "Item"), "—", "Suggested"]),
    };
  }
  return { columns: rawCols, rows: [["Option A", "Add details", "Open"], ["Option B", "Add details", "Open"]] };
}

function normalizeMarkers(props: Record<string, any>): { label: string; location: string; note?: string }[] {
  const raw = props.markers || props.pins || props.zones || props.locations || props.points || [];
  if (!Array.isArray(raw)) return [];
  return raw.map((item: any, idx: number) => {
    if (typeof item === "string") {
      const [label, ...rest] = item.split(/\s*[—–:-]\s*/);
      return { label: label || `Point ${idx + 1}`, location: rest.join(" — ") || "On site" };
    }
    if (item && typeof item === "object") {
      return {
        label: String(item.label ?? item.name ?? item.title ?? `Point ${idx + 1}`),
        location: String(item.location ?? item.place ?? item.area ?? item.zone ?? item.address ?? "Street zone"),
        note: item.note || item.description || item.details ? String(item.note ?? item.description ?? item.details) : undefined,
      };
    }
    return { label: `Point ${idx + 1}`, location: String(item) };
  });
}

/** Renders one workspace component. onAdd (optional) turns buildable cards
 *  into draft-insert buttons — the manual half of "jointly creating UI". */
export default function NodeView({
  node, onSelect, selectedId, onAdd, draftIds, addedIds,
}: {
  node: UINode;
  onSelect?: (node: UINode) => void;
  selectedId?: string | null;
  onAdd?: (type: string) => void;
  draftIds?: Set<string>;
  addedIds?: Set<string>;
}) {
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const spanClass = node.layout?.span === 2 ? "ui-card span-2" : "ui-card";
  const children = node.children || [];
  const isDraft = draftIds?.has(node.id);
  const isAdded = addedIds?.has(node.id);
  const selectable = !!onSelect;
  const cls = `${spanClass}${isDraft ? " is-draft" : ""}${isAdded ? " is-added" : ""}${selectedId === node.id ? " is-selected" : ""}`;

  function shell(kicker: string, body: React.ReactNode) {
    return (
      <section
        className={cls}
        data-node-id={node.id}
        data-just-added={isAdded && !isDraft ? "true" : undefined}
        onClick={selectable ? (e) => { e.stopPropagation(); onSelect!(node); } : undefined}
        title={selectable ? "Select component" : undefined}
        style={selectable ? { cursor: "pointer" } : undefined}
      >
        <div className="ui-kicker-row">
          <span className="ui-kicker">{TYPE_ICON[node.type] ? `${TYPE_ICON[node.type]} ` : ""}{kicker}</span>
          {isDraft && <span className="ui-badge draft">draft</span>}
          {isAdded && !isDraft && <span className="ui-badge added">✦ grown this beat</span>}
        </div>
        {body}
        {onAdd && !isDraft && ["Avatar", "Note", "Map", "Comparison", "Checklist", "Chart", "Metric"].includes(node.type) && (
          <button className="ui-card-clone" onClick={(e) => { e.stopPropagation(); onAdd(node.type); }} title={`Add another ${node.type} below`}>
            + Add {node.type}
          </button>
        )}
      </section>
    );
  }

  if (node.type === "Grid") {
    const columns = Math.max(1, Math.min(4, Number(node.props.columns || 2)));
    return (
      <div className="workspace-grid" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: node.props.gap || "11px" }}>
        {children.map((child) => (
          <NodeView key={child.id} node={child} onSelect={onSelect} selectedId={selectedId} onAdd={onAdd} draftIds={draftIds} addedIds={addedIds} />
        ))}
      </div>
    );
  }

  if (node.type === "Avatar") {
    const palette = node.props.palette || {};
    const primary = palette.primary || "#2f6fd1";
    const secondary = palette.secondary || "#ffb703";
    const accent = palette.accent || "#00a884";
    const bg = palette.bg || "#f0f7f4";
    const rawTraits = node.props.traits && typeof node.props.traits === "object" ? node.props.traits : {};
    const slotOrder: [string, string][] = [
      ["headgear", "Headgear"],
      ["eyes", "Eyes / Visor"],
      ["outfit", "Outfit / Cloak"],
      ["held_item", "Held Item"],
      ["companion", "Companion"],
      ["aura", "Aura"],
    ];
    const headStr = String(rawTraits.headgear || "").toLowerCase();
    const eyeStr = String(rawTraits.eyes || "").toLowerCase();
    const auraStr = String(rawTraits.aura || "").toLowerCase();
    const hasCrown = headStr.includes("crown") || headStr.includes("festoon") || headStr.includes("lantern") || headStr.includes("halo");
    const hasHeadphones = headStr.includes("headphone") || headStr.includes("600w") || headStr.includes("audio") || headStr.includes("sound") || headStr.includes("ear");
    const hasVisor = eyeStr.includes("visor") || eyeStr.includes("radar") || eyeStr.includes("tactical") || eyeStr.includes("goggle");
    const hasStars = eyeStr.includes("star") || eyeStr.includes("spark") || eyeStr.includes("glow");
    const hasSoundAura = auraStr.includes("sound") || auraStr.includes("bass") || auraStr.includes("82db") || auraStr.includes("wave") || auraStr.includes("all");
    const hasLightAura = auraStr.includes("light") || auraStr.includes("festoon") || auraStr.includes("halo") || auraStr.includes("twilight") || auraStr.includes("all");
    const hasRainAura = auraStr.includes("rain") || auraStr.includes("storm") || auraStr.includes("drizzle") || auraStr.includes("weather");
    const stats: { label: string; value: number }[] = Array.isArray(node.props.stats)
      ? node.props.stats.map((s: any) => ({ label: String(s?.label ?? s?.name ?? "Stat"), value: Math.max(0, Math.min(100, Number(s?.value ?? 80))) }))
      : [];

    const parseTrait = (val: any): { text: string; credit: string | null } => {
      const s = String(val || "—");
      const m = s.match(/^(.*?)\s*\(([^)]+)\)\s*$/);
      return m ? { text: m[1].trim(), credit: m[2].trim() } : { text: s, credit: null };
    };

    return shell(node.props.archetype ? `Avatar · ${node.props.archetype}` : "Avatar Studio", <>
      <div className="avatar-card-header">
        <div>
          <h3>{node.props.title || "Avatar Draft"}</h3>
          {node.props.subtitle && <p className="ui-subnote">{node.props.subtitle}</p>}
        </div>
        <div className="avatar-swatches" title="Character color palette">
          {[primary, secondary, accent, bg].map((c, i) => (
            <span key={i} className="avatar-swatch" style={{ background: c }} />
          ))}
        </div>
      </div>

      <div className="avatar-studio-grid">
        <div className="avatar-stage" style={{ background: `radial-gradient(circle at 50% 42%, #ffffff 0%, ${bg} 78%)` }}>
          <svg viewBox="0 0 200 200" className="avatar-svg" aria-label={node.props.title || "Avatar"}>
            <defs>
              <linearGradient id={`bodyGrad-${node.id}`} x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stopColor={primary} />
                <stop offset="100%" stopColor={accent} />
              </linearGradient>
            </defs>

            {/* Aura Layer: Soundwave rings */}
            {hasSoundAura && (
              <g opacity="0.32">
                <circle cx="100" cy="96" r="78" fill="none" stroke={accent} strokeWidth="1.8" strokeDasharray="6 4" />
                <circle cx="100" cy="96" r="66" fill="none" stroke={primary} strokeWidth="1.5" />
              </g>
            )}
            {/* Aura Layer: Rain streaks */}
            {hasRainAura && (
              <g stroke={primary} strokeWidth="1.6" opacity="0.35" strokeLinecap="round">
                <line x1="34" y1="24" x2="26" y2="42" />
                <line x1="168" y1="28" x2="160" y2="46" />
                <line x1="42" y1="68" x2="34" y2="86" />
                <line x1="162" y1="72" x2="154" y2="90" />
              </g>
            )}
            {/* Aura Layer: Twilight Festoon String Lights */}
            {hasLightAura && (
              <g>
                <path d="M 24 38 Q 100 10 176 38" fill="none" stroke={secondary} strokeWidth="1.8" opacity="0.75" />
                {[42, 70, 100, 130, 158].map((cx, i) => (
                  <circle key={i} cx={cx} cy={i === 2 ? 25 : i % 2 === 1 ? 28 : 33} r="4.5" fill={secondary} stroke="#fff" strokeWidth="1.2" />
                ))}
              </g>
            )}

            {/* Back Cape / Storm Cloak */}
            <path d="M 52 124 Q 100 106 148 124 L 162 186 L 38 186 Z" fill={secondary} opacity="0.88" />
            {/* Torso / Outfit */}
            <path d="M 58 126 Q 100 114 142 126 L 150 186 L 50 186 Z" fill={`url(#bodyGrad-${node.id})`} stroke="#111b21" strokeWidth="2.2" />
            {/* Chest Emblem / Acoustic Patchbay */}
            <rect x="82" y="142" width="36" height="24" rx="4" fill="#ffffff" opacity="0.9" stroke="#111b21" strokeWidth="1.6" />
            <circle cx="92" cy="154" r="4" fill={primary} />
            <circle cx="108" cy="154" r="4" fill={secondary} />

            {/* Studio Headphones Band (if equipped) */}
            {hasHeadphones && (
              <g>
                <path d="M 58 88 A 42 42 0 0 1 142 88" fill="none" stroke="#111b21" strokeWidth="5" />
                <rect x="48" y="78" width="13" height="26" rx="6" fill={accent} stroke="#111b21" strokeWidth="2" />
                <rect x="139" y="78" width="13" height="26" rx="6" fill={accent} stroke="#111b21" strokeWidth="2" />
              </g>
            )}

            {/* Head */}
            <circle cx="100" cy="88" r="34" fill="#fff7ec" stroke="#111b21" strokeWidth="2.4" />
            {/* Cheek blush */}
            <ellipse cx="78" cy="96" rx="5" ry="2.8" fill="#ff8fa3" opacity="0.55" />
            <ellipse cx="122" cy="96" rx="5" ry="2.8" fill="#ff8fa3" opacity="0.55" />

            {/* Eyes or Tactical Visor */}
            {hasVisor ? (
              <g>
                <rect x="70" y="76" width="60" height="16" rx="8" fill="#0b241e" stroke={accent} strokeWidth="2.2" />
                <line x1="78" y1="84" x2="96" y2="84" stroke="#25d366" strokeWidth="2.4" strokeLinecap="round" />
                <line x1="104" y1="84" x2="122" y2="84" stroke={secondary} strokeWidth="2.4" strokeLinecap="round" />
              </g>
            ) : hasStars ? (
              <g fill={primary}>
                <polygon points="84,76 86,82 92,84 86,86 84,92 82,86 76,84 82,82" />
                <polygon points="116,76 118,82 124,84 118,86 116,92 114,86 108,84 114,82" />
              </g>
            ) : (
              <g>
                <circle cx="85" cy="85" r="4.5" fill="#111b21" />
                <circle cx="115" cy="85" r="4.5" fill="#111b21" />
                <circle cx="86.5" cy="83.5" r="1.5" fill="#ffffff" />
                <circle cx="116.5" cy="83.5" r="1.5" fill="#ffffff" />
              </g>
            )}

            {/* Confident smile */}
            <path d="M 91 102 Q 100 109 109 102" fill="none" stroke="#111b21" strokeWidth="2.2" strokeLinecap="round" />

            {/* Headgear: Crown / Hood / Beret */}
            {hasCrown ? (
              <polygon
                points="72,60 82,40 94,53 100,36 106,53 118,40 128,60"
                fill={secondary}
                stroke="#111b21"
                strokeWidth="2"
                strokeLinejoin="round"
              />
            ) : (
              <path d="M 68 68 Q 100 42 132 68 Z" fill={primary} stroke="#111b21" strokeWidth="2" />
            )}
          </svg>

          <div className="avatar-props-Overlay">
            {rawTraits.held_item && (
              <span className="avatar-prop-pill left" title={`Held: ${rawTraits.held_item}`}>
                ⚡ {parseTrait(rawTraits.held_item).text.slice(0, 24)}
              </span>
            )}
            {rawTraits.companion && (
              <span className="avatar-prop-pill right" title={`Companion: ${rawTraits.companion}`}>
                ✦ {parseTrait(rawTraits.companion).text.slice(0, 24)}
              </span>
            )}
          </div>
        </div>

        <div className="avatar-genome">
          <div className="avatar-slots-grid">
            {slotOrder.map(([key, label]) => {
              const parsed = parseTrait(rawTraits[key]);
              return (
                <div key={key} className="avatar-slot-card">
                  <div className="avatar-slot-top">
                    <span className="avatar-slot-name">{label}</span>
                    {parsed.credit && <span className="avatar-slot-credit">{parsed.credit}</span>}
                  </div>
                  <div className="avatar-slot-val">{parsed.text}</div>
                </div>
              );
            })}
          </div>

          {stats.length > 0 && (
            <div className="avatar-stats">
              {stats.map((st, i) => (
                <div key={i} className="avatar-stat-row">
                  <span className="avatar-stat-lbl">{st.label}</span>
                  <div className="avatar-stat-track">
                    <div className="avatar-stat-fill" style={{ width: `${st.value}%`, background: primary }} />
                  </div>
                  <span className="avatar-stat-num">{st.value}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>);
  }
  if (node.type === "Metric") {
    const label = node.props.label || node.props.title || "Metric";
    const delta = node.props.delta || node.props.subtitle || node.props.note || "";
    return shell(label, <>
      <div className="metric-value">{node.props.value ?? "—"}</div>
      {delta && <div className="metric-delta">{delta}</div>}
    </>);
  }
  if (node.type === "Note") {
    const body = node.props.body || node.props.content || node.props.text || "";
    const bullets: string[] = Array.isArray(node.props.bullets) ? node.props.bullets : (Array.isArray(node.props.items) ? node.props.items : []);
    return shell("Note", <>
      <h3>{node.props.title || "Note"}</h3>
      {body && <p>{body}</p>}
      {bullets.length > 0 && (
        <ul className="note-bullets">
          {bullets.map((b, i) => <li key={i}>{String(b)}</li>)}
        </ul>
      )}
    </>);
  }
  if (node.type === "Heading") {
    return shell("Heading", <>
      <h3 className="ui-heading">{node.props.text || node.props.title || "Heading"}</h3>
      {node.props.subtitle && <p>{node.props.subtitle}</p>}
    </>);
  }
  if (node.type === "Chart") {
    const points = normalizeChartPoints(node.props);
    const max = Math.max(...points.map((p) => p.value), 1);
    const subtitle = node.props.subtitle || node.props.description;
    return shell(node.props.title || "Chart", <>
      {subtitle && <p className="ui-subnote">{subtitle}</p>}
      <div className="rich-chart">
        {points.map((pt, i) => {
          const isPeak = pt.value === max && max > 0;
          const pct = Math.max(12, Math.round((pt.value / max) * 100));
          return (
            <div key={i} className={`chart-col ${isPeak ? "is-peak" : ""}`}>
              <span className="chart-val">{pt.value}</span>
              <div className="chart-bar-wrap">
                <div className="chart-bar" style={{ height: `${pct}%` }} />
              </div>
              <span className="chart-lbl">{pt.label}</span>
            </div>
          );
        })}
      </div>
      {node.props.note && <p className="ui-footnote">{node.props.note}</p>}
    </>);
  }
  if (node.type === "Map") {
    const markers = normalizeMarkers(node.props);
    const caption = node.props.caption || node.props.description || node.props.subtitle;
    const pinPositions = [
      { left: "22%", top: "28%" },
      { left: "68%", top: "34%" },
      { left: "46%", top: "66%" },
      { left: "28%", top: "72%" },
      { left: "78%", top: "68%" },
      { left: "52%", top: "24%" },
    ];
    return shell(node.props.title || "Map", <>
      {caption && <p className="ui-subnote">{caption}</p>}
      <div className="map-canvas">
        <div className="map-road-h" />
        <div className="map-road-v" />
        {markers.map((m, i) => {
          const pos = pinPositions[i % pinPositions.length];
          return (
            <div key={i} className="map-pin-chip" style={{ left: pos.left, top: pos.top }} title={`${m.label}: ${m.location}`}>
              <span className="map-pin-num">{i + 1}</span>
              <span className="map-pin-text">{m.label}</span>
            </div>
          );
        })}
        {!markers.length && <span className="muted">⌖ Street layout schematic</span>}
      </div>
      {markers.length > 0 && (
        <div className="map-legend">
          {markers.map((m, i) => (
            <div key={i} className="map-legend-item">
              <span className="map-pin-num">{i + 1}</span>
              <div>
                <strong>{m.label}</strong> · <span>{m.location}</span>
                {m.note && <div className="map-legend-note">{m.note}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </>);
  }
  if (node.type === "Comparison" || node.type === "Table") {
    const { columns, rows } = normalizeTable(node.props);
    const subtitle = node.props.subtitle || node.props.description;
    return shell(node.type === "Table" ? "Table" : "Comparison", <>
      <h3>{node.props.title || (node.type === "Table" ? "Table" : "Comparison")}</h3>
      {subtitle && <p className="ui-subnote">{subtitle}</p>}
      <table className="comparison-table">
        <thead><tr>{columns.map((col, i) => <th key={i}>{col}</th>)}</tr></thead>
        <tbody>{rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{j === 0 ? <strong>{String(cell)}</strong> : String(cell)}</td>)}</tr>)}</tbody>
      </table>
      {node.props.note && <p className="ui-footnote">{node.props.note}</p>}
    </>);
  }
  if (node.type === "Checklist") {
    const rawItems: any[] = Array.isArray(node.props.items) ? node.props.items : [];
    const items = rawItems.map((it) => {
      if (typeof it === "string") return { text: it, initialDone: false };
      if (it && typeof it === "object") {
        return {
          text: String(it.text ?? it.label ?? it.title ?? it.item ?? "Step"),
          initialDone: Boolean(it.done ?? it.checked ?? it.completed),
        };
      }
      return { text: String(it), initialDone: false };
    });
    const doneCount = items.filter((it, i) => checked[`${node.id}-${i}`] ?? it.initialDone).length;
    return shell("Checklist", <>
      <div className="checklist-head">
        <h3>{node.props.title || "Next steps"}</h3>
        {items.length > 0 && <span className="checklist-count">{doneCount}/{items.length}</span>}
      </div>
      <ul className="check-list">{items.map((item, i) => {
        const key = `${node.id}-${i}`;
        const done = checked[key] ?? item.initialDone;
        return <li key={i}>
          <input type="checkbox" checked={done} onChange={(e) => setChecked((o) => ({ ...o, [key]: e.target.checked }))} />
          <span style={{ textDecoration: done ? "line-through" : "none", opacity: done ? .55 : 1 }}>{item.text}</span>
        </li>;
      })}</ul>
    </>);
  }
  if (node.type === "Section") {
    return shell(node.props.title || "Section", <>
      <div className="ui-section-children">
        {children.map((child) => (
          <NodeView key={child.id} node={child} onSelect={onSelect} selectedId={selectedId} onAdd={onAdd} draftIds={draftIds} addedIds={addedIds} />
        ))}
      </div>
    </>);
  }
  return shell(node.type, <><h3>{node.props.title || node.type}</h3><p>{node.props.body || "Custom component."}</p></>);
}

export function WorkspaceTree({
  nodes, onSelect, selectedId, onAdd, draftIds, addedIds, emptyText,
}: {
  nodes: UINode[];
  onSelect?: (node: UINode) => void;
  selectedId?: string | null;
  onAdd?: (type: string) => void;
  draftIds?: Set<string>;
  addedIds?: Set<string>;
  emptyText?: string;
}) {
  const roots = useMemo(() => nodes || [], [nodes]);
  if (!roots.length) return <div className="empty-state seed-canvas">{emptyText || "This workspace has no components yet."}</div>;
  const root = roots.find((n) => n.type === "Grid") || roots[0];
  const rest = roots.filter((n) => n.id !== root.id);
  if (root.type === "Grid" && (!root.children || root.children.length === 0) && rest.length === 0) {
    return (
      <div className="seed-canvas">
        <div className="seed-canvas-icon">✦</div>
        <div className="seed-canvas-title">Seed workspace — ready to grow</div>
        <p>{emptyText || "As the group shares ideas in chat, this workspace grows into a personal app shaped for how its owner works."}</p>
      </div>
    );
  }
  return <>
    <NodeView node={root} onSelect={onSelect} selectedId={selectedId} onAdd={onAdd} draftIds={draftIds} addedIds={addedIds} />
    {rest.length > 0 && <div className="workspace-grid" style={{ marginTop: 11 }}>
      {rest.map((n) => <NodeView key={n.id} node={n} onSelect={onSelect} selectedId={selectedId} onAdd={onAdd} draftIds={draftIds} addedIds={addedIds} />)}
    </div>}
  </>;
}

function flatCount(node: UINode): number {
  return 1 + (node.children || []).reduce((n, c) => n + flatCount(c), 0);
}

export { flatCount };
