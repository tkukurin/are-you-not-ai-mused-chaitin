from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.services.db import (
    WRITE_LOCK,
    commit_all,
    current_head,
    db_cursor,
    json_dumps,
    json_loads,
    seed_nodes,
    validate_revision,
)

COLOR_PALETTE = ["blue", "amber", "green", "pink", "violet", "teal", "orange", "purple", "gray"]
DEFAULT_CONVERSATION_ID = "group-main"
SEED_AI_MESSAGE = "Group chat ready — talk through your idea together, and each neighbour's personal workspace will grow from the conversation."


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _conversation_row(row: dict[str, Any], member_ids: list[str]) -> dict[str, Any]:
    return {
        "conversation_id": row["conversation_id"],
        "title": row["title"],
        "created_at": _iso(row.get("created_at")),
        "member_ids": list(member_ids),
        "message_count": int(row.get("message_count") or 0),
    }


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:14]}"


def list_actors() -> list[dict[str, Any]]:
    with db_cursor() as (_, cursor):
        cursor.execute("SELECT actor_id, display_name, actor_type, color FROM actors ORDER BY FIELD(actor_id, 'ai', 'alice', 'bob', 'charlie', 'dana'), display_name, actor_id")
        return list(cursor.fetchall())


def get_actor(actor_id: str) -> dict[str, Any] | None:
    with db_cursor() as (_, cursor):
        cursor.execute("SELECT actor_id, display_name, actor_type, color FROM actors WHERE actor_id=%s", (actor_id,))
        return cursor.fetchone()


