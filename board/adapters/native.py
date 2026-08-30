#!/usr/bin/env python3
"""native.py — Native Adapter for .agent-board/ YAML/JSON Data Layer."""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from board.miniyaml import safe_load_file
except (ImportError, ValueError):
    from miniyaml import safe_load_file

try:
    from .base import (
        BLOCKER_RE,
        FEED_LIMIT,
        RECEIPT_NEXT,
        RECEIPT_STATUS,
        RECEIPT_VERDICT,
        TITLE_LIMIT,
        BaseAdapter,
        clean_text,
        read_text,
        valid_name,
    )
except (ImportError, ValueError):
    from base import (
        BLOCKER_RE,
        FEED_LIMIT,
        RECEIPT_NEXT,
        RECEIPT_STATUS,
        RECEIPT_VERDICT,
        TITLE_LIMIT,
        BaseAdapter,
        clean_text,
        read_text,
        valid_name,
    )


def _load_data_file(path: str) -> Any:
    """Load YAML or JSON file using miniyaml parser."""
    if not os.path.isfile(path):
        return None
    try:
        return safe_load_file(path)
    except Exception:
        return None


class NativeAdapter(BaseAdapter):
    """Adapter for .agent-board/ directories containing board.yaml, tasks, receipts, events."""

    def _resolve_board_dir(
            self, board_id: Optional[str] = None) -> Tuple[str, str]:
        """Resolve board directory and name from root_dir and optional board_id."""
        # 1. If root_dir itself is .agent-board or contains board.yaml
        if os.path.isfile(os.path.join(self.root_dir, "board.yaml")) or os.path.isfile(
            os.path.join(self.root_dir, "board.json")
        ):
            name = board_id or os.path.basename(self.root_dir)
            if name == ".agent-board":
                name = os.path.basename(
                    os.path.dirname(
                        self.root_dir)) or "default"
            return self.root_dir, name

        # 2. Check if root_dir/.agent-board exists
        dot_ab = os.path.join(self.root_dir, ".agent-board")
        if os.path.isdir(dot_ab) and (
            os.path.isfile(os.path.join(dot_ab, "board.yaml"))
            or os.path.isfile(os.path.join(dot_ab, "board.json"))
        ):
            name = board_id or os.path.basename(self.root_dir)
            return dot_ab, name

        # 3. Check for subdirectories under root_dir
        if board_id and valid_name(board_id):
            candidate_dir = os.path.join(self.root_dir, board_id)
            if os.path.isdir(candidate_dir):
                if os.path.isfile(os.path.join(candidate_dir, "board.yaml")) or os.path.isfile(
                    os.path.join(candidate_dir, "board.json")
                ):
                    return candidate_dir, board_id
                sub_dot = os.path.join(candidate_dir, ".agent-board")
                if os.path.isdir(sub_dot):
                    return sub_dot, board_id

        # Fallback to root_dir
        return self.root_dir, board_id or os.path.basename(self.root_dir)

    def list_boards(self) -> List[Dict[str, Any]]:
        """List available boards under root_dir."""
        boards: List[Dict[str, Any]] = []

        # Check self
        if os.path.isfile(os.path.join(self.root_dir, "board.yaml")) or os.path.isfile(
            os.path.join(self.root_dir, "board.json")
        ):
            b_data = _load_data_file(os.path.join(self.root_dir, "board.yaml")) or _load_data_file(
                os.path.join(self.root_dir, "board.json")
            )
            b_meta = b_data.get(
                "board",
                {}) if isinstance(
                b_data,
                dict) else {}
            name = (
                b_meta.get("name")
                or os.path.basename(self.root_dir).lstrip(".")
                or "default"
            )
            status = b_meta.get("status", "active")
            boards.append({"name": name, "status": status})
            return boards

        dot_ab = os.path.join(self.root_dir, ".agent-board")
        if os.path.isdir(dot_ab):
            b_data = _load_data_file(os.path.join(dot_ab, "board.yaml")) or _load_data_file(
                os.path.join(dot_ab, "board.json")
            )
            b_meta = b_data.get(
                "board",
                {}) if isinstance(
                b_data,
                dict) else {}
            name = (
                b_meta.get("name")
                or os.path.basename(self.root_dir).lstrip(".")
                or "default"
            )
            status = b_meta.get("status", "active")
            boards.append({"name": name, "status": status})
            return boards

        # Scan subdirectories
        try:
            entries = sorted(os.listdir(self.root_dir))
        except OSError:
            return boards

        for entry in entries:
            if not valid_name(entry):
                continue
            entry_path = os.path.join(self.root_dir, entry)
            if not os.path.isdir(entry_path):
                continue
            sub_dot = os.path.join(entry_path, ".agent-board")
            target = sub_dot if os.path.isdir(sub_dot) else entry_path
            b_data = _load_data_file(os.path.join(target, "board.yaml")) or _load_data_file(
                os.path.join(target, "board.json")
            )
            if isinstance(b_data, dict) and "board" in b_data:
                b_meta = b_data.get("board", {})
                boards.append(
                    {
                        "name": b_meta.get("name", entry),
                        "status": b_meta.get("status", "active"),
                    }
                )
        return boards

    def _parse_receipt(
        self,
        receipts_dir: str,
        task_id: str,
        owner: Optional[str],
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Validate receipt according to strict whitelist rules.

        Returns: (receipt_dict_or_None, complete_bool)
        """
        yaml_path = os.path.join(receipts_dir, f"{task_id}.yaml")
        json_path = os.path.join(receipts_dir, f"{task_id}.json")
        data = _load_data_file(yaml_path) or _load_data_file(json_path)

        if data is None:
            return None, False

        # If wrapped in agent_board_receipt_v1
        if isinstance(data, dict) and "agent_board_receipt_v1" in data:
            data = data["agent_board_receipt_v1"]

        if not isinstance(data, dict):
            return None, True

        task = data.get("task") or data.get("task_id")
        worker = data.get("worker")
        status = data.get("status")
        verdict = data.get("verdict")
        next_step = data.get("next")
        blocker = data.get("blocker")
        artifact = data.get("artifact")
        summary = clean_text(data.get("summary"), limit=TITLE_LIMIT)
        metrics = data.get("metrics") if isinstance(
            data.get("metrics"), dict) else {}

        # 1. task must equal task_id
        if task != task_id:
            return None, True

        # 2. worker must equal owner if owner is known
        if owner and worker and worker != owner:
            return None, True

        # 3. status enum
        if status not in RECEIPT_STATUS:
            return None, True

        # 4. verdict enum
        if verdict not in RECEIPT_VERDICT:
            return None, True

        # 5. next enum
        if next_step not in RECEIPT_NEXT:
            return None, True

        # 6. blocker regex
        if blocker is None or not BLOCKER_RE.match(str(blocker)):
            return None, True

        # 7. artifact safety check
        if artifact is not None and not self._valid_artifact(
                artifact, task_id):
            return None, True

        receipt_obj = {
            "status": status,
            "verdict": verdict,
            "next": next_step,
            "blocker": str(blocker),
            "artifact": artifact,
            "summary": summary,
            "metrics": metrics,
        }
        return receipt_obj, True

    def _valid_artifact(self, value: Any, task_id: str) -> bool:
        """Validate artifact string shape without opening it."""
        if value is None or value == "none":
            return True
        if not isinstance(value, str):
            return False
        if "\t" in value or "\n" in value or "\r" in value:
            return False
        if any(not (ch == " " or ch.isprintable()) for ch in value):
            return False
        # Allow safe relative artifact path ending in task_id
        return value.endswith(
            f"artifacts/{task_id}.md") or value.endswith(f"artifacts/{task_id}.yaml") or "/" not in value

    def _build_events_lineage(
        self,
        events: List[Dict[str, Any]],
        known_tasks: Set[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Level 1 Lineage: build parent chains from events.jsonl."""
        parent_map: Dict[str, str] = {}
        worker_map: Dict[str, str] = {}

        for ev in events:
            if not isinstance(ev, dict):
                continue
            ev_type = ev.get("event")
            t_id = ev.get("task")
            w_id = ev.get("worker")
            p_id = ev.get("parent")

            if t_id and valid_name(t_id):
                if w_id and valid_name(w_id):
                    worker_map[t_id] = w_id
                if p_id and valid_name(p_id) and p_id != t_id:
                    parent_map[t_id] = p_id

        lineage_map: Dict[str, List[Dict[str, Any]]] = {}
        for t_id in known_tasks:
            if t_id in parent_map or t_id in worker_map:
                chain = []
                seen = set()
                curr: Optional[str] = t_id
                while curr and curr not in seen:
                    seen.add(curr)
                    chain.append(
                        {"task": curr, "worker": worker_map.get(curr)})
                    curr = parent_map.get(curr)
                chain.reverse()
                if len(chain) > 1 or t_id in parent_map:
                    lineage_map[t_id] = chain

        return lineage_map

    def _build_parent_field_lineage(
        self,
        tasks_dict: Dict[str, Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Level 2 Lineage: build parent chains from tasks[].parent field."""
        parent_map: Dict[str, str] = {}
        worker_map: Dict[str, str] = {}

        for t_id, task in tasks_dict.items():
            worker_map[t_id] = task.get("owner") or ""
            p = task.get("parent")
            if p and valid_name(p) and p != t_id:
                parent_map[t_id] = p

        lineage_map: Dict[str, List[Dict[str, Any]]] = {}
        for t_id in tasks_dict:
            if t_id in parent_map:
                chain = []
                seen = set()
                curr: Optional[str] = t_id
                while curr and curr not in seen:
                    seen.add(curr)
                    chain.append(
                        {"task": curr, "worker": worker_map.get(curr)})
                    curr = parent_map.get(curr)
                chain.reverse()
                lineage_map[t_id] = chain

        return lineage_map

    def _heuristic_root(self, task_id: str, known_tasks: Set[str]) -> str:
        """Level 3 Lineage: ID prefix heuristic (e.g. T005-verify -> T005)."""
        parts = task_id.split("-")
        for cut in range(len(parts) - 1, 0, -1):
            candidate = "-".join(parts[:cut])
            if candidate in known_tasks:
                return candidate
        return task_id

    def _build_heuristic_lineage(
        self,
        task_order: List[str],
        known_tasks: Set[str],
        owners: Dict[str, Optional[str]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Group tasks by heuristic root."""
        groups: Dict[str, List[str]] = {}
        for t_id in task_order:
            root = self._heuristic_root(t_id, known_tasks)
            groups.setdefault(root, []).append(t_id)

        lineage_map: Dict[str, List[Dict[str, Any]]] = {}
        for root, members in groups.items():
            if root in members and members[0] != root:
                members = [root] + [t for t in members if t != root]
            chain = [{"task": t, "worker": owners.get(t)} for t in members]
            for idx, t_id in enumerate(members):
                lineage_map[t_id] = chain[: idx + 1]
        return lineage_map

    def load_board(self, board_id: Optional[str] = None) -> Dict[str, Any]:
        """Aggregate .agent-board data and return Unified Core JSON Contract."""
        board_dir, default_name = self._resolve_board_dir(board_id)
        warnings: List[str] = []

        # 1. Read board.yaml / board.json
        b_yaml = os.path.join(board_dir, "board.yaml")
        b_json = os.path.join(board_dir, "board.json")
        board_raw = _load_data_file(b_yaml) or _load_data_file(b_json) or {}
        if not isinstance(board_raw, dict):
            board_raw = {}

        board_meta = board_raw.get("board", {})
        board_name = clean_text(
            board_meta.get("name") or default_name,
            limit=60) or "default"
        board_title = clean_text(
            board_meta.get("title") or board_name,
            limit=120) or board_name
        board_status = board_meta.get("status", "active")
        board_mode = clean_text(
            board_meta.get("mode") or board_raw.get(
                "config", {}).get("mode"), limit=40)
        board_desc = clean_text(board_meta.get("description"), limit=200)

        # 2. Read declared lanes
        lanes_raw = board_raw.get("lanes", [])
        lanes: List[Dict[str, Any]] = []
        known_lane_ids: Set[str] = set()

        if isinstance(lanes_raw, list):
            for l in lanes_raw:
                if isinstance(l, dict) and "id" in l:
                    l_id = str(l["id"])
                    known_lane_ids.add(l_id)
                    lanes.append(
                        {
                            "id": l_id,
                            "label": clean_text(l.get("label") or l_id, limit=60) or l_id,
                            "role": l.get("role", "worker"),
                            "runtime": clean_text(l.get("runtime"), limit=40),
                            "new": bool(l.get("new", False)),
                            "ext": l.get("ext", {}) if isinstance(l.get("ext"), dict) else {},
                        }
                    )

        # 3. Read tasks: scanning tasks/ directory and inline tasks in
        # board.yaml
        tasks_dir = os.path.join(board_dir, "tasks")
        tasks_map: Dict[str, Dict[str, Any]] = {}
        task_order: List[str] = []

        # A. Directory tasks/<id>.yaml or <id>.json
        if os.path.isdir(tasks_dir):
            try:
                for entry in sorted(os.listdir(tasks_dir)):
                    t_id = None
                    if entry.endswith(".yaml"):
                        t_id = entry[:-5]
                    elif entry.endswith(".json"):
                        t_id = entry[:-5]
                    if t_id and valid_name(t_id):
                        t_data = _load_data_file(
                            os.path.join(tasks_dir, entry))
                        if isinstance(t_data, dict):
                            t_data["id"] = t_data.get("id") or t_id
                            tasks_map[t_id] = t_data
                            if t_id not in task_order:
                                task_order.append(t_id)
            except OSError:
                pass

        # B. Inline tasks in board.yaml
        inline_tasks = board_raw.get("tasks", [])
        if isinstance(inline_tasks, list):
            for t in inline_tasks:
                if isinstance(t, dict) and "id" in t:
                    t_id = str(t["id"])
                    if valid_name(t_id) and t_id not in tasks_map:
                        tasks_map[t_id] = t
                        if t_id not in task_order:
                            task_order.append(t_id)

        known_tasks = set(task_order)

        # 4. Read events.jsonl
        events_path = os.path.join(board_dir, "events.jsonl")
        events: List[Dict[str, Any]] = []
        events_text = read_text(events_path, limit=1 << 22)
        if events_text:
            for line in events_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev_obj = json.loads(line)
                    if isinstance(ev_obj, dict):
                        events.append(ev_obj)
                except Exception:
                    continue

        # 5. Build Lineage
        events_lineage = self._build_events_lineage(events, known_tasks)
        parent_field_lineage = self._build_parent_field_lineage(tasks_map)
        owners_map = {t_id: tasks_map[t_id].get(
            "owner") for t_id in task_order}
        heuristic_lineage = self._build_heuristic_lineage(
            task_order, known_tasks, owners_map)

        # 5b. Superseded tasks: same rule as the tmux-agent-teams adapter
        # (see TeamsAdapter.load_board), applied to this adapter's two
        # explicit parent sources — events[].parent and tasks[].parent —
        # so the two adapters don't silently diverge. The ID-prefix
        # heuristic lineage is deliberately excluded: it's a guess, not a
        # recorded handoff, so it must never downgrade a real blocker.
        superseding_parents: Set[str] = set()
        superseded_by: Dict[str, List[str]] = {}

        def _record_supersede(parent_id: Any, child_id: Any) -> None:
            if not valid_name(parent_id) or not valid_name(child_id):
                return
            if parent_id == child_id:
                return
            if parent_id not in known_tasks or child_id not in known_tasks:
                return
            superseding_parents.add(parent_id)
            children = superseded_by.setdefault(parent_id, [])
            if child_id not in children:
                children.append(child_id)

        for ev in events:
            if isinstance(ev, dict):
                _record_supersede(ev.get("parent"), ev.get("task"))
        for t_id, t_raw in tasks_map.items():
            _record_supersede(t_raw.get("parent"), t_id)

        # 6. Process Receipts and Tasks
        receipts_dir = os.path.join(board_dir, "receipts")
        tasks: List[Dict[str, Any]] = []
        attention: List[str] = []
        activity: List[Dict[str, Any]] = []
        seen_owners: Set[str] = set()

        stats = {
            "total": len(task_order),
            "todo": 0,
            "doing": 0,
            "blocked": 0,
            "done": 0,
            "superseded": 0,
        }

        for t_id in task_order:
            t_raw = tasks_map[t_id]
            owner = t_raw.get("owner")
            if owner:
                seen_owners.add(str(owner))

            # Validate receipt
            receipt, complete = self._parse_receipt(receipts_dir, t_id, owner)
            if complete and receipt is None:
                warnings.append(f"invalid-receipt:{t_id}")

            badges: List[str] = []
            if isinstance(t_raw.get("badges"), list):
                badges.extend(t_raw["badges"])

            # Resolve column mapping
            explicit_col = t_raw.get("column") or t_raw.get("status")
            blocker_val = t_raw.get("blocker") or t_raw.get("blocked_by")
            has_task_blocker = bool(
                blocker_val and str(blocker_val).lower() != "none")

            if owner is None:
                column = "todo"
            elif receipt is not None and receipt["status"] == "completed":
                column = "done"
                # A parent pointer also records a routine "go verify this"
                # dispatch, not just a handoff after a stuck/failed task —
                # next == "verify" is exactly that routine case, so it must
                # not be mislabeled as "redone".
                if t_id in superseding_parents and receipt["next"] != "verify":
                    badges.append("superseded")
            elif receipt is not None and receipt["status"] in ("blocked", "failed"):
                badges.append(f"receipt:{receipt['status']}")
                if t_id in superseding_parents:
                    column = "superseded"
                    badges.append("superseded")
                else:
                    column = "blocked"
            elif complete:
                column = "blocked"
            elif has_task_blocker:
                column = "blocked"
                if f"blocker:{blocker_val}" not in badges:
                    badges.append(f"blocker:{blocker_val}")
            elif explicit_col in ("done", "completed"):
                column = "done"
            elif explicit_col in ("blocked", "failed"):
                column = "blocked"
            elif explicit_col in ("todo", "queued"):
                column = "todo"
            else:
                column = "doing"

            if receipt is not None and receipt["verdict"] == "fail":
                badges.append("verdict:fail")

            # Determine Lineage
            if t_id in events_lineage:
                lineage = {"chain": events_lineage[t_id], "source": "events"}
            elif t_id in parent_field_lineage:
                lineage = {
                    "chain": parent_field_lineage[t_id],
                    "source": "parent-field"}
            else:
                h_chain = heuristic_lineage.get(
                    t_id) or [{"task": t_id, "worker": owner}]
                lineage = {"chain": h_chain, "source": "heuristic"}
                if "-" in t_id and "heuristic-lineage" not in badges:
                    badges.append("heuristic-lineage")

            # Resolve Detail text (inline detail or detail_file)
            detail = t_raw.get("detail")
            detail_file = t_raw.get("detail_file")
            if detail_file:
                # Security: prevent directory traversal
                clean_df = str(detail_file).replace("\\", "/")
                if clean_df.startswith("/") or ".." in clean_df.split("/"):
                    warnings.append(f"invalid-detail-path:{t_id}")
                    detail = None
                else:
                    df_path = os.path.join(board_dir, clean_df)
                    if os.path.isfile(df_path):
                        detail = read_text(df_path, limit=1 << 16)
                    else:
                        detail = None

            # Collect attention
            if (
                column == "blocked"
                or (receipt is not None and (receipt["status"] in ("blocked", "failed") or receipt["next"] == "await_user"))
                or has_task_blocker
            ) and t_id not in superseding_parents:
                if t_id not in attention:
                    attention.append(t_id)

            # Update stats
            if column in stats:
                stats[column] += 1

            tasks.append(
                {
                    "id": t_id,
                    "title": clean_text(t_raw.get("title") or t_id, limit=TITLE_LIMIT),
                    "column": column,
                    "priority": t_raw.get("priority", "medium"),
                    "owner": owner,
                    "tags": t_raw.get("tags") if isinstance(t_raw.get("tags"), list) else [],
                    "detail": detail,
                    "blocker": blocker_val if has_task_blocker else (receipt.get("blocker") if receipt else None),
                    "blocked_since": t_raw.get("blocked_since"),
                    "updated_at": t_raw.get("updated_at"),
                    "badges": list(dict.fromkeys(badges)),  # deduplicate
                    "receipt": receipt,
                    "lineage": lineage,
                    "superseded_by": list(superseded_by.get(t_id, [])),
                    "ext": t_raw.get("ext") if isinstance(t_raw.get("ext"), dict) else {},
                }
            )

            if receipt is not None:
                mtime = 0
                r_file = os.path.join(receipts_dir, f"{t_id}.yaml")
                if not os.path.exists(r_file):
                    r_file = os.path.join(receipts_dir, f"{t_id}.json")
                try:
                    mtime = int(os.path.getmtime(r_file))
                except OSError:
                    mtime = int(time.time())

                activity.append(
                    {
                        "task": t_id,
                        "worker": owner,
                        "ts": mtime,
                        "kind": "receipt",
                        "fields": {
                            "status": receipt["status"],
                            "verdict": receipt["verdict"],
                            "blocker": receipt["blocker"],
                            "next": receipt["next"],
                            "summary": receipt.get("summary"),
                        },
                    }
                )

        # 7. Rework propagation for verdict: fail
        task_by_id = {t["id"]: t for t in tasks}
        for t in tasks:
            rec = t["receipt"]
            if rec and rec.get("verdict") == "fail":
                chain = t["lineage"]["chain"]
                if len(chain) >= 2:
                    pred_id = chain[-2]["task"]
                    if pred_id in task_by_id:
                        if "rework" not in task_by_id[pred_id]["badges"]:
                            task_by_id[pred_id]["badges"].append("rework")

        # 8. Dynamic Lane addition for undeclared owners
        for o in sorted(seen_owners):
            if o and o not in known_lane_ids:
                known_lane_ids.add(o)
                lanes.append(
                    {
                        "id": o,
                        "label": o,
                        "role": "worker",
                        "runtime": None,
                        "new": True,
                        "ext": {},
                    }
                )

        # Count active tasks per lane
        for lane in lanes:
            l_id = lane["id"]
            lane["doing_count"] = sum(
                1 for t in tasks if t["owner"] == l_id and t["column"] == "doing")
            lane["blocked_count"] = sum(
                1 for t in tasks if t["owner"] == l_id and t["column"] == "blocked")
            lane["superseded_count"] = sum(
                1 for t in tasks if t["owner"] == l_id and t["column"] == "superseded")
            lane["is_new"] = lane.get("new", False)

        # Merge events into activity
        for ev in events:
            if isinstance(ev, dict) and "ts" in ev:
                activity.append(
                    {
                        "task": ev.get("task", ""),
                        "worker": ev.get("worker") or ev.get("actor"),
                        "ts": ev.get("ts", 0),
                        "kind": ev.get("event", "event"),
                        "fields": ev,
                    }
                )

        activity.sort(key=lambda item: item.get("ts", 0), reverse=True)

        payload = {
            "board": {
                "id": board_name,
                "name": board_name,
                "title": board_title,
                "status": board_status,
                "mode": board_mode,
                "description": board_desc,
                "updated_at": int(time.time()),
                "ext": board_meta.get("ext", {}),
            },
            "lanes": lanes,
            "tasks": tasks,
            "attention": attention,
            "activity": activity[:FEED_LIMIT],
            "stats": stats,
            "adapter": "native",
            "generated_at": int(time.time()),
            "warnings": warnings,
            # Aliases for backwards compatibility
            "team": {
                "name": board_name,
                "title": board_title,
                "status": board_status,
                "mode": board_mode,
            },
            "roster": [
                {
                    "worker": l["id"],
                    "runtime": l.get("runtime"),
                    "role": l.get("role", "worker"),
                    "new": l.get("new", False),
                }
                for l in lanes
            ],
            "receipts_feed": activity[:FEED_LIMIT],
        }
        return payload

    def get_mount_paths(self) -> List[str]:
        """Return files and directories to mount read-only in sandbox mode."""
        paths: List[str] = []
        board_dir, _ = self._resolve_board_dir()

        targets = [
            os.path.join(board_dir, "board.yaml"),
            os.path.join(board_dir, "board.json"),
            os.path.join(board_dir, "tasks"),
            os.path.join(board_dir, "receipts"),
            os.path.join(board_dir, "events.jsonl"),
            os.path.join(board_dir, "notes"),
        ]
        for p in targets:
            if os.path.exists(p):
                paths.append(p)
        return paths
