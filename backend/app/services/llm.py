from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx

from app.config import settings
from app.services.db import json_loads
from app.services.repository import get_active_prompt


# Muse Glimmer runs locally via llama.cpp — $0 marginal cost per token.


def _cost(model: str, usage: dict[str, Any] | None) -> float:
    return 0.0


def _local_provider() -> dict[str, Any]:
    return {
        "provider": "local-llama.cpp", "base_url": settings.local_base_url,
        "api_key": settings.local_api_key, "model": settings.local_model,
    }


def _provider() -> dict[str, Any] | None:
    """Primary provider for status/error reporting. None = demo mode."""
    if settings.llm_mode == "demo":
        return None
    return _local_provider()


async def _call(provider: dict[str, Any], payload: dict[str, Any], *, timeout: float = 90.0) -> tuple[str, str, dict[str, Any], int]:
    started = time.perf_counter()
    headers = {"Authorization": f"Bearer {provider['api_key']}"} if provider["api_key"] else {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{provider['base_url']}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    duration_ms = int((time.perf_counter() - started) * 1000)
    message = data["choices"][0]["message"]
    content = message.get("content")
    if content is None:
        content = ""
    reasoning = message.get("reasoning_content") or ""
    return content, reasoning, data.get("usage") or {}, duration_ms


def _parse_model_json(content_text: str) -> Any:
    text = (content_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json_loads(text, None)
    if parsed is None and "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}")
        if start < end:
            parsed = json_loads(text[start : end + 1], None)
    return parsed


def _result(provider, model_name, prompt, messages, compact_workspace, content_text, usage, duration_ms, routing, reasoning_text: str = ""):
    parsed = _parse_model_json(content_text)
    if not isinstance(parsed, dict):
        parsed = {"reply": content_text, "patches": []}
    parsed.setdefault("reply", "I've updated my understanding of the workspace.")
    parsed.setdefault("patches", [])
    if not isinstance(parsed["patches"], list):
        parsed["patches"] = []
    if reasoning_text:
        parsed["reasoning"] = reasoning_text
    return {
        "provider": provider["provider"], "model_name": model_name, "prompt": prompt,
        "input_snapshot": {"messages": messages, "system_prompt": payload_system(messages), "workspace": compact_workspace, "routing": routing},
        "output": parsed, "raw_model_output": content_text, "duration_ms": duration_ms,
        "status": "success", "usage": usage, "cost_usd": _cost(model_name, usage),
    }


def payload_system(messages):
    return messages[0]["content"] if messages and messages[0].get("role") == "system" else ""


async def generate_reply(
    *,
    actor_name: str,
    content: str,
    transcript: list[dict[str, Any]],
    workspace: dict[str, Any],
    workspaces_summary: dict[str, int] | None = None,
) -> dict[str, Any]:
    prompt = get_active_prompt()
    mode = settings.llm_mode if settings.llm_mode in {"local", "demo"} else "local"
    system_prompt = prompt.get("prompt_text") or "You help people build interfaces."
    compact_workspace = {"owner_actor_id": workspace["owner_actor_id"], "nodes": workspace["nodes"]}
    # Root node (parent_id None) of the speaker's workspace in THIS
    # conversation: 'alice_root' for legacy group-main, a per-conversation
    # unique id otherwise. The model must insert under this id.
    root_node = next((n for n in workspace.get("flat_nodes", []) if n.get("parent_id") is None), None)
    workspace_root_id = root_node["id"] if root_node else None
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps({
            "active_user": actor_name,
            "latest_message": content,
            "recent_conversation": [{"actor": i["display_name"], "content": i["content"]} for i in transcript[-28:]],
            "current_ui": compact_workspace,
            "workspace_root_id": workspace_root_id,
            "all_workspaces_summary": workspaces_summary or {},
            "task": "Conclude autonomously whether action should be 'silent', 'grow', or 'merge'. Return JSON only.",
        }, ensure_ascii=False)},
    ]

    if mode == "demo":
        demo_out = mock_output(actor_name, content, workspace)
        raw_demo = json.dumps(demo_out, ensure_ascii=False)
        return _result(
            {"provider": "deterministic-demo"},
            "deterministic-demo",
            prompt,
            messages,
            compact_workspace,
            raw_demo,
            {},
            0,
            {"mode": "demo", "attempts": [{"provider": "deterministic-demo", "model": "deterministic-demo", "duration_ms": 0, "cost_usd": 0.0}], "final_provider": "deterministic-demo"},
        )

    provider = _local_provider()
    # llama.cpp serves one model; DB prompt rows may pin a stale model name.
    model_name = settings.local_model
    payload: dict[str, Any] = {
        "model": model_name, "messages": messages,
        "temperature": float(prompt.get("temperature", 0.2)),
        "response_format": {"type": "json_object"},
        # No reasoning_effort field: llama.cpp rejects unknown fields.
    }
    routing: dict[str, Any] = {"mode": mode, "attempts": []}
    call_res = await _call(provider, payload)
    if len(call_res) == 4:
        content_text, reasoning_text, usage, duration_ms = call_res
    else:
        content_text, usage, duration_ms = call_res  # type: ignore[misc]
        reasoning_text = ""
    reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
    routing["attempts"].append({
        "provider": provider["provider"], "model": model_name, "duration_ms": duration_ms,
        "usage": usage, "reasoning_tokens": reasoning_tokens, "cost_usd": _cost(model_name, usage), "escalation_reason": None,
    })
    routing["final_provider"] = provider["provider"]
    result = _result(provider, model_name, prompt, messages, compact_workspace, content_text, usage, duration_ms, routing, reasoning_text=reasoning_text)
    result["usage"] = usage
    result["cost_usd"] = _cost(model_name, usage)
    result["total_cost_usd"] = round(sum(a.get("cost_usd", 0.0) for a in routing["attempts"]), 6)
    return result


