from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.services.db import WRITE_LOCK, commit_all, current_head, db_cursor, json_dumps
from app.services.llm import _provider, generate_merged_workspace, generate_reply
from app.services.repository import (
    DEFAULT_CONVERSATION_ID,
    add_member,
    add_trace,
    conversation_human_members,
    create_actor,
    create_conversation,
    get_active_prompt,
    get_actor,
    get_commit_for_message,
    get_conversation,
    get_message_debug,
    get_prompt_version,
    get_messages,
    get_trace,
    ensure_workspace,
    get_workspace,
    insert_message,
    list_actors,
    list_commits,
    list_conversation_members,
    list_conversations,
    list_prompts,
    list_traces,
    new_id,
    push_to_dolthub,
    remove_member,
    save_prompt,
)
from app.services.ui import PatchError, apply_patches, preview_patches

router = APIRouter(prefix="/api")


class ChatRequest(BaseModel):
    actor_id: str
    content: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


class ConversationCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    member_ids: list[str] | None = None


class MemberRequest(BaseModel):
    actor_id: str


class PersonCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    color: str | None = None


class PatchRequest(BaseModel):
    actor_id: str
    summary: str = Field(min_length=1, max_length=200)
    patches: list[dict[str, Any]] = Field(min_length=1, max_length=40)
    conversation_id: str | None = None


class PromptRequest(BaseModel):
    prompt_text: str = Field(min_length=20, max_length=30000)
    model_name: str = Field(default="muse-glimmer-30b", min_length=1, max_length=120)
    temperature: float = Field(default=0.2, ge=0, le=2)
    created_by: str = "ai"


@router.get("/health")
def health():
    return {"ok": True, "head": current_head()}


@router.get("/users")
def users():
    return {"users": list_actors()}


@router.post("/people", status_code=201)
def create_person(request: PersonCreateRequest):
    name = request.display_name.strip()
    if not (1 <= len(name) <= 80):
        raise HTTPException(status_code=400, detail="display_name must be 1..80 characters")
    try:
        actor = create_actor(name, request.color)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"actor": actor, "user": actor}


@router.get("/conversations")
def conversations_list():
    return {"conversations": list_conversations()}


@router.post("/conversations", status_code=201)
def conversations_create(request: ConversationCreateRequest):
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    member_ids = request.member_ids
    if member_ids is None:
        # Default: all existing humans.
        member_ids = [a["actor_id"] for a in list_actors() if a["actor_type"] == "human"]
    # Validate existence up-front for a clean 400.
    for mid in member_ids:
        if not get_actor(mid):
            raise HTTPException(status_code=400, detail=f"Unknown actor: {mid}")
    humans = [mid for mid in member_ids if (a := get_actor(mid)) and a["actor_type"] == "human"]
    if not humans:
        raise HTTPException(status_code=400, detail="A conversation needs at least 1 human member")
    try:
        conv = create_conversation(title, member_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"conversation": conv, **conv}


@router.get("/conversations/{conversation_id}")
def conversation_detail(conversation_id: str):
    conv = get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Unknown conversation")
    members = [a for mid in conv["member_ids"] if (a := get_actor(mid))]
    return {"conversation": conv, **conv, "members": members}


@router.post("/conversations/{conversation_id}/members")
def conversation_add_member(conversation_id: str, request: MemberRequest):
    if not get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Unknown conversation")
    if not get_actor(request.actor_id):
        raise HTTPException(status_code=400, detail=f"Unknown actor: {request.actor_id}")
    try:
        conv = add_member(conversation_id, request.actor_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"conversation": conv, **conv, "member_ids": conv["member_ids"]}


@router.delete("/conversations/{conversation_id}/members/{actor_id}")
def conversation_remove_member(conversation_id: str, actor_id: str):
    if not get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Unknown conversation")
    if not get_actor(actor_id):
        raise HTTPException(status_code=400, detail=f"Unknown actor: {actor_id}")
    try:
        conv = remove_member(conversation_id, actor_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"conversation": conv, **conv, "member_ids": conv["member_ids"]}


