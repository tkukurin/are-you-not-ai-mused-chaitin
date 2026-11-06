from __future__ import annotations

import hashlib
import re
from typing import Any

from app.services.db import db_cursor, json_dumps, json_loads
from app.services.repository import build_tree, get_workspace

ALLOWED_COMPONENTS = {
    "Grid", "Section", "Heading", "Note", "Metric", "Chart", "Table",
    "Map", "Comparison", "Checklist", "Avatar",
}
_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,96}$")


class PatchError(ValueError):
    pass


# Aliases the model actually emits (measured): `update` is intended as
# `set`, `delete` as `remove`, etc. We also harvest ids that the model
# hides in nodeId/node_id/node/target_id before validating strictly.
_OP_ALIASES = {
    "update": "set", "edit": "set", "modify": "set", "change": "set",
    "delete": "remove", "drop": "remove",
    "add": "insert", "create": "insert",
    "move": "move", "reorder": "move",
}
_ID_KEYS = ("id", "nodeId", "node_id", "target_id", "node")


def _coerce_patch(patch: dict[str, Any], existing_nodes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    p = dict(patch)
    raw_op = str(p.get("op") or "").lower()
    op = _OP_ALIASES.get(raw_op, raw_op)
    if op in {"remove", "move", "set"} and not p.get("id"):
        for key in _ID_KEYS[1:]:
            candidate = p.get(key)
            if isinstance(candidate, dict):
                candidate = candidate.get("id")
            if candidate and isinstance(candidate, str):
                p["id"] = candidate
                break
    # The model often nests id/order under node for move/set too.
    node = p.get("node")
    if isinstance(node, dict):
        if not p.get("id") and node.get("id"):
            p["id"] = node["id"]
        if op == "move" and p.get("order_index") is None and node.get("order_index") is not None:
            p["order_index"] = node["order_index"]
    # If id is still missing for remove/move/set, try matching by title or component type in existing_nodes.
    if op in {"remove", "move", "set"} and not p.get("id") and existing_nodes:
        target_type = p.get("type") or (node.get("type") if isinstance(node, dict) else None)
        target_title = (
            p.get("title")
            or (p.get("props", {}).get("title") if isinstance(p.get("props"), dict) else None)
            or (node.get("props", {}).get("title") if isinstance(node, dict) and isinstance(node.get("props"), dict) else None)
        )
        non_root = [n for n in existing_nodes if n.get("parent_id") is not None]
        if target_title and isinstance(target_title, str):
            matches = [n for n in non_root if str(n.get("props", {}).get("title", "")).lower() == target_title.lower()]
            if matches:
                p["id"] = matches[-1]["id"]
        if not p.get("id") and target_type and isinstance(target_type, str):
            matches = [n for n in non_root if str(n.get("type", "")).lower() == target_type.lower()]
            if matches:
                p["id"] = matches[-1]["id"]
    p["op"] = op
    return p


def normalize_patch(patch: dict[str, Any], existing_nodes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise PatchError("Each patch must be an object")
    patch = _coerce_patch(patch, existing_nodes=existing_nodes)
    op = patch.get("op")
    if op not in {"insert", "remove", "move", "set"}:
        raise PatchError(f"Unsupported patch operation: {op}")
    normalized = dict(patch)
    if op == "insert":
        node = patch.get("node") or {}
        node_id = str(node.get("id", ""))
        node_type = node.get("type")
        if not _ID_RE.fullmatch(node_id):
            raise PatchError("Inserted node needs a stable id (letters, numbers, _ or -)")
        if node_type not in ALLOWED_COMPONENTS:
            raise PatchError(f"Component type '{node_type}' is not in the catalog")
        if not isinstance(node.get("props", {}), dict) or not isinstance(node.get("layout", {}), dict):
            raise PatchError("Node props and layout must be objects")
        normalized["node"] = {
            "id": node_id,
            "type": node_type,
            "props": node.get("props", {}),
            "layout": node.get("layout", {}),
            "order_index": int(node.get("order_index", 999)),
        }
        parent_id = patch.get("parent_id")
        if parent_id is not None and (not isinstance(parent_id, str) or not _ID_RE.fullmatch(parent_id)):
            raise PatchError("parent_id must be null or a valid node id")
        normalized["parent_id"] = parent_id
    elif op in {"remove", "move", "set"}:
        node_id = str(patch.get("id", ""))
        if not _ID_RE.fullmatch(node_id):
            raise PatchError(f"{op} patch needs a valid node id")
        normalized["id"] = node_id
        if op == "move":
            parent_id = patch.get("parent_id")
            if parent_id is not None and (not isinstance(parent_id, str) or not _ID_RE.fullmatch(parent_id)):
                raise PatchError("parent_id must be null or a valid node id")
            normalized["parent_id"] = parent_id
            normalized["order_index"] = int(patch.get("order_index", 999))
        if op == "set":
            props = patch.get("props", {})
            layout = patch.get("layout", {})
            # The model often nests the payload under node for set/move (measured).
            node = patch.get("node") if isinstance(patch.get("node"), dict) else {}
            if not props and isinstance(node.get("props"), dict):
                props = node["props"]
            if not layout and isinstance(node.get("layout"), dict):
                layout = node["layout"]
            if not isinstance(props, dict) or not isinstance(layout, dict):
                raise PatchError("set.props and set.layout must be objects")
            normalized["props"] = props
            normalized["layout"] = layout
    return normalized


def _workspace_id_suffix(workspace_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", workspace_id or "").strip("_") or "workspace"
    digest = hashlib.sha1((workspace_id or "").encode("utf-8")).hexdigest()[:6]
    return f"{cleaned[-14:]}_{digest}"


def _scoped_insert_id(node_id: str, workspace_id: str, taken: set[str]) -> str:
    tail = f"__{_workspace_id_suffix(workspace_id)}"
    candidate = f"{node_id[:96 - len(tail)]}{tail}"
    suffix = 2
    while candidate in taken:
        extra = f"_{suffix}"
        candidate = f"{node_id[:96 - len(tail) - len(extra)]}{tail}{extra}"
        suffix += 1
    return candidate


def _numbered_insert_id(node_id: str, taken: set[str]) -> str:
    suffix = 2
    while True:
        extra = f"_{suffix}"
        candidate = f"{node_id[:96 - len(extra)]}{extra}"
        if candidate not in taken:
            return candidate
        suffix += 1


def rewrite_insert_ids_for_global_uniqueness(
    patches: list[dict[str, Any]],
    workspace_id: str,
    global_owners: dict[str, str],
    workspace_node_ids: set[str] | list[str],
) -> list[dict[str, Any]]:
    """Rewrite insert ids that would collide with the global ui_nodes PK.

    Pure (no DB): ``global_owners`` maps every already-persisted node_id the
    batch wants to insert to the workspace_id that owns it. Rule:
    - id owned by a DIFFERENT workspace (and not already in this workspace)
      -> ``<id>__<short workspace suffix>``, then ``_2``/``_3``... while the
      candidate is taken anywhere (globally, locally, or earlier in batch).
    - id already in THIS workspace, or a repeat insert of an id an earlier
      patch in the same batch claimed -> existing same-workspace semantics:
      ``<id>_2``, ``_3``...
    - otherwise the id is kept unchanged.
    After an insert is (re)named, later patches in the batch that reference
    the old id via ``parent_id``/``id`` are remapped to the new id (the old
    id cannot denote a node in this workspace). Same-workspace duplicates
    are NOT remapped: references to that id still mean the pre-existing node.
    """
    workspace_ids = set(workspace_node_ids)
    taken = workspace_ids | set(global_owners)
    batch_final_ids: set[str] = set()
    rename_map: dict[str, str] = {}
    result: list[dict[str, Any]] = []
    for patch in patches:
        p = dict(patch)
        if rename_map:
            parent_id = p.get("parent_id")
            if isinstance(parent_id, str) and parent_id in rename_map:
                p["parent_id"] = rename_map[parent_id]
            if p.get("op") != "insert":
                patch_id = p.get("id")
                if isinstance(patch_id, str) and patch_id in rename_map:
                    p["id"] = rename_map[patch_id]
                node_ref = p.get("node")
                if isinstance(node_ref, dict) and node_ref.get("id") in rename_map:
                    p["node"] = {**node_ref, "id": rename_map[node_ref["id"]]}
        if p.get("op") == "insert" and isinstance(p.get("node"), dict):
            node = dict(p["node"])
            old_id = str(node.get("id", ""))
            if old_id:
                if old_id not in taken:
                    final_id = old_id
                elif (
                    old_id in global_owners
                    and global_owners.get(old_id) != workspace_id
                    and old_id not in workspace_ids
                    and old_id not in batch_final_ids
                ):
                    final_id = _scoped_insert_id(old_id, workspace_id, taken)
                else:
                    final_id = _numbered_insert_id(old_id, taken)
                node["id"] = final_id
                p["node"] = node
                taken.add(final_id)
                batch_final_ids.add(final_id)
                if old_id not in workspace_ids:
                    rename_map[old_id] = final_id
        result.append(p)
    return result


def _fetch_global_node_owners(node_ids: list[str]) -> dict[str, str]:
    ids = list(dict.fromkeys(node_ids))
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    with db_cursor() as (_, cursor):
        cursor.execute(
            f"SELECT node_id, workspace_id FROM ui_nodes WHERE node_id IN ({placeholders})",
            ids,
        )
        owners: dict[str, str] = {}
        for row in cursor.fetchall():
            if isinstance(row, dict):
                owners[str(row["node_id"])] = str(row["workspace_id"])
            else:
                owners[str(row[0])] = str(row[1])
        return owners


def preview_patches(owner_id: str, patches: list[dict[str, Any]], conversation_id: str = "group-main") -> dict[str, Any]:
    # workspace["workspace_id"] is the ACTUAL workspace row id (legacy
    # workspace_<owner> for group-main, ws_<conv>_<owner> otherwise); it is
    # what gets returned here and used for ui_nodes/ui_change_events writes.
    workspace = get_workspace(owner_id, conversation_id=conversation_id)
    nodes = [
        {**node, "props": dict(node.get("props", {})), "layout": dict(node.get("layout", {}))}
        for node in workspace["flat_nodes"]
    ]
    normalized = [normalize_patch(patch, existing_nodes=nodes) for patch in patches]
    # ui_nodes.node_id is a GLOBAL PK: an insert id free in this workspace may
    # already be owned by another workspace (e.g. every merged workspace gets
    # a model-invented 'merged_map_master'). Rewrite those to workspace-scoped
    # ids up front (fixing later parent_id/id refs in the batch) so the INSERT
    # in apply_patches cannot hit a duplicate-PK and discard the whole batch.
    # Insert ids are collected AFTER normalize (which validates them), and
    # batch-internal repeats are resolved by the rewrite's taken-set as well.
    insert_ids = [
        str(p["node"]["id"]) for p in normalized
        if p.get("op") == "insert" and isinstance(p.get("node"), dict) and p["node"].get("id")
    ]
    if insert_ids:
        workspace_ids = {node["id"] for node in nodes}
        global_owners = _fetch_global_node_owners(insert_ids)
        # Iterate: a rewritten candidate (e.g. '<id>_2') can itself be taken
        # globally, so re-check the final ids until none is cross-workspace.
        for _ in range(4):
            normalized = rewrite_insert_ids_for_global_uniqueness(
                normalized, workspace["workspace_id"], global_owners, workspace_ids,
            )
            final_ids = [
                str(p["node"]["id"]) for p in normalized
                if p.get("op") == "insert" and isinstance(p.get("node"), dict)
            ]
            global_owners = {
                nid: ws for nid, ws in _fetch_global_node_owners(final_ids).items()
                if ws != workspace["workspace_id"] and nid not in workspace_ids
            }
            if not global_owners:
                break
    node_map = {node["id"]: node for node in nodes}
    for patch in normalized:
        op = patch["op"]
        if op == "insert":
            node = dict(patch["node"])
            root_id = next((n["id"] for n in nodes if n.get("parent_id") is None), None)
            parent_id = patch.get("parent_id")
            if (not parent_id or parent_id not in node_map) and root_id:
                parent_id = root_id
                patch["parent_id"] = root_id
            if node["id"] in node_map:
                # Same-workspace / same-batch duplicate (existing semantics):
                # _2, _3... Cross-workspace ids were already rewritten above.
                node["id"] = _numbered_insert_id(node["id"], set(node_map))
                patch["node"] = node
            if parent_id and parent_id not in node_map:
                raise PatchError(f"Parent node does not exist: {parent_id}")
            new_node = {
                "id": node["id"], "parent_id": parent_id,
                "type": node["type"], "props": dict(node["props"]), "layout": dict(node["layout"]),
                "order_index": node["order_index"],
            }
            nodes.append(new_node)
            node_map[new_node["id"]] = new_node
        elif op == "remove":
            if patch["id"] not in node_map:
                raise PatchError(f"Node does not exist: {patch['id']}")
            to_remove = {patch["id"]}
            changed = True
            while changed:
                changed = False
                for node in nodes:
                    if node.get("parent_id") in to_remove and node["id"] not in to_remove:
                        to_remove.add(node["id"])
                        changed = True
            nodes = [node for node in nodes if node["id"] not in to_remove]
            node_map = {node["id"]: node for node in nodes}
        elif op == "move":
            node = node_map.get(patch["id"])
            if not node:
                raise PatchError(f"Node does not exist: {patch['id']}")
            parent_id = patch.get("parent_id")
            if parent_id and parent_id not in node_map:
                raise PatchError(f"Parent node does not exist: {parent_id}")
            if parent_id == patch["id"]:
                raise PatchError("A node cannot be its own parent")
            node["parent_id"] = parent_id
            node["order_index"] = patch["order_index"]
        elif op == "set":
            node = node_map.get(patch["id"])
            if not node:
                raise PatchError(f"Node does not exist: {patch['id']}")
            node["props"].update(patch["props"])
            node["layout"].update(patch["layout"])

    # Reject cycles and orphaned nodes, both common model mistakes.
    for node in nodes:
        parent = node.get("parent_id")
        if parent and parent not in node_map:
            raise PatchError(f"Node {node['id']} has missing parent {parent}")
        seen = {node["id"]}
        parent_id = parent
        while parent_id:
            if parent_id in seen:
                raise PatchError("Patch would create a UI tree cycle")
            seen.add(parent_id)
            parent_id = node_map[parent_id].get("parent_id")

    return {
        "workspace_id": workspace["workspace_id"],
        "owner_actor_id": owner_id,
        "head": workspace["head"],
        "patches": normalized,
        "nodes": build_tree(nodes),
        "flat_nodes": nodes,
    }


def apply_patches(owner_id: str, actor_id: str, patches: list[dict[str, Any]], summary: str, *, make_commit: bool = True, conversation_id: str = "group-main") -> tuple[dict[str, Any], str | None]:
    from app.services.db import WRITE_LOCK, commit_all, reset_working_set
    from app.services.repository import insert_change_event

    with WRITE_LOCK:
        preview = preview_patches(owner_id, patches, conversation_id=conversation_id)
        workspace_id = preview["workspace_id"]
        try:
            # Replace rows individually; stable IDs ensure Dolt records useful row history.
            with db_cursor() as (_, cursor):
                for patch in preview["patches"]:
                    op = patch["op"]
                    if op == "insert":
                        node = patch["node"]
                        cursor.execute(
                            """INSERT INTO ui_nodes
                            (node_id, workspace_id, parent_id, node_type, props_json, layout_json, order_index)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                            (node["id"], workspace_id, patch.get("parent_id"), node["type"],
                             json_dumps(node["props"]), json_dumps(node["layout"]), node["order_index"]),
                        )
                    elif op == "remove":
                        # preview has already validated the patch; remove descendants too.
                        current = get_workspace(owner_id, conversation_id=conversation_id)["flat_nodes"]
                        remove_ids = {patch["id"]}
                        changed = True
                        while changed:
                            changed = False
                            for row in current:
                                if row.get("parent_id") in remove_ids and row["id"] not in remove_ids:
                                    remove_ids.add(row["id"])
                                    changed = True
                        if remove_ids:
                            placeholders = ",".join(["%s"] * len(remove_ids))
                            cursor.execute(
                                f"DELETE FROM ui_nodes WHERE workspace_id=%s AND node_id IN ({placeholders})",
                                (workspace_id, *remove_ids),
                            )
                    elif op == "move":
                        cursor.execute(
                            "UPDATE ui_nodes SET parent_id=%s, order_index=%s, updated_at=CURRENT_TIMESTAMP(6) WHERE workspace_id=%s AND node_id=%s",
                            (patch.get("parent_id"), patch["order_index"], workspace_id, patch["id"]),
                        )
                    elif op == "set":
                        existing = next(row for row in preview["flat_nodes"] if row["id"] == patch["id"])
                        cursor.execute(
                            "UPDATE ui_nodes SET props_json=%s, layout_json=%s, updated_at=CURRENT_TIMESTAMP(6) WHERE workspace_id=%s AND node_id=%s",
                            (json_dumps(existing["props"]), json_dumps(existing["layout"]), workspace_id, patch["id"]),
                        )
                cursor.execute("UPDATE workspaces SET updated_at=CURRENT_TIMESTAMP(6) WHERE workspace_id=%s", (workspace_id,))
            insert_change_event(workspace_id, actor_id, summary, preview["patches"])
            # Keep the seeded '<owner>_activity' ("UI commits") metric card in sync with actual workspace edits.
            activity_node_id = f"{owner_id}_activity"
            with db_cursor() as (_, cursor):
                cursor.execute(
                    "SELECT COUNT(*) AS n FROM ui_change_events WHERE workspace_id=%s",
                    (workspace_id,),
                )
                change_count = int((cursor.fetchone() or {}).get("n", 0))
                cursor.execute(
                    "SELECT props_json FROM ui_nodes WHERE workspace_id=%s AND node_id=%s",
                    (workspace_id, activity_node_id),
                )
                act_row = cursor.fetchone()
                if act_row:
                    act_props = json_loads(act_row.get("props_json"), {})
                    if isinstance(act_props, dict) and act_props.get("label") == "UI commits":
                        act_props["value"] = str(change_count)
                        cursor.execute(
                            "UPDATE ui_nodes SET props_json=%s, updated_at=CURRENT_TIMESTAMP(6) WHERE workspace_id=%s AND node_id=%s",
                            (json_dumps(act_props), workspace_id, activity_node_id),
                        )
            commit_hash = commit_all(f"ui: {actor_id} — {summary}", f"{actor_id.title()} <{actor_id}@malleable.demo>") if make_commit else None
        except Exception:
            if make_commit:
                reset_working_set()
            raise
    return get_workspace(owner_id, conversation_id=conversation_id), commit_hash