MERGE_SYSTEM_PROMPT = """You are the master UI synthesis architect for a group of neighbours who have each grown their own personal workspace from a shared conversation.
You are invoked for high-thinking synthesis to deeply analyze ALL participants' personal UI workspaces alongside the conversation transcript, reconcile their numbers/timelines/constraints, and synthesize a single unified **Shared Master Event App** under the shared workspace root (given as `target_shared_workspace_root_id`; use it as every insert's `parent_id`).

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


async def generate_merged_workspace(
    *,
    actor_name: str,
    content: str,
    transcript: list[dict[str, Any]],
    member_workspaces: list[dict[str, Any]],
    target_workspace: dict[str, Any],
    reasoning_effort: str = "high",
) -> dict[str, Any]:
    from app.services.seeds import get_active_merge_prompt

    prompt = get_active_prompt()
    mode = settings.llm_mode if settings.llm_mode in {"local", "demo"} else "local"
    active_merge_prompt = get_active_merge_prompt() or MERGE_SYSTEM_PROMPT
    compact_members = [
        {
            "owner": ws["owner_actor_id"],
            "title": ws.get("title") or ws["owner_actor_id"],
            "components": [
                {"id": n["id"], "type": n["type"], "props": n["props"], "layout": n["layout"]}
                for n in ws.get("flat_nodes", [])
                if n.get("parent_id") is not None
            ],
        }
        for ws in member_workspaces
    ]
    compact_target = {"owner_actor_id": target_workspace["owner_actor_id"], "nodes": target_workspace["nodes"]}
    target_root = next((n for n in target_workspace.get("flat_nodes", []) if n.get("parent_id") is None), None)
    target_root_id = target_root["id"] if target_root else "ai_root"
    # Stored prompts (prompt_versions / meta-seed) still say `ai_root`;
    # rewrite that literal to this conversation's actual shared-root id
    # (identical for legacy group-main, so /demo prompts are unchanged).
    active_merge_prompt = active_merge_prompt.replace("ai_root", target_root_id)
    messages = [
        {"role": "system", "content": active_merge_prompt},
        {"role": "user", "content": json.dumps({
            "requested_by": actor_name,
            "merge_request": content,
            "recent_conversation": [{"actor": i["display_name"], "content": i["content"]} for i in transcript[-30:]],
            "neighbour_workspaces_to_merge": compact_members,
            "workspace_root_id": target_root_id,
            "target_shared_workspace_root_id": target_root_id,
            "task": f"Synthesize and merge all neighbour workspaces into a unified Shared Master App under parent_id '{target_root_id}'. Return JSON only.",
        }, ensure_ascii=False)},
    ]

    if mode == "demo":
        demo_patches = [
            {"op": "insert", "parent_id": target_root_id, "node": {"id": "merged_kitty", "type": "Metric", "props": {"label": "Unified Kitty & Buffer", "value": "£600 · £245 buffer", "delta": "£4.28/head across 140 peak"}, "layout": {"span": 1}, "order_index": 10}},
            {"op": "insert", "parent_id": target_root_id, "node": {"id": "merged_readiness", "type": "Metric", "props": {"label": "4-Neighbour Synthesis", "value": f"{sum(len(m['components']) for m in compact_members)} → 6", "delta": "Alice + Bob + Charlie + Dana merged"}, "layout": {"span": 1}, "order_index": 20}},
            {"op": "insert", "parent_id": target_root_id, "node": {"id": "merged_map", "type": "Map", "props": {"title": "Unified Elm Street Site Blueprint", "caption": "Merged spatial layout across Charlie, Dana & Alice", "markers": [{"label": "Acoustic Stage", "location": "North bollard (facing south)", "note": "82dB cap for #14"}, {"label": "Café & Spritz Bar", "location": "Dana's west terrace", "note": "240V RCD + 220 cups"}, {"label": "Food Trestles", "location": "East kerb past postbox", "note": "60 samosas + 38 sausage rolls"}]}, "layout": {"span": 2}, "order_index": 30}},
        ]
        raw = json.dumps({"reply": "Merged all personal workspaces into the Shared Master App.", "patches": demo_patches})
        return _result(
            {"provider": "deterministic-demo"},
            "deterministic-demo",
            prompt,
            messages,
            compact_target,
            raw,
            {},
            0,
            {"mode": "merge-demo", "reasoning_effort": reasoning_effort, "attempts": []},
            reasoning_text="Deterministic offline merge fallback.",
        )

    provider = _local_provider()
    model_name = settings.local_model
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
        # No reasoning_effort field: llama.cpp rejects unknown fields.
        # High-thinking intent is recorded in routing metadata below.
    }
    call_res = await _call(provider, payload, timeout=120.0)
    if len(call_res) == 4:
        content_text, reasoning_text, usage, duration_ms = call_res
    else:
        content_text, usage, duration_ms = call_res  # type: ignore[misc]
        reasoning_text = ""
    reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
    routing = {
        "mode": "merge-high-thinking",
        "reasoning_effort": reasoning_effort,
        "final_provider": provider["provider"],
        "attempts": [{
            "provider": provider["provider"],
            "model": model_name,
            "reasoning_effort": reasoning_effort,
            "duration_ms": duration_ms,
            "usage": usage,
            "reasoning_tokens": reasoning_tokens,
            "cost_usd": _cost(model_name, usage),
        }],
    }
    result = _result(provider, model_name, prompt, messages, compact_target, content_text, usage, duration_ms, routing, reasoning_text=reasoning_text)
    result["usage"] = usage
    result["cost_usd"] = _cost(model_name, usage)
    result["total_cost_usd"] = result["cost_usd"]
    return result


def mock_output(actor_name: str, content: str, workspace: dict[str, Any]) -> dict[str, Any]:
    lower = content.lower()
    patches: list[dict[str, Any]] = []
    reply = f"Got it, {actor_name}. I'll keep that in the shared conversation."
    root_node = next((node for node in workspace.get("flat_nodes", []) if node.get("parent_id") is None), None)
    root_id = root_node["id"] if root_node else None
    existing_ids = {node["id"] for node in workspace.get("flat_nodes", [])}
    if any(word in lower for word in ("map", "location", "places", "where")):
        node_id = f"map_{uuid.uuid4().hex[:10]}"
        if root_id and node_id not in existing_ids:
            patches.append({
                "op": "insert", "parent_id": root_id,
                "node": {"id": node_id, "type": "Map", "order_index": 90,
                         "props": {"title": "Places mentioned", "description": "A place to collect and compare locations from the conversation.", "locations": ["Add a place from chat"]},
                         "layout": {"span": 2}},
            })
        reply = "I added a map-style component to your workspace. It's a starter view; add actual places as the group discusses them."
    elif any(word in lower for word in ("compare", "comparison", "options", "price", "budget", "trails")):
        node_id = f"compare_{uuid.uuid4().hex[:10]}"
        if root_id and node_id not in existing_ids:
            patches.append({
                "op": "insert", "parent_id": root_id,
                "node": {"id": node_id, "type": "Comparison", "order_index": 90,
                         "props": {"title": "Options to compare", "columns": ["Option", "Cost", "Notes"], "rows": [["Option A", "TBD", "Add details from chat"], ["Option B", "TBD", "Add details from chat"]]},
                         "layout": {"span": 2}},
            })
        reply = "I added a comparison panel to your workspace. I've left unknown details as TBD rather than inventing them."
    elif any(word in lower for word in ("checklist", "tasks", "todo", "to-do")):
        node_id = f"check_{uuid.uuid4().hex[:10]}"
        if root_id and node_id not in existing_ids:
            patches.append({
                "op": "insert", "parent_id": root_id,
                "node": {"id": node_id, "type": "Checklist", "order_index": 90,
                         "props": {"title": "Next steps", "items": ["Collect options", "Compare details", "Agree as a group"]},
                         "layout": {"span": 2}},
            })
        reply = "I added a checklist to your workspace so the next steps have a home."
    elif any(word in lower for word in ("bigger", "larger", "compact", "layout", "rearrange")):
        note = next((node for node in workspace.get("flat_nodes", []) if node["type"] == "Note"), None)
        if note:
            patches.append({"op": "set", "id": note["id"], "layout": {"span": 2}, "props": {}})
        reply = "I made the introductory note span the full workspace width. You can keep refining the layout or commit further changes directly."
    else:
        reply = "I understand. I can turn requests like 'add a map', 'compare the options', or 'make a checklist' into editable workspace components. This response is running in deterministic demo mode; run `uv run app` to use Muse Glimmer."
    return {"reply": reply, "patches": patches}