@router.get("/conversation")
def conversation(limit: int = Query(default=100, ge=1, le=250), conversation_id: str = Query(default=DEFAULT_CONVERSATION_ID)):
    conv_id = conversation_id or DEFAULT_CONVERSATION_ID
    if not get_conversation(conv_id):
        raise HTTPException(status_code=404, detail="Unknown conversation")
    return {"conversation_id": conv_id, "messages": get_messages(conv_id, limit)}


@router.get("/workspaces/{owner_id}")
def workspace(owner_id: str, conversation_id: str = Query(default=DEFAULT_CONVERSATION_ID)):
    if not get_actor(owner_id):
        raise HTTPException(status_code=404, detail="Unknown user")
    try:
        # No conversation_id -> legacy group-main workspace (demo replay).
        # An explicit conversation ensures its blank workspace exists first
        # (get_workspace itself never creates one).
        if (conversation_id or DEFAULT_CONVERSATION_ID) != DEFAULT_CONVERSATION_ID:
            return ensure_workspace(conversation_id, owner_id)
        return get_workspace(owner_id, conversation_id=conversation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/commits")
def commits(limit: int = Query(default=60, ge=1, le=200)):
    return {"commits": list_commits(limit), "head": current_head()}


@router.get("/commits/{revision}/workspace")
def historical_workspace(revision: str, owner_id: str = Query(default="alice"), conversation_id: str = Query(default=DEFAULT_CONVERSATION_ID)):
    if not get_actor(owner_id):
        raise HTTPException(status_code=404, detail="Unknown user")
    try:
        return get_workspace(owner_id, revision=revision, conversation_id=conversation_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# Silence gate: the AI is an aside, not a participant. It only speaks when a
# turn is build-shaped (asks for a concrete artefact/change) or when enough
# new human chatter has piled up since its last reply to be worth an attempt.
import re as _re

_BUILD_VERBS = _re.compile(
    r"\b(add|make|create|build|grow|shape|draft|design|fuse|equip|give it|give mine|upgrade|swap|lock in|pull together|set up|spin up|lay out|map out|break down|merge|combine|synthesize|unify|give me|show|compare|remove|delete|get rid of|drop|clear|hide|rename|move|update|edit|change|track|chart|organise|organize|tally|schedule|rsvp|sketch|draw|pin|blueprint|dashboard|runsheet)\b",
    _re.IGNORECASE,
)
_MERGE_RE = _re.compile(
    r"(\b(merge|combine|synthesize|unify|fuse|reconcile|bring together)\b.*\b(apps|workspaces|views|interfaces|dashboards|avatars|drafts|mascots|characters|versions|all four|all 4|everything|ours)\b)|(\b(all four|all 4|four of us)\b.*\b(into one|together|single|shared|joint|complete plan|one figure|one character)\b)|(\b(whole street in one|one complete plan for sunday)\b)",
    _re.IGNORECASE,
)
_QUESTIONS = _re.compile(r"\?\s*$|^(what|who|when|where|how|can|could|should|is|are|do|does|did)\b", _re.IGNORECASE)
AI_SILENCE_THRESHOLD = 9999  # effectively never on a timer — build-shaped only.
# Organic talk accumulates into the next build turn's context instead of
# triggering a chatty reply. Raise/lower via env if you want periodic digests.


def _wants_ai(content: str, *, human_since_ai: int) -> tuple[bool, str]:
    text = content.strip()
    lowered = text.lower()
    words = text.split()
    # Explicit build/merge verbs or direct mention
    if _BUILD_VERBS.search(text) or "@ai" in lowered or lowered.startswith("ai ") or lowered.startswith("ai:") or lowered.startswith("ai,"):
        return True, "build-shaped request"
    # Substantive conversational turns (>= 18 words, or >= 3 human turns since AI last checked in with >= 12 words)
    # are observed by the AI so it can autonomously decide action="silent" | "grow" | "merge" without any command verbs!
    if len(words) >= 18 or (human_since_ai >= 3 and len(words) >= 12):
        return True, "ambient observation of substantive conversational turn"
    if human_since_ai >= AI_SILENCE_THRESHOLD:
        return True, f"{human_since_ai} human messages since AI last spoke"
    return False, "short banter — AI stays silent"


def _human_since_ai(messages: list[dict[str, Any]]) -> int:
    n = 0
    for m in reversed(messages):
        if m.get("actor_id") == "ai":
            break
        n += 1
    return n


@router.post("/chat")
async def chat(request: ChatRequest):
    conversation_id = request.conversation_id or DEFAULT_CONVERSATION_ID
    conv = get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Unknown conversation")
    actor = get_actor(request.actor_id)
    if not actor or actor["actor_type"] != "human":
        raise HTTPException(status_code=400, detail="Select a human member to send a group message. AI is a read-only viewing identity.")
    if request.actor_id not in conv["member_ids"]:
        raise HTTPException(status_code=403, detail="Actor is not a member of this conversation")
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Persist the human message as its own Dolt commit before invoking the model.
    with WRITE_LOCK:
        user_message_id = insert_message(actor_id=request.actor_id, content=request.content.strip(), conversation_id=conversation_id)
        user_commit = commit_all(f"chat: {actor['display_name']} — {request.content.strip()[:90]}", f"{actor['display_name']} <{request.actor_id}@malleable.demo>")

    transcript = get_messages(conversation_id, 100)
    is_merge = bool(_MERGE_RE.search(request.content))
    target_owner_id = "ai" if is_merge else request.actor_id
    # Every workspace read/write in this handler is scoped to the request's
    # conversation (speaker's personal workspace, ai as merge target,
    # member summary, member workspaces). group-main keeps working because
    # its legacy rows carry conversation_id='group-main'.
    workspace_before = ensure_workspace(conversation_id, target_owner_id) if conversation_id != DEFAULT_CONVERSATION_ID else get_workspace(target_owner_id, conversation_id=conversation_id)
    should_ai, reason = _wants_ai(request.content, human_since_ai=_human_since_ai(transcript))
    if not should_ai and not is_merge:
        # Store the human message only. No model call, no AI bubble, no trace.
        return {
            "conversation_id": conversation_id,
            "user_message_id": user_message_id,
            "user_commit": user_commit,
            "ai_message_id": None,
            "trace_id": None,
            "commit_hash": user_commit,
            "patches_applied": [],
            "patch_warning": None,
            "ai_silent": True,
            "silence_reason": reason,
            "messages": get_messages(conversation_id, 100),
            "workspace": workspace_before,
        }
    started = time.perf_counter()
    # Scope workspace context + merge to this conversation's human members only.
    human_actors = conversation_human_members(conversation_id)
    member_actor_ids = set(conv["member_ids"])
    try:
        if is_merge:
            member_workspaces = [get_workspace(a["actor_id"], conversation_id=conversation_id) for a in human_actors]
            generated = await generate_merged_workspace(
                actor_name=actor["display_name"],
                content=request.content.strip(),
                transcript=transcript,
                member_workspaces=member_workspaces,
                target_workspace=workspace_before,
                reasoning_effort="high",
            )
        else:
            summary_actors = [a for a in list_actors() if a["actor_id"] in member_actor_ids]
            workspaces_summary = {
                a["actor_id"]: sum(1 for n in (ensure_workspace(conversation_id, a["actor_id"]) if conversation_id != DEFAULT_CONVERSATION_ID else get_workspace(a["actor_id"], conversation_id=conversation_id))["flat_nodes"] if n.get("parent_id") is not None)
                for a in summary_actors
            }
            generated = await generate_reply(
                actor_name=actor["display_name"],
                content=request.content.strip(),
                transcript=transcript,
                workspace=workspace_before,
                workspaces_summary=workspaces_summary,
            )
            # If the AI autonomously concluded that now is the moment to synthesize ("action": "merge"),
            # escalate immediately to high-thinking synthesis into workspace_ai!
            if isinstance(generated.get("output"), dict) and str(generated["output"].get("action") or "").lower() == "merge":
                # Guard: only honor an autonomous merge once every human
                # member has cards (and at least 2 humans do) — otherwise
                # fall through with the model's original result as a grow
                # on the speaker instead of merging prematurely.
                human_counts = [int(workspaces_summary.get(a["actor_id"], 0)) for a in human_actors]
                merge_ready = len(human_counts) >= 2 and all(c > 0 for c in human_counts)
                if merge_ready:
                    is_merge = True
                    target_owner_id = "ai"
                    # ensure_workspace (not bare get_workspace): a non-group-main
                    # conversation may not have an ai workspace row yet.
                    workspace_before = ensure_workspace(conversation_id, "ai") if conversation_id != DEFAULT_CONVERSATION_ID else get_workspace("ai", conversation_id=conversation_id)
                    member_workspaces = [get_workspace(a["actor_id"], conversation_id=conversation_id) for a in human_actors]
                    generated = await generate_merged_workspace(
                        actor_name=actor["display_name"],
                        content=request.content.strip(),
                        transcript=transcript,
                        member_workspaces=member_workspaces,
                        target_workspace=workspace_before,
                        reasoning_effort="high",
                    )
        output = generated["output"]
        status = generated["status"]
    except Exception as exc:
        prompt = get_active_prompt()
        generated = {
            "provider": "error-fallback",
            "model_name": (_provider() or {}).get("model") or settings.local_model,
            "prompt": prompt,
            "input_snapshot": {
                "system_prompt": prompt.get("prompt_text", ""),
                "transcript": transcript,
                "workspace": workspace_before,
                "trigger": {"actor": actor["display_name"], "content": request.content},
            },
            "output": {
                "reply": "I couldn't reach the configured model. Your message is saved; check the debug trace for the error and provider settings.",
                "patches": [],
                "error": str(exc)[:1000],
            },
            "raw_model_output": None,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "status": "failed",
        }
        output = generated["output"]
        status = "failed"

    # Honor the model's autonomous decision to stay silent if it returned action="silent" and no patches.
    if isinstance(output, dict) and str(output.get("action") or "").lower() == "silent" and not output.get("patches"):
        return {
            "conversation_id": conversation_id,
            "user_message_id": user_message_id,
            "user_commit": user_commit,
            "ai_message_id": None,
            "trace_id": None,
            "commit_hash": user_commit,
            "patches_applied": [],
            "patch_warning": None,
            "ai_silent": True,
            "silence_reason": "AI concluded to stay silent",
            "messages": get_messages(conversation_id, 100),
            "workspace": workspace_before,
        }

    patches = output.get("patches", []) if isinstance(output, dict) else []
    safe_patches: list[dict[str, Any]] = []
    patch_warning = None
    if patches:
        try:
            preview = preview_patches(target_owner_id, patches, conversation_id=conversation_id)
            safe_patches = preview["patches"]
        except (PatchError, ValueError, TypeError) as exc:
            patch_warning = str(exc)
            safe_patches = []
            output["patches"] = []
            output["patch_warning"] = patch_warning

    trace_id = new_id("trace")
    response_message_id = new_id("msg")
    reply_text = str(output.get("reply") or "I have a new thought about the workspace.")[:8000]
    routing = (generated.get("input_snapshot") or {}).get("routing") or {}
    attempts = routing.get("attempts") or []
    steps = [
        {"name": "load conversation context", "status": "success"},
        {"name": "load all neighbour workspaces for high-thinking synthesis" if is_merge else "load active user's UI document", "status": "success"},
        {"name": "generate structured response (reasoning_effort=high)" if is_merge else "generate structured response", "status": status},
        *[
            {
                "name": f"model attempt: {a.get('provider', '?')}{' · reasoning=' + str(a.get('reasoning_effort')) if a.get('reasoning_effort') else ''}",
                "status": "warning" if a.get("escalation_reason") or a.get("error") else "success",
                "detail": a.get("escalation_reason") or a.get("error") or f"{a.get('duration_ms', 0)} ms · {a.get('reasoning_tokens', 0)} reasoning tok · ${a.get('cost_usd', 0)}",
            }
            for a in attempts
        ],
        {"name": "validate UI patches", "status": "warning" if patch_warning else "success", "detail": patch_warning},
        {"name": "persist response and trace", "status": "success"},
    ]

    # Keep model response, trace, accepted UI changes, and the resulting Dolt commit atomic.
    with WRITE_LOCK:
        if safe_patches:
            try:
                apply_patches(
                    target_owner_id, "ai", safe_patches,
                    summary="AI high-thinking merge of all workspaces" if is_merge else f"AI update for {actor['display_name']}",
                    make_commit=False,
                    conversation_id=conversation_id,
                )
            except Exception as exc:
                patch_warning = str(exc)[:500]
                safe_patches = []
                output["patch_warning"] = patch_warning
        with db_cursor() as (_, cursor):
            cursor.execute(
                """INSERT INTO messages
                (message_id, conversation_id, actor_id, content, message_type, trace_id, metadata_json)
                VALUES (%s, %s, 'ai', %s, 'text', %s, %s)""",
                (response_message_id, conversation_id, reply_text, trace_id, json_dumps({"triggered_by": user_message_id, "merged": is_merge})),
            )
        add_trace(
            trace_id=trace_id,
            triggering_message_id=user_message_id,
            response_message_id=response_message_id,
            actor_id="ai",
            # Keep the legacy trace label: the frontend parses the owner
            # out of `workspace_<owner>`; the per-conversation row id
            # (ws_<conv>_<owner>) would break that parsing.
            workspace_id=f"workspace_{target_owner_id}",
            prompt_version_id=generated.get("prompt", {}).get("version_id"),
            model_name=generated.get("model_name", "unknown"),
            provider=generated.get("provider", "unknown"),
            status=status,
            duration_ms=int(generated.get("duration_ms", 0)),
            input_snapshot=generated.get("input_snapshot", {}),
            output_snapshot={**output, "raw_model_output": generated.get("raw_model_output")},
            steps=steps,
        )
        commit_message = (
            f"chat: AI high-thinking merge [trace:{trace_id}]"
            if is_merge
            else f"chat: AI reply to {actor['display_name']} [trace:{trace_id}]"
        )
        if safe_patches:
            commit_message += f"; ui: {len(safe_patches)} patch(es)"
        ai_commit = commit_all(commit_message, "AI <ai@malleable.demo>")

    return {
        "conversation_id": conversation_id,
        "user_message_id": user_message_id,
        "user_commit": user_commit,
        "ai_message_id": response_message_id,
        "trace_id": trace_id,
        "commit_hash": ai_commit,
        "target_owner_id": target_owner_id,
        "merged": is_merge,
        "patches_applied": safe_patches,
        "patch_warning": patch_warning,
        "messages": get_messages(conversation_id, 100),
        "workspace": get_workspace(target_owner_id, conversation_id=conversation_id),
    }


class MergeRequest(BaseModel):
    actor_id: str = "alice"
    conversation_id: str | None = None


@router.post("/workspaces/merge")
async def merge_workspaces(request: MergeRequest):
    return await chat(
        ChatRequest(
            actor_id=request.actor_id,
            conversation_id=request.conversation_id,
            content="@ai Combine and merge all of our personal workspaces into one shared master view with high thinking.",
        )
    )


@router.post("/workspaces/{owner_id}/preview")
def preview_workspace(owner_id: str, request: PatchRequest):
    actor = get_actor(request.actor_id)
    if not actor or (actor["actor_type"] != "ai" and actor["actor_id"] != owner_id):
        raise HTTPException(status_code=403, detail="You can only edit your own personal workspace")
    conv = request.conversation_id or DEFAULT_CONVERSATION_ID
    try:
        if conv != DEFAULT_CONVERSATION_ID:
            ensure_workspace(conv, owner_id)
        return preview_patches(owner_id, request.patches, conversation_id=conv)
    except (PatchError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workspaces/{owner_id}/commit")
def commit_workspace(owner_id: str, request: PatchRequest):
    actor = get_actor(request.actor_id)
    if not actor or (actor["actor_type"] != "ai" and actor["actor_id"] != owner_id):
        raise HTTPException(status_code=403, detail="You can only commit changes to your own personal workspace")
    conv = request.conversation_id or DEFAULT_CONVERSATION_ID
    try:
        if conv != DEFAULT_CONVERSATION_ID:
            ensure_workspace(conv, owner_id)
        updated, commit_hash = apply_patches(owner_id, request.actor_id, request.patches, request.summary, conversation_id=conv)
        return {"workspace": updated, "commit_hash": commit_hash, "commits": list_commits(10)}
    except (PatchError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/debug/traces")
def debug_traces(limit: int = Query(default=60, ge=1, le=200), include_details: bool = Query(default=False)):
    return {"traces": list_traces(limit, include_details=include_details)}


@router.get("/debug/traces/{trace_id}")
def debug_trace(trace_id: str):
    trace = get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"trace": trace}


@router.get("/debug/message/{message_id}")
def debug_message(message_id: str):
    result = get_message_debug(message_id)
    if not result:
        raise HTTPException(status_code=404, detail="Message not found")
    return result


@router.get("/debug/prompts")
def debug_prompts():
    return {"active": get_active_prompt(), "versions": list_prompts()}


@router.get("/debug/prompts/{version_id}")
def debug_prompt_version(version_id: str):
    prompt = get_prompt_version(version_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt version not found")
    return {"prompt": prompt}


@router.post("/debug/prompts")
def create_prompt_version(request: PromptRequest):
    if request.created_by != "ai" and not get_actor(request.created_by):
        raise HTTPException(status_code=400, detail="Unknown prompt author")
    prompt, commit_hash = save_prompt(
        request.prompt_text, request.model_name, request.temperature, request.created_by
    )
    return {"active": prompt, "commit_hash": commit_hash, "versions": list_prompts()}


@router.get("/debug/status")
def debug_status():
    active = get_active_prompt()
    remote_configured = bool(settings.dolthub_remote)
    remotes: list[dict[str, Any]] = []
    try:
        with db_cursor() as (_, cursor):
            cursor.execute("SELECT name, url FROM dolt_remotes")
            remotes = list(cursor.fetchall())
    except Exception:
        remotes = []
    provider = _provider()
    return {
        "database": settings.dolt_database,
        "head": current_head(),
        "model_provider": (provider or {}).get("provider", "deterministic-demo"),
        "llm_mode": settings.llm_mode if provider else "demo",
        "model_name": (
            "deterministic-demo" if not provider else settings.local_model
        ),
        "dolthub_remote_configured": remote_configured,
        "dolthub_remote": settings.dolthub_remote or None,
        "remotes": remotes,
    }


@router.post("/dolt/push")
def dolt_push():
    if not settings.dolthub_remote:
        raise HTTPException(status_code=400, detail="Set DOLTHUB_REMOTE=organization/repository in .env and restart the backend")
    try:
        return push_to_dolthub(settings.dolthub_remote)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"DoltHub push failed: {exc}") from exc