def _slugify(display_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", display_name.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:40].strip("-")
    return slug or "person"


def workspace_id_for(conversation_id: str, owner_id: str) -> str:
    """Per-conversation workspace id (spec: ws_<conv>_<owner>, <=64 chars).

    The (conversation_id, owner_id) order is the spec'd call signature;
    legacy group-main rows keep `workspace_<owner>` and are never renamed.
    Long ids fall back to a hashed form.
    """
    candidate = f"ws_{conversation_id}_{owner_id}"
    if len(candidate) <= 64:
        return candidate
    return f"ws_{hashlib.sha1(f'{conversation_id}:{owner_id}'.encode()).hexdigest()}"


def ensure_workspace(conversation_id: str, owner_id: str) -> dict[str, Any]:
    """Create the (owner, conversation) workspace (root Grid only) if
    missing, then return it via get_workspace.

    Endpoints call this before get_workspace for non-legacy conversations:
    a missing per-conversation workspace is a KeyError there, not an
    implicit create. Acquires WRITE_LOCK and commits its own row, so do
    not call it while holding WRITE_LOCK for an uncommitted batch.
    """
    conv = conversation_id or DEFAULT_CONVERSATION_ID
    try:
        return get_workspace(owner_id, conversation_id=conv)
    except KeyError:
        pass
    actor = get_actor(owner_id)
    if not actor:
        raise KeyError(f"Unknown workspace owner: {owner_id}")
    title = "Shared workspace" if owner_id == "ai" else f"{actor['display_name']}'s workspace"
    workspace_id = workspace_id_for(conv, owner_id)
    with WRITE_LOCK:
        # Re-check under lock; another request may have created it.
        try:
            return get_workspace(owner_id, conversation_id=conv)
        except KeyError:
            pass
        with db_cursor() as (_, cursor):
            cursor.execute(
                "INSERT INTO workspaces (workspace_id, owner_actor_id, conversation_id, title) VALUES (%s, %s, %s, %s)",
                (workspace_id, owner_id, conv, title),
            )
            cursor.executemany(
                """INSERT INTO ui_nodes
                (node_id, workspace_id, parent_id, node_type, props_json, layout_json, order_index)
                VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                seed_nodes(owner_id, workspace_id, root_node_id=f"{workspace_id}_root", title=title),
            )
        commit_all(f"workspace: create blank workspace for {owner_id} in {conv}", "Malleable Demo <system@malleable.demo>")
    return get_workspace(owner_id, conversation_id=conv)


def create_actor(display_name: str, color: str | None = None) -> dict[str, Any]:
    """Create a human actor + its legacy group-main workspace
    (workspace_<id>, '<actor>_root') for /demo compat, as before.
    Per-conversation workspaces are created by create_conversation /
    add_member / ensure_workspace, never here."""
    name = (display_name or "").strip()
    if not (1 <= len(name) <= 80):
        raise ValueError("display_name must be 1..80 characters")
    requested_color = (color or "").strip().lower() or None
    if requested_color and requested_color not in COLOR_PALETTE:
        raise ValueError(f"color must be one of: {', '.join(COLOR_PALETTE)}")
    with WRITE_LOCK:
        with db_cursor() as (_, cursor):
            cursor.execute("SELECT color FROM actors")
            used_colors = {str(r["color"]) for r in cursor.fetchall()}
            chosen_color = requested_color or next((c for c in COLOR_PALETTE if c not in used_colors), COLOR_PALETTE[0])
            base = _slugify(name)
            actor_id, suffix = base, 2
            while True:
                cursor.execute("SELECT COUNT(*) AS n FROM actors WHERE actor_id=%s", (actor_id,))
                if int(cursor.fetchone()["n"]) == 0:
                    break
                actor_id = f"{base}-{suffix}"
                suffix += 1
            cursor.execute(
                "INSERT INTO actors (actor_id, display_name, actor_type, color) VALUES (%s, %s, 'human', %s)",
                (actor_id, name, chosen_color),
            )
            workspace_id = f"workspace_{actor_id}"
            cursor.execute(
                "INSERT INTO workspaces (workspace_id, owner_actor_id, conversation_id, title) VALUES (%s, %s, 'group-main', %s)",
                (workspace_id, actor_id, f"{name}'s workspace"),
            )
            cursor.executemany(
                """INSERT INTO ui_nodes
                (node_id, workspace_id, parent_id, node_type, props_json, layout_json, order_index)
                VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                seed_nodes(actor_id, workspace_id, title=f"{name}'s workspace"),
            )
        commit_all(f"people: add {name}", f"{name} <{actor_id}@malleable.demo>")
    actor = get_actor(actor_id)
    assert actor is not None
    return actor


def list_conversation_members(conversation_id: str) -> list[str]:
    with db_cursor() as (_, cursor):
        cursor.execute(
            "SELECT actor_id FROM conversation_members WHERE conversation_id=%s ORDER BY FIELD(actor_id, 'ai'), actor_id",
            (conversation_id,),
        )
        return [str(r["actor_id"]) for r in cursor.fetchall()]


def _conversation_exists(cursor: Any, conversation_id: str) -> dict[str, Any] | None:
    cursor.execute("SELECT conversation_id, title, created_at FROM conversations WHERE conversation_id=%s", (conversation_id,))
    return cursor.fetchone()


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    with db_cursor() as (_, cursor):
        row = _conversation_exists(cursor, conversation_id)
        if not row:
            return None
        cursor.execute("SELECT COUNT(*) AS n FROM messages WHERE conversation_id=%s", (conversation_id,))
        count = int(cursor.fetchone()["n"])
    members = list_conversation_members(conversation_id)
    return _conversation_row({**row, "message_count": count}, members)


def list_conversations() -> list[dict[str, Any]]:
    with db_cursor() as (_, cursor):
        cursor.execute(
            """SELECT c.conversation_id, c.title, c.created_at,
                (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.conversation_id) AS message_count
                FROM conversations c ORDER BY c.created_at, c.conversation_id"""
        )
        rows = list(cursor.fetchall())
        cursor.execute("SELECT conversation_id, actor_id FROM conversation_members ORDER BY FIELD(actor_id, 'ai'), actor_id")
        members_by_conv: dict[str, list[str]] = {}
        for r in cursor.fetchall():
            members_by_conv.setdefault(str(r["conversation_id"]), []).append(str(r["actor_id"]))
    return [_conversation_row(row, members_by_conv.get(str(row["conversation_id"]), [])) for row in rows]


def _create_blank_workspace_rows(cursor: Any, owner_id: str, conversation_id: str, title: str) -> None:
    workspace_id = workspace_id_for(conversation_id, owner_id)
    cursor.execute(
        "INSERT INTO workspaces (workspace_id, owner_actor_id, conversation_id, title) VALUES (%s, %s, %s, %s)",
        (workspace_id, owner_id, conversation_id, title),
    )
    cursor.executemany(
        """INSERT INTO ui_nodes
        (node_id, workspace_id, parent_id, node_type, props_json, layout_json, order_index)
        VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        seed_nodes(owner_id, workspace_id, root_node_id=f"{workspace_id}_root", title=title),
    )


def create_conversation(title: str, member_ids: list[str] | None = None) -> dict[str, Any]:
    clean_title = (title or "").strip()
    if not (1 <= len(clean_title) <= 200):
        raise ValueError("title must be 1..200 characters")
    requested = list(dict.fromkeys(member_ids or []))
    # Always include ai; dedupe while preserving order.
    members: list[str] = ["ai"] + [m for m in requested if m != "ai"]
    resolved: dict[str, dict[str, Any]] = {}
    for mid in members:
        actor = get_actor(mid)
        if not actor:
            raise ValueError(f"Unknown actor: {mid}")
        resolved[mid] = actor
    if not any(resolved[mid]["actor_type"] == "human" for mid in members):
        raise ValueError("A conversation needs at least 1 human member")
    conversation_id = new_id("conv")
    with WRITE_LOCK:
        with db_cursor() as (_, cursor):
            cursor.execute("INSERT INTO conversations (conversation_id, title) VALUES (%s, %s)", (conversation_id, clean_title))
            cursor.executemany(
                "INSERT INTO conversation_members (conversation_id, actor_id) VALUES (%s, %s)",
                [(conversation_id, mid) for mid in members],
            )
            cursor.execute(
                """INSERT INTO messages
                (message_id, conversation_id, actor_id, content, message_type, trace_id, metadata_json)
                VALUES (%s, %s, 'ai', %s, 'text', NULL, %s)""",
                (new_id("msg"), conversation_id, SEED_AI_MESSAGE, json_dumps({})),
            )
            # Blank (root-only) per-conversation workspace for every member
            # (incl. ai as the shared/merge target of this conversation).
            for mid in members:
                a_title = "Shared workspace" if mid == "ai" else f"{resolved[mid]['display_name']}'s workspace"
                _create_blank_workspace_rows(cursor, mid, conversation_id, a_title)
        commit_all(f"chat: create conversation '{clean_title[:80]}'", "Malleable Demo <system@malleable.demo>")
    conv = get_conversation(conversation_id)
    assert conv is not None
    return conv


def add_member(conversation_id: str, actor_id: str) -> dict[str, Any]:
    actor = get_actor(actor_id)
    if not actor:
        raise ValueError(f"Unknown actor: {actor_id}")
    with WRITE_LOCK:
        with db_cursor() as (_, cursor):
            if not _conversation_exists(cursor, conversation_id):
                raise KeyError(f"Unknown conversation: {conversation_id}")
            cursor.execute(
                "INSERT IGNORE INTO conversation_members (conversation_id, actor_id) VALUES (%s, %s)",
                (conversation_id, actor_id),
            )
            changed = int(cursor.rowcount or 0)
            if changed:
                # Per-conversation workspace for the new member. group-main
                # keeps its legacy workspace_<actor> row, so only create
                # the blank per-conversation row when none exists yet
                # (for group-main that legacy row already satisfies this).
                cursor.execute(
                    "SELECT COUNT(*) AS n FROM workspaces WHERE owner_actor_id=%s AND conversation_id=%s",
                    (actor_id, conversation_id),
                )
                if int(cursor.fetchone()["n"]) == 0:
                    a_title = "Shared workspace" if actor_id == "ai" else f"{actor['display_name']}'s workspace"
                    _create_blank_workspace_rows(cursor, actor_id, conversation_id, a_title)
        if changed:
            commit_all(f"chat: add {actor_id} to {conversation_id}", "Malleable Demo <system@malleable.demo>")
    conv = get_conversation(conversation_id)
    assert conv is not None
    return conv


def remove_member(conversation_id: str, actor_id: str) -> dict[str, Any]:
    """Remove membership only; messages and workspace are kept."""
    with WRITE_LOCK:
        with db_cursor() as (_, cursor):
            if not _conversation_exists(cursor, conversation_id):
                raise KeyError(f"Unknown conversation: {conversation_id}")
            cursor.execute(
                "DELETE FROM conversation_members WHERE conversation_id=%s AND actor_id=%s",
                (conversation_id, actor_id),
            )
            changed = int(cursor.rowcount or 0)
        if changed:
            commit_all(f"chat: remove {actor_id} from {conversation_id}", "Malleable Demo <system@malleable.demo>")
    conv = get_conversation(conversation_id)
    assert conv is not None
    return conv


def conversation_human_members(conversation_id: str) -> list[dict[str, Any]]:
    ids = list_conversation_members(conversation_id)
    humans = []
    for mid in ids:
        actor = get_actor(mid)
        if actor and actor["actor_type"] == "human":
            humans.append(actor)
    return humans


def get_messages(conversation_id: str = DEFAULT_CONVERSATION_ID, limit: int = 100) -> list[dict[str, Any]]:
    # Backward-compat: old positional call get_messages(100) meant limit.
    if isinstance(conversation_id, int):
        conversation_id, limit = DEFAULT_CONVERSATION_ID, conversation_id
    safe_limit = max(1, min(int(limit), 250))
    with db_cursor() as (_, cursor):
        cursor.execute(
            f"""SELECT m.message_id, m.conversation_id, m.actor_id, a.display_name,
                a.color, m.content, m.message_type, m.trace_id, m.metadata_json, m.created_at
                FROM messages m JOIN actors a ON a.actor_id=m.actor_id
                WHERE m.conversation_id=%s
                ORDER BY m.created_at DESC LIMIT {safe_limit}""",
            (conversation_id or DEFAULT_CONVERSATION_ID,),
        )
        rows = list(cursor.fetchall())
    rows.reverse()
    for row in rows:
        row["metadata"] = json_loads(row.pop("metadata_json"), {})
        row["created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
    return rows


def insert_message(
    *, actor_id: str, content: str, trace_id: str | None = None,
    message_type: str = "text", metadata: dict[str, Any] | None = None,
    conversation_id: str = DEFAULT_CONVERSATION_ID,
) -> str:
    message_id = new_id("msg")
    with db_cursor() as (_, cursor):
        cursor.execute(
            """INSERT INTO messages
            (message_id, conversation_id, actor_id, content, message_type, trace_id, metadata_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (message_id, conversation_id or DEFAULT_CONVERSATION_ID, actor_id, content, message_type, trace_id, json_dumps(metadata or {})),
        )
    return message_id


def get_workspace(owner_id: str, revision: str | None = None, conversation_id: str = DEFAULT_CONVERSATION_ID) -> dict[str, Any]:
    """Workspace for (owner, conversation). Default 'group-main' selects
    the legacy shipped rows (workspace_<actor>, '<actor>_root'), which is
    what /demo (no conversation_id anywhere) relies on.

    A missing row raises KeyError for every conversation, including
    non-group-main ones — callers that want a blank workspace must call
    ensure_workspace(conversation_id, owner) first (endpoints do).
    """
    conv = conversation_id or DEFAULT_CONVERSATION_ID
    if revision:
        revision = validate_revision(revision)
    with db_cursor() as (_, cursor):
        if revision:
            cursor.execute(
                f"""SELECT workspace_id, owner_actor_id, conversation_id, title, created_at, updated_at
                    FROM workspaces AS OF '{revision}'
                    WHERE owner_actor_id=%s AND conversation_id=%s""",
                (owner_id, conv),
            )
            workspace = cursor.fetchone()
            if not workspace and conv == DEFAULT_CONVERSATION_ID:
                cursor.execute(
                    f"""SELECT workspace_id, owner_actor_id, conversation_id, title, created_at, updated_at
                        FROM workspaces AS OF '{revision}'
                        WHERE owner_actor_id=%s AND conversation_id IS NULL""",
                    (owner_id,),
                )
                workspace = cursor.fetchone()
        else:
            cursor.execute(
                """SELECT workspace_id, owner_actor_id, conversation_id, title, created_at, updated_at
                    FROM workspaces WHERE owner_actor_id=%s AND conversation_id=%s""",
                (owner_id, conv),
            )
            workspace = cursor.fetchone()
            if not workspace and conv == DEFAULT_CONVERSATION_ID:
                # Pre-migration safety: legacy row not yet backfilled.
                cursor.execute(
                    """SELECT workspace_id, owner_actor_id, conversation_id, title, created_at, updated_at
                        FROM workspaces WHERE owner_actor_id=%s AND conversation_id IS NULL""",
                    (owner_id,),
                )
                workspace = cursor.fetchone()
        if not workspace:
            raise KeyError(f"Unknown workspace owner: {owner_id}")
        workspace_id = workspace["workspace_id"]
        workspace_conv = workspace.get("conversation_id") or conv
        if revision:
            query = f"""SELECT node_id, parent_id, node_type, props_json, layout_json, order_index
                FROM ui_nodes AS OF '{revision}' WHERE workspace_id=%s ORDER BY order_index, node_id"""
        else:
            query = """SELECT node_id, parent_id, node_type, props_json, layout_json, order_index
                FROM ui_nodes WHERE workspace_id=%s ORDER BY order_index, node_id"""
        cursor.execute(query, (workspace_id,))
        rows = list(cursor.fetchall())
    nodes = []
    for row in rows:
        nodes.append({
            "id": row["node_id"],
            "parent_id": row["parent_id"],
            "type": row["node_type"],
            "props": json_loads(row["props_json"], {}),
            "layout": json_loads(row["layout_json"], {}),
            "order_index": row["order_index"],
        })
    return {
        "workspace_id": workspace_id,
        "owner_actor_id": workspace["owner_actor_id"],
        "conversation_id": workspace_conv,
        "title": workspace["title"],
        "nodes": build_tree(nodes),
        "flat_nodes": nodes,
        "head": revision or current_head(),
        "is_historical": bool(revision),
    }


def build_tree(flat_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []
    for node in flat_nodes:
        by_id[node["id"]] = {**node, "children": []}
    for node in flat_nodes:
        parent_id = node.get("parent_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(by_id[node["id"]])
        else:
            roots.append(by_id[node["id"]])
    for node in by_id.values():
        node["children"].sort(key=lambda child: (child.get("order_index", 0), child["id"]))
    roots.sort(key=lambda child: (child.get("order_index", 0), child["id"]))
    return roots


def list_commits(limit: int = 60) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 200))
    with db_cursor() as (_, cursor):
        cursor.execute(
            f"SELECT commit_hash, committer, email, date, message FROM dolt_log ORDER BY date DESC LIMIT {safe_limit}"
        )
        rows = list(cursor.fetchall())
    for row in rows:
        row["date"] = row["date"].isoformat() if row.get("date") else None
    return rows


def get_commit_for_message(message_id: str) -> dict[str, Any] | None:
    with db_cursor() as (_, cursor):
        cursor.execute(
            """SELECT commit_hash, commit_date FROM dolt_history_messages
            WHERE message_id=%s ORDER BY commit_date ASC LIMIT 1""",
            (message_id,),
        )
        row = cursor.fetchone()
    if row and row.get("commit_date"):
        row["commit_date"] = row["commit_date"].isoformat()
    return row


def get_trace(trace_id: str, *, include_snapshots: bool = True) -> dict[str, Any] | None:
    with db_cursor() as (_, cursor):
        cursor.execute(
            """SELECT t.*, a.display_name AS actor_name
            FROM llm_traces t JOIN actors a ON a.actor_id=t.actor_id WHERE t.trace_id=%s""",
            (trace_id,),
        )
        row = cursor.fetchone()
    if not row:
        return None
    for field in ("input_json", "output_json", "steps_json"):
        value = json_loads(row.pop(field), {})
        if include_snapshots or field == "steps_json":
            row[field.removesuffix("_json")] = value
    for field in ("created_at",):
        if row.get(field):
            row[field] = row[field].isoformat()
    # Trace rows are committed with the AI answer and any accepted UI patches.
    with db_cursor() as (_, cursor):
        cursor.execute(
            "SELECT commit_hash, commit_date FROM dolt_history_llm_traces WHERE trace_id=%s ORDER BY commit_date ASC LIMIT 1",
            (trace_id,),
        )
        history = cursor.fetchone()
    if history:
        if history.get("commit_date"):
            history["commit_date"] = history["commit_date"].isoformat()
        row["commit"] = history
    return row


def list_traces(limit: int = 60, *, include_details: bool = False) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 200))
    cols = (
        "t.trace_id, t.triggering_message_id, t.response_message_id, t.actor_id, a.display_name AS actor_name, t.workspace_id, t.model_name, t.provider, t.status, t.duration_ms, t.created_at, t.input_json, t.output_json"
        if include_details
        else "t.trace_id, t.triggering_message_id, t.response_message_id, t.actor_id, a.display_name AS actor_name, t.workspace_id, t.model_name, t.provider, t.status, t.duration_ms, t.created_at"
    )
    with db_cursor() as (_, cursor):
        cursor.execute(
            f"""SELECT {cols}
                FROM llm_traces t JOIN actors a ON a.actor_id=t.actor_id
                ORDER BY t.created_at DESC LIMIT {safe_limit}"""
        )
        rows = list(cursor.fetchall())
    for row in rows:
        row["created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
        if include_details:
            row["input"] = json_loads(row.pop("input_json", None), {})
            row["output"] = json_loads(row.pop("output_json", None), {})
    return rows


def get_message_debug(message_id: str) -> dict[str, Any] | None:
    with db_cursor() as (_, cursor):
        cursor.execute(
            """SELECT m.message_id, m.actor_id, a.display_name AS actor_name,
                m.content, m.trace_id, m.created_at
                FROM messages m JOIN actors a ON a.actor_id=m.actor_id
                WHERE m.message_id=%s""",
            (message_id,),
        )
        message = cursor.fetchone()
    if not message:
        return None
    message["created_at"] = message["created_at"].isoformat() if message.get("created_at") else None
    commit = get_commit_for_message(message_id)
    trace_id = message.get("trace_id")
    if not trace_id:
        # Human trigger messages point to the model trace through the trace table.
        with db_cursor() as (_, cursor):
            cursor.execute("SELECT trace_id FROM llm_traces WHERE triggering_message_id=%s LIMIT 1", (message_id,))
            trace_row = cursor.fetchone()
            trace_id = trace_row["trace_id"] if trace_row else None
    trace = get_trace(trace_id) if trace_id else None
    return {"message": message, "commit": commit, "trace": trace}


def get_active_prompt() -> dict[str, Any]:
    with db_cursor() as (_, cursor):
        cursor.execute(
            """SELECT version_id, prompt_text, model_name, temperature, created_by, created_at
            FROM prompt_versions WHERE is_active=1 ORDER BY created_at DESC LIMIT 1"""
        )
        row = cursor.fetchone()
    if not row:
        return {"version_id": None, "prompt_text": "", "model_name": "", "temperature": 0.2}
    row["created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
    return row


def get_prompt_version(version_id: str) -> dict[str, Any] | None:
    with db_cursor() as (_, cursor):
        cursor.execute("SELECT version_id, prompt_text, model_name, temperature, is_active, created_by, created_at FROM prompt_versions WHERE version_id=%s", (version_id,))
        row = cursor.fetchone()
    if row and row.get("created_at"):
        row["created_at"] = row["created_at"].isoformat()
    return row


def list_prompts() -> list[dict[str, Any]]:
    with db_cursor() as (_, cursor):
        cursor.execute(
            """SELECT version_id, model_name, temperature, is_active, created_by, created_at
            FROM prompt_versions ORDER BY created_at DESC LIMIT 50"""
        )
        rows = list(cursor.fetchall())
    for row in rows:
        row["created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
    return rows


def save_prompt(prompt_text: str, model_name: str, temperature: float, created_by: str) -> tuple[dict[str, Any], str | None]:
    version_id = new_id("prompt")
    with WRITE_LOCK:
        with db_cursor() as (_, cursor):
            cursor.execute("UPDATE prompt_versions SET is_active=0 WHERE is_active=1")
            cursor.execute(
                """INSERT INTO prompt_versions
                (version_id, prompt_text, model_name, temperature, is_active, created_by)
                VALUES (%s, %s, %s, %s, 1, %s)""",
                (version_id, prompt_text, model_name, temperature, created_by),
            )
        commit_hash = commit_all(f"debug: activate prompt {version_id}", f"{created_by.title()} <{created_by}@malleable.demo>")
    return get_active_prompt(), commit_hash


def add_trace(
    *, trace_id: str, triggering_message_id: str, response_message_id: str | None,
    actor_id: str, workspace_id: str, prompt_version_id: str | None,
    model_name: str, provider: str, status: str, duration_ms: int,
    input_snapshot: dict[str, Any], output_snapshot: dict[str, Any], steps: list[dict[str, Any]],
) -> None:
    with db_cursor() as (_, cursor):
        cursor.execute(
            """INSERT INTO llm_traces
            (trace_id, triggering_message_id, response_message_id, actor_id, workspace_id,
             prompt_version_id, model_name, provider, status, duration_ms,
             input_json, output_json, steps_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                trace_id, triggering_message_id, response_message_id, actor_id, workspace_id,
                prompt_version_id, model_name, provider, status, duration_ms,
                json_dumps(input_snapshot), json_dumps(output_snapshot), json_dumps(steps),
            ),
        )


def insert_change_event(workspace_id: str, actor_id: str, summary: str, patches: list[dict[str, Any]]) -> str:
    event_id = new_id("event")
    with db_cursor() as (_, cursor):
        cursor.execute(
            """INSERT INTO ui_change_events (event_id, workspace_id, actor_id, summary, patch_json)
            VALUES (%s, %s, %s, %s, %s)""",
            (event_id, workspace_id, actor_id, summary[:255], json_dumps(patches)),
        )
    return event_id


def push_to_dolthub(remote: str) -> dict[str, Any]:
    """Configure origin and push main. Auth is supplied by Dolt's credential store."""
    if not remote or len(remote) > 200:
        raise ValueError("Set DOLTHUB_REMOTE to an org/repo remote first")
    if not all(part and part.replace("-", "").replace("_", "").isalnum() for part in remote.split("/")) or len(remote.split("/")) != 2:
        raise ValueError("DOLTHUB_REMOTE must be in org/repo form")
    with WRITE_LOCK, db_cursor() as (_, cursor):
        cursor.execute("SELECT COUNT(*) AS n FROM dolt_remotes WHERE name='origin'")
        exists = int(cursor.fetchone()["n"]) > 0
        if not exists:
            cursor.execute("CALL DOLT_REMOTE('add', 'origin', %s)", (remote,))
            while cursor.nextset():
                pass
        cursor.execute("CALL DOLT_PUSH('origin', 'main')")
        result = cursor.fetchone()
        while cursor.nextset():
            pass
    return {"remote": remote, "result": result or {"status": "push command completed"}}
