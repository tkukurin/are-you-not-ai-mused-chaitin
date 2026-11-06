from __future__ import annotations

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
                suffix = 2
                while f"{node['id']}_{suffix}" in node_map:
                    suffix += 1
                node["id"] = f"{node['id']}_{suffix}"
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
