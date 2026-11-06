"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { Actor } from "@/lib/types";

export const VIEWER_KEY = "malleable-viewer";

export function useViewer(actors: Actor[]) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [viewer, setViewerState] = useState("alice");

  useEffect(() => {
    const fromUrl = searchParams.get("user");
    const stored = typeof window !== "undefined" ? window.localStorage.getItem(VIEWER_KEY) : null;
    const candidate = fromUrl || stored || "alice";
    if (actors.length && actors.some((a) => a.actor_id === candidate)) setViewerState(candidate);
    else if (actors.length) setViewerState(actors.find((a) => a.actor_type === "human")?.actor_id || actors[0].actor_id);
  }, [actors, searchParams]);

  const setViewer = useCallback((actorId: string) => {
    setViewerState(actorId);
    try { window.localStorage.setItem(VIEWER_KEY, actorId); } catch { /* private mode */ }
    const params = new URLSearchParams(searchParams.toString());
    params.set("user", actorId);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  }, [pathname, router, searchParams]);

  return { viewer, setViewer };
}

export default function TopBar({ viewer = "alice" }: { viewer?: string }) {
  const pathname = usePathname();
  const isDebug = pathname?.startsWith("/debug");
  return (
    <header className="topbar">
      <Link href={`/?user=${viewer}`} className="brand">
        <span className="brand-mark">m</span>
        <span className="brand-name">Museable</span>
        <span className="brand-tag">shared chat → personal apps</span>
      </Link>
      <div className="topbar-right">
        <nav className="topnav">
          <Link className="nav-link" href="/demo">▶ Example</Link>
          <Link className={`nav-link ${!isDebug ? "active" : ""}`} href={`/?user=${viewer}`}>App</Link>
          <Link className={`nav-link ${isDebug ? "active" : ""}`} href={`/debug?user=${viewer}`}>Debug ↗</Link>
        </nav>
      </div>
    </header>
  );
}
