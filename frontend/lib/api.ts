// 127.0.0.1 (not localhost) matches how the backend binds and must be in its
// CORS_ORIGINS allowlist alongside http://localhost:3000.
const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return data as T;
}

export function formatTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function timeAgo(value?: string | null): string {
  if (!value) return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return value;
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(value).toLocaleDateString([], { month: "short", day: "numeric" });
}

export function fullTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Hex for the backend's actor `color` names (see COLOR_PALETTE in the API). */
export const ACTOR_COLOR_NAMES: Record<string, string> = {
  teal: "#0f766e", orange: "#ea580c", purple: "#7c3aed", gray: "#667781",
  violet: "#8b5cf6", amber: "#b07d1e", green: "#1e8a5f", pink: "#c2457a", blue: "#2f6fd1",
};

export const ACTOR_COLORS: Record<string, string> = {
  ai: "#008069", alice: "#2f6fd1", bob: "#b07d1e", charlie: "#1e8a5f", dana: "#c2457a",
  ...ACTOR_COLOR_NAMES,
};

const HASH_PALETTE = Object.values(ACTOR_COLOR_NAMES);

/**
 * Colour for an actor: prefer their stored `color` name, then the legacy
 * id-based map, then a stable hash so new people never all render grey.
 */
export function colorForActor(actor: { actor_id: string; color?: string | null }): string {
  const named = actor?.color ? ACTOR_COLOR_NAMES[actor.color.trim().toLowerCase()] : undefined;
  if (named) return named;
  if (actor?.color?.startsWith("#")) return actor.color;
  if (actor?.actor_id && ACTOR_COLORS[actor.actor_id]) return ACTOR_COLORS[actor.actor_id];
  const id = actor?.actor_id || "";
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return HASH_PALETTE[hash % HASH_PALETTE.length] || "#667781";
}

export const actorColor = colorForActor;

export function shortHash(value?: string | null): string {
  return value ? value.slice(0, 7) : "no commit yet";
}
