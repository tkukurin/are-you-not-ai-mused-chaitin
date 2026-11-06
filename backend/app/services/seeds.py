from __future__ import annotations

from typing import Any

from app.services.db import db_cursor, default_system_prompt, json_loads
from app.services.repository import get_active_prompt

STREET_FEAST_GROWTH_PROMPT = default_system_prompt()

STREET_FEAST_MERGE_PROMPT = """You are the master UI synthesis architect for a group of neighbours who have each grown their own personal workspace from a shared conversation.
You are invoked with `reasoning_effort="high"` to deeply analyze ALL participants' personal UI workspaces alongside the conversation transcript, reconcile their numbers/timelines/constraints, and synthesize a single unified **Shared Master Event App** under the shared workspace root (given as `target_shared_workspace_root_id`; use it as every insert's `parent_id`).

Think carefully through:
1. Cross-workspace dependencies (how Alice's host runsheet, Bob's £600 budget & rain trigger, Charlie's Elm Street spatial map & #14 82dB noise protocol, and Dana's 220-cup bar & allergen board fit together).
2. Any tensions or bottlenecks (e.g. 6pm–6:30pm peak of 140 neighbours vs Dana's 5:45pm spritz pre-batch & Tomaš shift relief; 2pm Sunday weather trigger vs Charlie's scout-hut tarps or St Jude's Hall fallback).

Build a cohesive, non-redundant 6-to-7 component master interface inside the shared workspace root using `op: "insert"`:
- 2 top `Metric` cards (`layout: {"span": 1}`): e.g. Unified Kitty & Per-Head Cost, and Peak Capacity & Service Readiness.
- 1 unified `Map` (`layout: {"span": 2}`): Master Elm Street Site & Power Blueprint combining all spatial zones, power feeds, and rain tarp anchors (`markers: [{"label": "...", "location": "...", "note": "..."}]`).
- 1 unified `Comparison` or `Table` (`layout: {"span": 2}`): Master Operations, Budget & Allergen Matrix combining owners, costs, and status across all 4 neighbours (`columns` + `rows`).
- 1 `Chart` (`layout: {"span": 2}`): Combined Hourly Footfall & Service Load (`data: [{"hour": "4pm", "attendees": 35}, ...]`).
- 1 `Checklist` (`layout: {"span": 1}`): Cross-Neighbour Critical Path (the 5–6 make-or-break synchronized checkpoints across Alice, Bob, Charlie, and Dana).
- 1 `Note` (`layout: {"span": 1}`): Synthesis Summary & Contingency Protocol explaining how the 4 personal apps were merged.

Return ONLY valid JSON:
{
  "reply": "A concise synthesis summary explaining how you combined all neighbours' workspaces into the Shared Master App",
  "patches": [
    {"op": "insert", "parent_id": "<target_shared_workspace_root_id>", "node": {"id": "merged_...", "type": "...", "props": {...}, "layout": {"span": ...}, "order_index": 10}}
  ]
}"""

_STREET_FEAST: dict[str, Any] = {
    "id": "street-feast",
    "branch": "main",
    "icon": "🎪",
    "label": "Street Feast App",
    "tagline": "Neighbours plan a twilight block party → 4 operational apps → 1 Shared Event Command Center",
    "roles": {
        "alice": "Host & Producer",
        "bob": "Budget & Weather Risk",
        "charlie": "Street Layout & Sound",
        "dana": "Corner Café & Bar",
        "ai": "High-Thinking Synthesis of All 4 Apps",
    },
    "growth_prompt": STREET_FEAST_GROWTH_PROMPT,
    "merge_prompt": STREET_FEAST_MERGE_PROMPT,
}


def _read_meta_setting() -> dict[str, Any]:
    with db_cursor() as (_, cursor):
        cursor.execute("SELECT value_json FROM app_settings WHERE setting_key='meta_seed'")
        row = cursor.fetchone()
        if row:
            return json_loads(row.get("value_json"), {}) or {}
    return {}


def get_meta_seed_state() -> dict[str, Any]:
    custom_meta = _read_meta_setting()
    # Only the shipped street-feast seed exists; ignore stale overrides
    # recorded under any other seed id.
    if custom_meta.get("id") not in (None, "street-feast"):
        custom_meta = {}
    active_prompt = get_active_prompt()
    preset = _STREET_FEAST
    return {
        "active_seed_id": "street-feast",
        "active_branch": preset["branch"],
        "label": custom_meta.get("label") or preset["label"],
        "icon": custom_meta.get("icon") or preset["icon"],
        "tagline": custom_meta.get("tagline") or preset["tagline"],
        "roles": custom_meta.get("roles") or preset["roles"],
        "growth_prompt": active_prompt.get("prompt_text") or preset["growth_prompt"],
        "merge_prompt": custom_meta.get("merge_prompt") or preset["merge_prompt"],
        "presets": [
            {
                "id": preset["id"],
                "branch": preset["branch"],
                "icon": preset["icon"],
                "label": preset["label"],
                "tagline": preset["tagline"],
                "roles": preset["roles"],
                "growth_prompt": preset["growth_prompt"],
                "merge_prompt": preset["merge_prompt"],
            }
        ],
    }


def get_active_merge_prompt() -> str:
    try:
        state = get_meta_seed_state()
        return state["merge_prompt"]
    except Exception:
        return STREET_FEAST_MERGE_PROMPT
