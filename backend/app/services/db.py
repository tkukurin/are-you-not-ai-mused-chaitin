from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import contextmanager
from threading import RLock
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from app.config import settings


# Dolt has one working set per checked-out branch. Serialize demo writes so two
# requests cannot accidentally stage each other's changes into one commit.
WRITE_LOCK = RLock()
# Version id of the prompt row _ensure_active_prompt() keeps active. Shipping
# a new default_system_prompt() should bump this and add a fresh row (legacy
# rows stay as history).
ACTIVE_PROMPT_VERSION = "prompt_v2_autonomous"
_HASH_RE = re.compile(r"^[0-9a-z]{20,64}$")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS actors (
        actor_id VARCHAR(64) PRIMARY KEY,
        display_name VARCHAR(80) NOT NULL,
        actor_type VARCHAR(16) NOT NULL,
        color VARCHAR(24) NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    )""",
    """CREATE TABLE IF NOT EXISTS conversations (
        conversation_id VARCHAR(64) PRIMARY KEY,
        title VARCHAR(200) NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    )""",
    """CREATE TABLE IF NOT EXISTS conversation_members (
        conversation_id VARCHAR(64) NOT NULL,
        actor_id VARCHAR(64) NOT NULL,
        joined_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (conversation_id, actor_id),
        INDEX idx_conv_members_actor (actor_id)
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        message_id VARCHAR(64) PRIMARY KEY,
        conversation_id VARCHAR(64) NOT NULL,
        actor_id VARCHAR(64) NOT NULL,
        content LONGTEXT NOT NULL,
        message_type VARCHAR(24) NOT NULL DEFAULT 'text',
        trace_id VARCHAR(64) NULL,
        metadata_json LONGTEXT NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        INDEX idx_messages_created (created_at),
        INDEX idx_messages_trace (trace_id)
    )""",
    """CREATE TABLE IF NOT EXISTS workspaces (
        workspace_id VARCHAR(64) PRIMARY KEY,
        owner_actor_id VARCHAR(64) NOT NULL,
        conversation_id VARCHAR(64) NULL,
        title VARCHAR(160) NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        INDEX idx_workspaces_owner_conv (owner_actor_id, conversation_id)
    )""",
    """CREATE TABLE IF NOT EXISTS ui_nodes (
        node_id VARCHAR(96) PRIMARY KEY,
        workspace_id VARCHAR(64) NOT NULL,
        parent_id VARCHAR(96) NULL,
        node_type VARCHAR(48) NOT NULL,
        props_json LONGTEXT NOT NULL,
        layout_json LONGTEXT NOT NULL,
        order_index INT NOT NULL DEFAULT 0,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        INDEX idx_nodes_workspace (workspace_id),
        INDEX idx_nodes_parent (workspace_id, parent_id)
    )""",
    """CREATE TABLE IF NOT EXISTS ui_change_events (
        event_id VARCHAR(64) PRIMARY KEY,
        workspace_id VARCHAR(64) NOT NULL,
        actor_id VARCHAR(64) NOT NULL,
        summary VARCHAR(255) NOT NULL,
        patch_json LONGTEXT NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    )""",
    """CREATE TABLE IF NOT EXISTS prompt_versions (
        version_id VARCHAR(64) PRIMARY KEY,
        prompt_text LONGTEXT NOT NULL,
        model_name VARCHAR(120) NOT NULL,
        temperature DOUBLE NOT NULL DEFAULT 0.2,
        is_active TINYINT(1) NOT NULL DEFAULT 0,
        created_by VARCHAR(64) NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        INDEX idx_prompt_active (is_active, created_at)
    )""",
    """CREATE TABLE IF NOT EXISTS llm_traces (
        trace_id VARCHAR(64) PRIMARY KEY,
        triggering_message_id VARCHAR(64) NOT NULL,
        response_message_id VARCHAR(64) NULL,
        actor_id VARCHAR(64) NOT NULL,
        workspace_id VARCHAR(64) NOT NULL,
        prompt_version_id VARCHAR(64) NULL,
        model_name VARCHAR(120) NOT NULL,
        provider VARCHAR(32) NOT NULL,
        status VARCHAR(24) NOT NULL,
        duration_ms INT NOT NULL DEFAULT 0,
        input_json LONGTEXT NOT NULL,
        output_json LONGTEXT NOT NULL,
        steps_json LONGTEXT NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        INDEX idx_traces_created (created_at),
        INDEX idx_traces_message (triggering_message_id),
        INDEX idx_traces_response (response_message_id)
    )""",
    """CREATE TABLE IF NOT EXISTS app_settings (
        setting_key VARCHAR(120) PRIMARY KEY,
        value_json LONGTEXT NOT NULL,
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    )""",
]


def connect(*, database: bool = True):
    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            return pymysql.connect(
                host=settings.dolt_host,
                port=settings.dolt_port,
                user=settings.dolt_user,
                password=settings.dolt_password,
                database=settings.dolt_database if database else None,
                charset="utf8mb4",
                cursorclass=DictCursor,
                autocommit=True,
                connect_timeout=5,
                read_timeout=30,
                write_timeout=30,
            )
        except pymysql.err.OperationalError as exc:
            last_exc = exc
            if attempt < 3:
                time.sleep(0.35 * (attempt + 1))
                continue
            raise
    raise last_exc  # type: ignore[misc]


@contextmanager
def db_cursor(*, database: bool = True) -> Iterator[tuple[Any, Any]]:
    conn = connect(database=database)
    try:
        with conn.cursor() as cursor:
            yield conn, cursor
    finally:
        conn.close()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def json_loads(value: Any, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def commit_all(message: str, author: str | None = None) -> str | None:
    """Create a Dolt commit for all staged/working-set changes in this demo DB."""
    with db_cursor() as (_, cursor):
        if author:
            cursor.execute("CALL DOLT_COMMIT('-A', '--allow-empty', '-m', %s, '--author', %s)", (message[:240], author[:180]))
        else:
            cursor.execute("CALL DOLT_COMMIT('-A', '--allow-empty', '-m', %s)", (message[:240],))
        row = cursor.fetchone()
        # Drain any additional result sets returned by the stored procedure.
        while cursor.nextset():
            pass
        return str(row["hash"]) if row and row.get("hash") else None


def reset_working_set() -> None:
    """Discard uncommitted working-set changes so a failed write never leaks into the next commit."""
    try:
        with db_cursor() as (_, cursor):
            cursor.execute("CALL DOLT_RESET('--hard')")
            while cursor.nextset():
                pass
    except Exception:
        pass


def current_head() -> str | None:
    with db_cursor() as (_, cursor):
        cursor.execute("SELECT HASHOF('HEAD') AS hash")
        row = cursor.fetchone()
        return str(row["hash"]) if row and row.get("hash") else None


def validate_revision(revision: str) -> str:
    if not _HASH_RE.fullmatch(revision):
        raise ValueError("Invalid Dolt commit hash")
    return revision


def wait_for_dolt(retries: int = 40, delay: float = 2.0) -> None:
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            with db_cursor(database=False) as (_, cursor):
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return
        except Exception as exc:  # startup retry while the local dolt sql-server comes up
            last_error = exc
            time.sleep(delay)
    raise RuntimeError(f"Could not connect to Dolt at {settings.dolt_host}:{settings.dolt_port}: {last_error}")


STARTER_CONVERSATION_ID = "starter-chat"
STARTER_TITLE = "My first group chat"
STARTER_SEED_MESSAGE = "Welcome! Pick who you're speaking as, then ask the model to build something — e.g. 'Make a checklist for Saturday'. Your workspaces start blank here."


def _per_conversation_workspace_id(conversation_id: str, owner_id: str) -> str:
    candidate = f"ws_{conversation_id}_{owner_id}"
    if len(candidate) <= 64:
        return candidate
    return f"ws_{hashlib.sha1(f'{conversation_id}:{owner_id}'.encode()).hexdigest()}"


def _ensure_active_prompt(cursor: Any) -> bool:
    """Ensure the active prompt is the current autonomous default.

    Shipped DBs may pin an old active prompt (e.g. prompt_street-feast)
    that predates the silent/grow/merge decision contract, so the live
    model could never merge on its own. If the active prompt text differs
    from default_system_prompt(), insert/refresh the ACTIVE_PROMPT_VERSION
    row as the sole active prompt (legacy rows are kept as history).
    Idempotent: returns False (no writes) when already current.
    """
    desired = default_system_prompt()
    cursor.execute(
        "SELECT prompt_text FROM prompt_versions WHERE is_active=1 ORDER BY created_at DESC LIMIT 1"
    )
    active = cursor.fetchone()
    if active and str(active.get("prompt_text") or "") == desired:
        return False
    cursor.execute("UPDATE prompt_versions SET is_active=0 WHERE is_active=1")
    cursor.execute(
        """INSERT INTO prompt_versions
        (version_id, prompt_text, model_name, temperature, is_active, created_by)
        VALUES (%s, %s, %s, 0.2, 1, 'ai')
        ON DUPLICATE KEY UPDATE prompt_text=VALUES(prompt_text), model_name=VALUES(model_name), temperature=VALUES(temperature), is_active=1, created_by='ai'""",
        (ACTIVE_PROMPT_VERSION, desired, settings.local_model),
    )
    return True


def _ensure_starter_chat(cursor: Any) -> bool:
    """Create the blank starter conversation once (idempotent).

    Members are ai + all current human actors; every member (incl. ai)
    gets an EMPTY per-conversation workspace (root Grid only), so the
    default app opens blank instead of leaking group-main content.
    Returns True when this call created the starter chat.
    """
    cursor.execute(
        "SELECT COUNT(*) AS n FROM conversations WHERE conversation_id=%s",
        (STARTER_CONVERSATION_ID,),
    )
    if int(cursor.fetchone()["n"]):
        return False
    cursor.execute("SELECT actor_id, display_name, actor_type FROM actors")
    actors = list(cursor.fetchall())
    member_actors = [a for a in actors if a["actor_id"] == "ai"] + [
        a for a in actors if a["actor_type"] == "human"
    ]
    if not member_actors:
        return False
    cursor.execute(
        "INSERT INTO conversations (conversation_id, title) VALUES (%s, %s)",
        (STARTER_CONVERSATION_ID, STARTER_TITLE),
    )
    cursor.executemany(
        "INSERT IGNORE INTO conversation_members (conversation_id, actor_id) VALUES (%s, %s)",
        [(STARTER_CONVERSATION_ID, str(a["actor_id"])) for a in member_actors],
    )
    cursor.execute(
        """INSERT INTO messages
        (message_id, conversation_id, actor_id, content, message_type, trace_id, metadata_json)
        VALUES (%s, %s, 'ai', %s, 'text', NULL, '{}')""",
        ("msg_seed_starter_chat", STARTER_CONVERSATION_ID, STARTER_SEED_MESSAGE),
    )
    for actor in member_actors:
        owner_id = str(actor["actor_id"])
        title = "Shared workspace" if owner_id == "ai" else f"{actor['display_name']}'s workspace"
        workspace_id = _per_conversation_workspace_id(STARTER_CONVERSATION_ID, owner_id)
        cursor.execute(
            "INSERT IGNORE INTO workspaces (workspace_id, owner_actor_id, conversation_id, title) VALUES (%s, %s, %s, %s)",
            (workspace_id, owner_id, STARTER_CONVERSATION_ID, title),
        )
        cursor.executemany(
            """INSERT IGNORE INTO ui_nodes
            (node_id, workspace_id, parent_id, node_type, props_json, layout_json, order_index)
            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            seed_nodes(owner_id, workspace_id, root_node_id=f"{workspace_id}_root", title=title),
        )
    return True


