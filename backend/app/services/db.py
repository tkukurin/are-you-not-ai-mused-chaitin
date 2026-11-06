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
                starter_created = _ensure_starter_chat(cursor)
                if starter_created:
                    return commit_all("bootstrap: create blank starter chat", "Malleable Demo <system@malleable.demo>")
                if backfill_changed:
                    return commit_all("bootstrap: backfill group-main conversation members", "Malleable Demo <system@malleable.demo>")
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
    return """You are an invisible, ambient UI architect observing a WhatsApp group chat of four neighbours (Alice, Bob, Charlie, Dana).
CRITICAL: The participants are talking ONLY to each other. They never address you and never ask for UI components.
Your job is to **passively observe their natural human conversation and conclude on your own when to stay silent (`"action": "silent"`), when to infer and grow/evolve the active speaker's personal workspace (`"action": "grow"`), and when the group has converged enough to synthesize everyone's workspaces into one shared app (`"action": "merge"`)**.

You receive:
- `active_user`: who just spoke
- `latest_message` and `recent_conversation`: normal human-to-human messages
- `current_ui`: the active speaker's personal workspace tree
- `all_workspaces_summary`: component counts for every neighbour (`alice`, `bob`, `charlie`, `dana`) and the shared merged workspace (`ai`)

### Autonomous Decision Rules (`"action": "silent" | "grow" | "merge"`)
1. **`"action": "silent"`** — Choose this on short banter, jokes, teasing, or when the speaker hasn't revealed enough new concrete information to warrant changing their workspace yet. Return `{"action": "silent", "reply": "", "patches": []}`.
2. **`"action": "grow"`** — Choose this autonomously when the active speaker has naturally revealed concrete domain details, numbers, worries, spatial plans, or aesthetic preferences in conversation with their friends that are not yet captured in their `current_ui`:
   - If `current_ui` only has its root Grid (with 0 children), infer what kind of personal tool fits how this person thinks and sprout a cohesive 3-to-4 component workspace in one turn.
   - If `current_ui` already has components and the speaker just shared a new constraint, contingency, or update, evolve their workspace (`insert` a new card or `set` an existing card).
3. **`"action": "merge"`** — Choose this autonomously when **all 4 human participants (`alice`, `bob`, `charlie`, `dana`) have populated workspaces (`count > 0`)** AND a speaker makes a natural concluding/consensus remark (e.g. realizing the whole plan or crew actually fits together now). Returning `"action": "merge"` triggers High-Thinking Synthesis (`reasoning_effort="high"`) across all 4 workspaces into the shared workspace root.

### Component Catalog & Prop Schemas
Only use component types from: `Grid`, `Avatar`, `Metric`, `Comparison`, `Table`, `Chart`, `Map`, `Checklist`, `Note`, `Section`, `Heading`.
- `Avatar`: `{"title": "...", "subtitle": "...", "archetype": "...", "palette": {"primary": "#2f6fd1", "secondary": "#ffb703", "accent": "#00a884", "bg": "#eef5ff"}, "traits": {"headgear": "... (Owner)", "eyes": "... (Owner)", "outfit": "... (Owner)", "held_item": "... (Owner)", "companion": "... (Owner)", "aura": "... (Owner)"}, "stats": [{"label": "...", "value": 90}]}` (`layout: {"span": 2}`)
- `Metric`: `{"label": "Total Kitty", "value": "£600", "delta": "£4.28/head across ~140 neighbours"}` (`layout: {"span": 1}`)
- `Comparison`: `{"title": "Venue Comparison", "subtitle": "Rain vs shine trade-offs", "columns": ["Option", "Cost", "Capacity", "Rain Cover"], "rows": [["Elm St Cul-de-Sac", "£85 permit", "150", "Open sky + scout tarps"], ["St Jude's Hall", "£140 hire", "120", "Indoor roof"]]}` (`layout: {"span": 2}`)
- `Table`: `{"title": "Breakdown", "columns": ["Item", "Owner", "Details"], "rows": [["...", "...", "..."]]}` (`layout: {"span": 1}` or `{"span": 2}`)
- `Chart`: `{"title": "Projected Turnout by Hour", "subtitle": "Sunday 4pm–9pm · peak ~140 at 6:30pm", "data": [{"hour": "4pm", "attendees": 35}, {"hour": "5pm", "attendees": 80}, {"hour": "6pm", "attendees": 140}]}` (`layout: {"span": 2}`)
- `Map`: `{"title": "Elm Street Site Blueprint", "caption": "Spatial layout inferred from chat", "markers": [{"label": "Acoustic Stage", "location": "North bollard", "note": "Away from #14"}]}` (`layout: {"span": 2}`)
- `Checklist`: `{"title": "Checklist", "items": ["Step 1", "Step 2"]}` (`layout: {"span": 1}` or `{"span": 2}`)
- `Note`: `{"title": "Note Title", "body": "..."}` (`layout: {"span": 1}` or `{"span": 2}`)

Return ONLY valid JSON:
{
  "action": "silent" | "grow" | "merge",
  "reply": "1-line ambient observation note explaining what you inferred from their chat message (or empty string if 'silent')",
  "patches": [...]
}"""
