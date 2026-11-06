"use client";

import { colorForActor } from "@/lib/api";

export function Avatar({ actorId, name, size = 28, color }: { actorId: string; name: string; size?: number; color?: string | null }) {
  const bg = colorForActor({ actor_id: actorId, color });
  return (
    <div
      aria-hidden
      style={{
        flex: `0 0 ${size}px`, width: size, height: size, display: "grid", placeItems: "center",
        borderRadius: "50%", fontSize: size <= 24 ? 9 : 10, fontWeight: 800, color: "#fff", textShadow: "0 1px 1px rgba(0,0,0,.18)",
        background: bg,
      }}
    >
      {actorId === "ai" ? "✦" : name.slice(0, 1).toUpperCase()}
    </div>
  );
}