def initialize_database() -> str | None:
    # DOLT_DATABASE in the official SQL-server image creates this database.
    # CREATE DATABASE IF NOT EXISTS also makes local/manual setups friendlier.
    with db_cursor(database=False) as (_, cursor):
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{settings.dolt_database}`")
    with db_cursor() as (_, cursor):
        for statement in SCHEMA:
            cursor.execute(statement)

    with WRITE_LOCK:
        # Idempotent migration: per-conversation workspaces. Existing rows
        # are the shipped Street Feast (group-main) workspaces, so backfill
        # them to 'group-main' to preserve the /demo replay.
        migration_changed = 0
        with db_cursor() as (_, cursor):
            cursor.execute(
                """SELECT COUNT(*) AS n FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='workspaces' AND COLUMN_NAME='conversation_id'"""
            )
            if int(cursor.fetchone()["n"]) == 0:
                try:
                    cursor.execute("ALTER TABLE workspaces ADD COLUMN conversation_id VARCHAR(64) NULL")
                    cursor.execute("CREATE INDEX idx_workspaces_owner_conv ON workspaces (owner_actor_id, conversation_id)")
                    migration_changed += 1
                except Exception:
                    # Column appeared concurrently / unsupported ALTER form.
                    cursor.execute(
                        """SELECT COUNT(*) AS n FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='workspaces' AND COLUMN_NAME='conversation_id'"""
                    )
                    if int(cursor.fetchone()["n"]) == 0:
                        raise
            cursor.execute(
                """SELECT COUNT(*) AS n FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='workspaces' AND INDEX_NAME='idx_workspaces_owner_conv'"""
            )
            if int(cursor.fetchone()["n"]) == 0:
                try:
                    cursor.execute("CREATE INDEX idx_workspaces_owner_conv ON workspaces (owner_actor_id, conversation_id)")
                except Exception:
                    pass
            cursor.execute("UPDATE workspaces SET conversation_id='group-main' WHERE conversation_id IS NULL")
            migration_changed += int(cursor.rowcount or 0)
        # Backfill the shipped demo conversation/memberships on every boot.
        # This must run before the existing-DB early return below so a
        # shipped Street Feast DB (actors already present) gains members.
        backfill_changed = migration_changed
        with db_cursor() as (_, cursor):
            cursor.execute(
                "INSERT IGNORE INTO conversations (conversation_id, title) VALUES (%s, %s)",
                ("group-main", "The group chat"),
            )
            backfill_changed += int(cursor.rowcount or 0)
            cursor.executemany(
                "INSERT IGNORE INTO conversation_members (conversation_id, actor_id) VALUES (%s, %s)",
                [("group-main", actor_id) for actor_id in ("ai", "alice", "bob", "charlie", "dana")],
            )
            backfill_changed += int(cursor.rowcount or 0)
        with db_cursor() as (_, cursor):
            cursor.execute("SELECT COUNT(*) AS n FROM actors")
            count = int(cursor.fetchone()["n"])
            if count:
                prompt_changed = _ensure_active_prompt(cursor)
                starter_created = _ensure_starter_chat(cursor)
                if starter_created or prompt_changed or backfill_changed:
                    if prompt_changed:
                        commit_message = "bootstrap: activate autonomous prompt v2"
                    elif starter_created:
                        commit_message = "bootstrap: create blank starter chat"
                    else:
                        commit_message = "bootstrap: backfill group-main conversation members"
                    return commit_all(commit_message, "Malleable Demo <system@malleable.demo>")
                return None
            cursor.executemany(
                "INSERT INTO actors (actor_id, display_name, actor_type, color) VALUES (%s, %s, %s, %s)",
                [
                    ("ai", "AI", "ai", "violet"),
                    ("alice", "Alice", "human", "blue"),
                    ("bob", "Bob", "human", "amber"),
                    ("charlie", "Charlie", "human", "green"),
                    ("dana", "Dana", "human", "pink"),
                ],
            )
            cursor.execute(
                "INSERT IGNORE INTO conversations (conversation_id, title) VALUES (%s, %s)",
                ("group-main", "The group chat"),
            )
            cursor.executemany(
                "INSERT IGNORE INTO conversation_members (conversation_id, actor_id) VALUES (%s, %s)",
                [("group-main", actor_id) for actor_id in ("ai", "alice", "bob", "charlie", "dana")],
            )
            for actor_id, title in [
                ("ai", "AI workspace"),
                ("alice", "Alice's workspace"),
                ("bob", "Bob's workspace"),
                ("charlie", "Charlie's workspace"),
                ("dana", "Dana's workspace"),
            ]:
                workspace_id = f"workspace_{actor_id}"
                cursor.execute(
                    "INSERT INTO workspaces (workspace_id, owner_actor_id, conversation_id, title) VALUES (%s, %s, 'group-main', %s)",
                    (workspace_id, actor_id, title),
                )
                nodes = seed_nodes(actor_id, workspace_id)
                cursor.executemany(
                    """INSERT INTO ui_nodes
                    (node_id, workspace_id, parent_id, node_type, props_json, layout_json, order_index)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    nodes,
                )
            messages = [
                ("msg_seed_ai", "group-main", "ai", "Group chat ready — talk through your idea together, and each neighbour's personal workspace will grow from the conversation.", "text", None, "{}"),
            ]
            cursor.executemany(
                """INSERT INTO messages
                (message_id, conversation_id, actor_id, content, message_type, trace_id, metadata_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                messages,
            )
            prompt = default_system_prompt()
            cursor.execute(
                """INSERT INTO prompt_versions
                (version_id, prompt_text, model_name, temperature, is_active, created_by)
                VALUES (%s, %s, %s, %s, 1, %s)""",
                ("prompt_initial", prompt, settings.local_model, 0.2, "ai"),
            )
            _ensure_starter_chat(cursor)
        return commit_all("bootstrap: initialize malleable UI demo", "Malleable Demo <system@malleable.demo>")


def seed_nodes(actor_id: str, workspace_id: str, root_node_id: str | None = None, title: str | None = None, *, root_id: str | None = None) -> list[tuple[Any, ...]]:
    if root_node_id is None:
        root_node_id = root_id
    if title is None:
        if actor_id == "ai":
            title = "AI's workspace"
        else:
            title = f"{actor_id.title()}'s workspace"
    # Start each workspace as a clean root Grid seed so every visible component
    # is genuinely grown from the group conversation rather than pre-filled boilerplate.
    # ui_nodes PK is node_id globally, so per-conversation workspaces MUST pass a
    # unique root_id (legacy group-main rows keep '<actor>_root' / 'ai_root').
    nodes = [
        (root_node_id or f"{actor_id}_root", workspace_id, None, "Grid", {"columns": 2, "gap": "14px", "title": title}, {"width": "100%"}, 0),
    ]
    return [
        (node_id, ws_id, parent_id, node_type, json_dumps(props), json_dumps(layout), order_index)
        for node_id, ws_id, parent_id, node_type, props, layout, order_index in nodes
    ]


def default_system_prompt() -> str:
    return """You are an invisible, ambient UI architect observing a group chat. The group can be anyone — friends, neighbours, teammates, family, a book club, a project crew — talking about any topic. Do not assume a particular group, topic, or cast of characters; use only the names, details, and plans the conversation itself gives you.

CRITICAL: The participants are talking ONLY to each other. They never address you and never ask for UI components. You must not wait for instructions or commands. Passively observe their natural human conversation and decide ON YOUR OWN, for each new message, which one of three actions to take:
- "silent": stay out of the way — nothing changes.
- "grow": grow or evolve the active speaker's personal workspace.
- "merge": synthesize everyone's personal workspaces into the one shared workspace.

You receive:
- `active_user`: display name of who just spoke (the active speaker).
- `latest_message`: the speaker's newest message.
- `recent_conversation`: recent human-to-human messages, oldest to newest.
- `current_ui`: the active speaker's personal workspace tree (their root Grid plus any components already there).
- `workspace_root_id`: the node id of the root Grid of the ACTIVE SPEAKER's workspace in THIS conversation. The root id varies per chat — never hardcode a root id, and never reuse a root id from an example. Unless a patch targets an existing card, EVERY insert patch MUST use `workspace_root_id` as its `parent_id`.
- `all_workspaces_summary`: component counts (number of cards, root Grid excluded) keyed by actor id for every member of this conversation, including `ai` (the shared workspace that only a merge writes to). A count of 0 means that member's workspace is still just a blank root Grid.

### Autonomous Decision Rules ("action": "silent" | "grow" | "merge")

1. "silent" — Choose this for banter, jokes, teasing, greetings, reactions, short acknowledgements, or any message that reveals no new concrete information worth capturing. Most casual chat is silent. Return exactly {"action": "silent", "reply": "", "patches": []}.

2. "grow" — Choose this when the active speaker has revealed concrete details that are not yet captured in their `current_ui`: facts, numbers, dates, quantities, costs, places, options being weighed, worries, constraints, contingencies, preferences, decisions, or tasks they own. Grow THE SPEAKER's workspace (the owner of `current_ui`), not anyone else's:
   - If `current_ui` only has its root Grid (0 children), infer what kind of personal tool fits how this person is thinking about the topic and sprout a cohesive set of 3-4 components in one turn that captures what they have actually said so far.
   - If `current_ui` already has components and the speaker shared a new detail, constraint, or update, evolve it: `insert` a new card, or `set` the props of an existing card when the new message changes what that card should say. Prefer few, high-value patches over many small ones, and do not duplicate information already shown.
   - Judge buildability from content, not phrasing: a plain statement of fact between friends ("we need the hall by 6, it seats 120") is just as buildable as an explicit request. Only the speaker's own revealed details go into their workspace.

3. "merge" — Choose this ONLY when BOTH conditions hold:
   (a) EVERY human member in `all_workspaces_summary` has at least 1 component (count >= 1; ignore the `ai` entry), i.e. no one's workspace is still blank, and
   (b) the conversation has reached a natural wrap-up: a consensus, an agreed decision, a "we have a plan" moment, or closing remarks that pull the whole thing together (e.g. someone summing up what was decided, confirming the final arrangement, or saying it all fits together now).
   Return {"action": "merge", "reply": "<1-line note on what the group settled>", "patches": []} — when you return "merge", the backend itself runs a separate high-thinking synthesis across all workspaces into the shared `ai` workspace; your patches are ignored, so always send an empty patches list. Do not choose merge early, mid-discussion, or as a way to summarize one person's news: until every human has cards, keep growing instead.

### Patch Format
- insert: {"op": "insert", "parent_id": "<workspace_root_id>", "node": {"id": "<unique_snake_id>", "type": "<catalog type>", "props": {...}, "layout": {"span": 1|2}, "order_index": <int>}}
- set: {"op": "set", "id": "<existing node id from current_ui>", "props": {...}, "layout": {"span": 1|2}} — use only to update an existing card; send the card's full corrected props.
- Node ids must be unique within the workspace and stable: lowercase letters, digits, "_" or "-" only.
- Never invent `parent_id` values: inserts go under `workspace_root_id` (or under a Section you just inserted).

### Component Catalog & Prop Schemas
Only use component types from: Grid, Avatar, Metric, Comparison, Table, Chart, Map, Checklist, Note, Section, Heading. (`Grid` is the root container — never insert a Grid; it already exists as the workspace root.)
- Avatar: {"title": "...", "subtitle": "...", "archetype": "...", "palette": {"primary": "#2f6fd1", "secondary": "#ffb703", "accent": "#00a884", "bg": "#eef5ff"}, "traits": {"headgear": "...", "eyes": "...", "outfit": "...", "held_item": "...", "companion": "...", "aura": "..."}, "stats": [{"label": "...", "value": 90}]} (layout: {"span": 2}) — a persona/identity card for the workspace owner; fill traits/stats only from what they have actually revealed about themselves or their role.
- Metric: {"label": "Total budget", "value": "$600", "delta": "$25 per person across 24 people"} (layout: {"span": 1})
- Comparison: {"title": "Venue comparison", "subtitle": "Cost vs capacity trade-offs", "columns": ["Option", "Cost", "Capacity", "Notes"], "rows": [["Community hall", "$140 hire", "120", "Indoor, rain-safe"], ["Riverside pavilion", "$85 permit", "150", "Open sky"]]} (layout: {"span": 2})
- Table: {"title": "Who brings what", "columns": ["Item", "Owner", "Details"], "rows": [["Cups for 60", "Sam", "Compostable"], ["Extension lead", "Priya", "10 m, from the garage"]]} (layout: {"span": 1} or {"span": 2})
- Chart: {"title": "Expected turnout by hour", "subtitle": "Peak around 6pm", "data": [{"hour": "4pm", "attendees": 35}, {"hour": "5pm", "attendees": 80}, {"hour": "6pm", "attendees": 120}]} (layout: {"span": 2}) — data points are objects with one text label key and one numeric value key; keep 3-8 points, numbers only from the conversation.
- Map: {"title": "Meeting points", "caption": "Places mentioned so far", "markers": [{"label": "Main gate", "location": "North entrance", "note": "Meet here at 5pm"}]} (layout: {"span": 2})
- Checklist: {"title": "Sam's next steps", "items": ["Book the hall", "Confirm the deposit"]} (layout: {"span": 1} or {"span": 2})
- Note: {"title": "Rain plan", "body": "If it pours, move everything under the pavilion roof."} (layout: {"span": 1} or {"span": 2})
- Section: {"title": "Logistics"} (layout: {"span": 2}) — a grouping container; insert children with parent_id set to the Section's own id.
- Heading: {"text": "Saturday plan", "subtitle": "Agreed by the group"} (layout: {"span": 2})

Use only facts, names, numbers, and preferences actually present in the conversation — never invent details to fill a card. Match component types to the shape of the information (quantities -> Metric/Chart, options -> Comparison, ownership/tasks -> Table/Checklist, places -> Map, narrative context -> Note).

Return ONLY valid JSON (no markdown, no commentary outside the JSON):
{
  "action": "silent" | "grow" | "merge",
  "reply": "1-line ambient observation note explaining what you inferred from their chat message (empty string if 'silent'; for 'merge', 1 line on what the group settled)",
  "patches": [...]
}"""
