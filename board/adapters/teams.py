#!/usr/bin/env python3
"""teams.py — Adapter for tmux-agent-teams .teams/ Data Layer."""

import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

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

WORKTREE_STATUS = ("working", "blocked", "review", "merged", "closed")

TEAM_FILES = (
    "board.tsv",
    "worktrees.tsv",
    "agents.tsv",
    "workers.tsv",
    "flow.tsv",
    "mode.md",
    "team-meta.env",
    "team.meta",
)
TEAM_DIRS = (
    "receipts",
    "tasks",
)


def read_rows(path: str, columns: int) -> List[List[str]]:
    """Parse a tab-separated append-only file into fixed-width rows."""
    text = read_text(path)
    if text is None:
        return []
    rows: List[List[str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < columns:
            continue
        rows.append(fields[:columns])
    return rows


def read_meta(team_dir: str) -> Dict[str, str]:
    """Parse team-meta.env or team.meta."""
    path = os.path.join(team_dir, "team-meta.env")
    text = read_text(path)
    if text is None:
        text = read_text(os.path.join(team_dir, "team.meta"))
    meta: Dict[str, str] = {}
    if text is None:
        return meta
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if value.startswith("$'") and value.endswith("'") and len(value) > 2:
            value = value[2:-1]
        elif value.startswith("'") and value.endswith("'") and len(value) > 1:
            value = value[1:-1]
        elif value.startswith('"') and value.endswith('"') and len(value) > 1:
            value = value[1:-1]
        meta[key] = value
    return meta


class TeamsAdapter(BaseAdapter):
    """Adapter for .teams/ data structure."""

    def _resolve_teams_root(self) -> str:
        """Resolve the directory containing individual team folders."""
        if os.path.basename(self.root_dir) == ".teams":
            return self.root_dir
        sub_teams = os.path.join(self.root_dir, ".teams")
        if os.path.isdir(sub_teams):
            return sub_teams
        return self.root_dir

    def list_boards(self) -> List[Dict[str, Any]]:
        """List available teams under .teams/ directory."""
        teams_root = self._resolve_teams_root()
        teams: List[Dict[str, Any]] = []

        try:
            entries = sorted(os.listdir(teams_root))
        except OSError:
            return teams

        for name in entries:
            if not valid_name(name):
                continue
            team_dir = os.path.join(teams_root, name)
            if not os.path.isdir(team_dir):
                continue
            if not (
                os.path.isfile(os.path.join(team_dir, "team-meta.env"))
                or os.path.isfile(os.path.join(team_dir, "team.meta"))
            ):
                continue
            meta = read_meta(team_dir)
            teams.append(
                {
                    "name": name,
                    "status": clean_text(meta.get("TEAM_STATUS")) or "unknown",
                }
            )
        return teams

    def _parse_receipt(
        self,
        receipts_dir: str,
        task_id: str,
        owner: Optional[str],
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Parse markdown receipt with DONE sentinel and whitelist validation."""
        path = os.path.join(receipts_dir, f"{task_id}.md")
        text = read_text(path, limit=1 << 16)
        if text is None:
            return None, False
        lines = text.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines or lines[-1] != "DONE " + task_id:
            return None, False

        def get_val(key: str) -> Optional[str]:
            prefix = key + ": "
            for line in lines:
                if line.startswith(prefix):
                    return line[len(prefix):].strip()
            return None

        fields = {
            "task_id": get_val("task_id"),
            "worker": get_val("worker"),
            "status": get_val("status"),
            "verdict": get_val("verdict"),
            "blocker": get_val("blocker"),
            "next": get_val("next"),
            "artifact": get_val("artifact"),
        }

        if fields["task_id"] != task_id:
            return None, True
        if not owner or fields["worker"] != owner:
            return None, True
        if fields["status"] not in RECEIPT_STATUS:
            return None, True
        if fields["verdict"] not in RECEIPT_VERDICT:
            return None, True
        if fields["next"] not in RECEIPT_NEXT:
            return None, True
        if not fields["blocker"] or not BLOCKER_RE.match(fields["blocker"]):
            return None, True
        if not self._valid_artifact(fields["artifact"], task_id):
            return None, True

        return (
            {
                "status": fields["status"],
                "verdict": fields["verdict"],
                "blocker": fields["blocker"],
                "next": fields["next"],
                "artifact": fields["artifact"],
            },
            True,
        )

    def _valid_artifact(self, value: Any, task_id: str) -> bool:
        if value is None:
            return False
        if value == "none":
            return True
        if "\t" in value or "\n" in value or "\r" in value:
            return False
        if any(not (ch == " " or ch.isprintable()) for ch in value):
            return False
        return value.endswith("artifacts/" + task_id + ".md")

    def _build_flow_lineage(
        self,
        flow_rows: List[List[str]],
        known_tasks: Set[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        parent: Dict[str, str] = {}
        worker_of: Dict[str, str] = {}
        order: List[str] = []

        for row in flow_rows:
            _ts, task_id, worker, parent_id = row[0], row[1], row[2], row[3]
            if not valid_name(task_id) or task_id not in known_tasks:
                continue
            if not valid_name(worker):
                continue
            if task_id not in worker_of:
                order.append(task_id)
            worker_of[task_id] = worker
            if valid_name(parent_id) and parent_id != task_id:
                parent[task_id] = parent_id
            else:
                parent.pop(task_id, None)

        if not worker_of:
            return {}

        def ancestors(task_id: str) -> List[str]:
            chain = []
            seen = set()
            cursor: Optional[str] = task_id
            while cursor and cursor not in seen:
                seen.add(cursor)
                chain.append(cursor)
                cursor = parent.get(cursor)
            chain.reverse()
            return chain

        lineage: Dict[str, List[Dict[str, Any]]] = {}
        for task_id in order:
            lineage[task_id] = [
                {"task": node, "worker": worker_of.get(node)} for node in ancestors(task_id)
            ]
        return lineage

    def _heuristic_root(self, task_id: str, known_tasks: Set[str]) -> str:
        parts = task_id.split("-")
        for cut in range(len(parts) - 1, 0, -1):
            candidate = "-".join(parts[:cut])
            if candidate in known_tasks:
                return candidate
        return task_id

    def _build_heuristic_lineage(
        self,
        known_tasks: Set[str],
        order: List[str],
        owners: Dict[str, Optional[str]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        groups: Dict[str, List[str]] = {}
        for task_id in order:
            groups.setdefault(
                self._heuristic_root(
                    task_id,
                    known_tasks),
                []).append(task_id)
        lineage: Dict[str, List[Dict[str, Any]]] = {}
        for root, members in groups.items():
            if root in members and members[0] != root:
                members = [root] + [t for t in members if t != root]
            chain = [{"task": t, "worker": owners.get(t)} for t in members]
            for index, task_id in enumerate(members):
                lineage[task_id] = chain[: index + 1]
        return lineage

    def _resolve_mode(self, team_dir: str) -> Optional[str]:
        text = read_text(os.path.join(team_dir, "mode.md"), limit=1 << 16)
        if text is None:
            return None
        for line in text.splitlines():
            value = clean_text(line.lstrip("# ").strip(), 60)
            if value:
                return value
        return None

    def load_board(self, board_id: Optional[str] = None) -> Dict[str, Any]:
        """Aggregate .teams data and return Unified Core JSON Contract."""
        teams_root = self._resolve_teams_root()
        all_teams = self.list_boards()
        team_names = [t["name"] for t in all_teams]

        team_name = board_id
        if team_name is None:
            if len(team_names) == 1:
                team_name = team_names[0]
            elif team_names:
                team_name = team_names[0]
            else:
                team_name = "default"

        team_dir = os.path.join(teams_root, team_name)
        warnings: List[str] = []
        meta = read_meta(team_dir)

        # 1. Roster
        agent_rows = read_rows(os.path.join(team_dir, "agents.tsv"), 7)
        worker_rows = read_rows(os.path.join(team_dir, "workers.tsv"), 2)

        agent_by_name: Dict[str, Dict[str, Any]] = {}
        agent_order: List[str] = []
        for role, name, runtime, _session, _dir, _pane, lifecycle in agent_rows:
            if not valid_name(name):
                continue
            if name not in agent_by_name:
                agent_order.append(name)
            agent_by_name[name] = {
                "role": role if role in ("leader", "worker") else "worker",
                "runtime": clean_text(runtime, 40),
                "lifecycle": clean_text(lifecycle, 40),
            }

        roster_names: List[str] = []
        for name, _pane in worker_rows:
            if valid_name(name) and name not in roster_names:
                roster_names.append(name)
        for name in agent_order:
            if name not in roster_names:
                roster_names.append(name)

        # 2. Board / Worktrees / Flow
        board_rows = read_rows(os.path.join(team_dir, "board.tsv"), 2)
        worktree_rows = read_rows(os.path.join(team_dir, "worktrees.tsv"), 6)
        flow_rows = read_rows(os.path.join(team_dir, "flow.tsv"), 4)

        owners: Dict[str, Optional[str]] = {}
        dispatch_order: List[str] = []
        for task_id, worker in board_rows:
            if not valid_name(task_id) or not valid_name(worker):
                continue
            if task_id not in owners:
                owners[task_id] = worker
                dispatch_order.append(task_id)

        worktrees: Dict[str, Dict[str, Any]] = {}
        for worker, _pane, mr, _dir, branch, status in worktree_rows:
            if not valid_name(worker):
                continue
            worktrees[worker] = {
                "mr": clean_text(mr, 40),
                "branch": clean_text(branch, 80),
                "status": status if status in WORKTREE_STATUS else None,
            }

        # 3. Tasks
        tasks_dir = os.path.join(team_dir, "tasks")
        contract_ids: List[str] = []
        try:
            for entry in sorted(os.listdir(tasks_dir)):
                if not entry.endswith(".md"):
                    continue
                task_id = entry[:-3]
                if valid_name(task_id):
                    contract_ids.append(task_id)
        except OSError:
            pass

        task_order = list(contract_ids)
        for task_id in dispatch_order:
            if task_id not in task_order:
                task_order.append(task_id)
        known_tasks = set(task_order)

        # Tasks that appear as the `parent` of another known task in
        # flow.tsv: someone took over from them. Same field validation as
        # _build_flow_lineage, so a malformed flow.tsv row can't silently
        # reclassify a real blocker. superseded_by keeps the child ids (in
        # first-seen order) so the UI can say who took over, not just that
        # someone did.
        superseding_parents: Set[str] = set()
        superseded_by: Dict[str, List[str]] = {}
        for row in flow_rows:
            child_id, parent_id = row[1], row[3]
            if not valid_name(child_id) or child_id not in known_tasks:
                continue
            if not valid_name(parent_id) or parent_id == child_id:
                continue
            superseding_parents.add(parent_id)
            children = superseded_by.setdefault(parent_id, [])
            if child_id not in children:
                children.append(child_id)

        flow_lineage = self._build_flow_lineage(flow_rows, known_tasks)
        heuristic_order = list(dispatch_order)
        heuristic_order += [t for t in task_order if t not in owners]
        heuristic_lineage = self._build_heuristic_lineage(
            known_tasks, heuristic_order, owners)

        receipts_dir = os.path.join(team_dir, "receipts")
        tasks: List[Dict[str, Any]] = []
        activity: List[Dict[str, Any]] = []
        attention: List[str] = []
        engaged_workers = set(owners.values())
        for row in flow_rows:
            if valid_name(row[2]):
                engaged_workers.add(row[2])

        receipt_by_task: Dict[str, Tuple[Optional[Dict[str, Any]], bool]] = {}
        for task_id in task_order:
            owner = owners.get(task_id)
            receipt, complete = self._parse_receipt(
                receipts_dir, task_id, owner)
            if complete and receipt is None:
                warnings.append(f"invalid-receipt:{task_id}")
            receipt_by_task[task_id] = (receipt, complete)

        stats = {
            "total": len(task_order),
            "todo": 0,
            "doing": 0,
            "blocked": 0,
            "done": 0,
            "superseded": 0,
        }

        for task_id in task_order:
            owner = owners.get(task_id)
            receipt, complete = receipt_by_task[task_id]
            worktree = worktrees.get(owner) if owner else None
            badges: List[str] = []

            if owner is None:
                column = "todo"
            elif receipt is not None and receipt["status"] == "completed":
                column = "done"
                # flow.tsv parent links also record routine "go verify this"
                # dispatches, not just handoffs after a stuck/failed task —
                # next == "verify" is exactly that routine case, so it must
                # not be mislabeled as "redone".
                if task_id in superseding_parents and receipt["next"] != "verify":
                    badges.append("superseded")
            elif receipt is not None and receipt["status"] in ("blocked", "failed"):
                badges.append("receipt:" + receipt["status"])
                if task_id in superseding_parents:
                    column = "superseded"
                    badges.append("superseded")
                else:
                    column = "blocked"
            elif worktree is not None and worktree["status"] == "blocked":
                column = "blocked"
                badges.append("worktree:blocked")
            elif complete:
                column = "blocked"
            else:
                column = "doing"

            if (
                column == "blocked"
                and receipt is not None
                and worktree is not None
                and worktree["status"] == "blocked"
                and "worktree:blocked" not in badges
            ):
                badges.append("worktree:blocked")

            if receipt is not None and receipt["verdict"] == "fail":
                badges.append("verdict:fail")

            chain = flow_lineage.get(task_id)
            lineage_source = "flow"
            if not chain:
                lineage_source = "heuristic"
                chain = heuristic_lineage.get(
                    task_id) or [{"task": task_id, "worker": owner}]
                if "heuristic-lineage" not in badges:
                    badges.append("heuristic-lineage")

            title = None
            detail_content = None
            contract_text = read_text(
                os.path.join(
                    tasks_dir,
                    task_id + ".md"),
                limit=1 << 16)
            if contract_text is not None:
                detail_content = contract_text
                for line in contract_text.splitlines():
                    title = clean_text(line.lstrip("# ").strip())
                    if title:
                        break

            if column in stats:
                stats[column] += 1

            tasks.append(
                {
                    "id": task_id,
                    "title": title or task_id,
                    "column": column,
                    "priority": "medium",
                    "owner": owner,
                    "tags": [],
                    "detail": detail_content,
                    "blocker": receipt.get("blocker") if receipt else None,
                    "blocked_since": None,
                    "updated_at": None,
                    "badges": list(dict.fromkeys(badges)),
                    "receipt": receipt,
                    "worktree": worktree,
                    "lineage": {"chain": chain, "source": lineage_source},
                    "superseded_by": list(superseded_by.get(task_id, [])),
                    "ext": {"worktree": worktree} if worktree else {},
                }
            )

            if receipt is not None and (
                receipt["status"] in (
                    "blocked", "failed") or receipt["next"] == "await_user"
            ) and task_id not in superseding_parents:
                if task_id not in attention:
                    attention.append(task_id)

            if receipt is not None:
                try:
                    mtime = int(
                        os.path.getmtime(
                            os.path.join(
                                receipts_dir,
                                task_id +
                                ".md")))
                except OSError:
                    mtime = 0
                activity.append(
                    {
                        "task": task_id,
                        "worker": owner,
                        "ts": mtime,
                        "kind": "receipt",
                        "fields": {
                            "status": receipt["status"],
                            "verdict": receipt["verdict"],
                            "blocker": receipt["blocker"],
                            "next": receipt["next"],
                        },
                    }
                )

        # Rework detection
        by_id = {task["id"]: task for task in tasks}
        for task in tasks:
            rec = task["receipt"]
            if rec is None or rec.get("verdict") != "fail":
                continue
            chain = task["lineage"]["chain"]
            if len(chain) < 2:
                continue
            target = by_id.get(chain[-2]["task"])
            if target is not None and "rework" not in target["badges"]:
                target["badges"].append("rework")

        lanes: List[Dict[str, Any]] = []
        for name in roster_names:
            agent = agent_by_name.get(name, {})
            is_new = name not in engaged_workers
            lanes.append(
                {
                    "id": name,
                    "label": name,
                    "role": agent.get("role", "worker"),
                    "runtime": agent.get("runtime"),
                    "new": is_new,
                    "doing_count": sum(1 for t in tasks if t["owner"] == name and t["column"] == "doing"),
                    "blocked_count": sum(1 for t in tasks if t["owner"] == name and t["column"] == "blocked"),
                    "superseded_count": sum(1 for t in tasks if t["owner"] == name and t["column"] == "superseded"),
                    "ext": {
                        "lifecycle": agent.get("lifecycle"),
                        "session": clean_text(meta.get("TEAM_TMUX_SESSION")),
                    },
                }
            )

        activity.sort(key=lambda item: item.get("ts", 0), reverse=True)

        return {
            "board": {
                "id": team_name,
                "name": team_name,
                "title": clean_text(meta.get("TEAM_TASK") or meta.get("TEAM_NAME") or team_name, 120),
                "status": clean_text(meta.get("TEAM_STATUS")) or "unknown",
                "mode": self._resolve_mode(team_dir),
                "updated_at": int(time.time()),
                "ext": {"session": clean_text(meta.get("TEAM_TMUX_SESSION"))},
            },
            "lanes": lanes,
            "tasks": tasks,
            "attention": attention,
            "activity": activity[:FEED_LIMIT],
            "stats": stats,
            "adapter": "tmux-agent-teams",
            "generated_at": int(time.time()),
            "warnings": warnings,
            # Aliases for backwards compatibility
            "team": {
                "name": team_name,
                "status": clean_text(meta.get("TEAM_STATUS")) or "unknown",
                "mode": self._resolve_mode(team_dir),
                "session": clean_text(meta.get("TEAM_TMUX_SESSION")),
            },
            "roster": [
                {
                    "worker": l["id"],
                    "runtime": l.get("runtime"),
                    "role": l.get("role", "worker"),
                    "registered_at": None,
                    "new": l.get("new", False),
                }
                for l in lanes
            ],
            "receipts_feed": [
                {
                    "task": a["task"],
                    "worker": a["worker"],
                    "mtime": a["ts"],
                    "fields": a["fields"],
                }
                for a in activity[:FEED_LIMIT]
            ],
        }

    def get_mount_paths(self) -> List[str]:
        """Return files and directories to mount read-only in sandbox mode."""
        teams_root = self._resolve_teams_root()
        paths: List[str] = []

        try:
            entries = sorted(os.listdir(teams_root))
        except OSError:
            return paths

        for name in entries:
            if not valid_name(name):
                continue
            team_dir = os.path.join(teams_root, name)
            if not os.path.isdir(team_dir):
                continue
            for f in TEAM_FILES:
                fp = os.path.join(team_dir, f)
                if os.path.exists(fp):
                    paths.append(fp)
            for d in TEAM_DIRS:
                dp = os.path.join(team_dir, d)
                if os.path.exists(dp):
                    paths.append(dp)
        return paths
