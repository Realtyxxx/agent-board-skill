#!/usr/bin/env python3
"""base.py — Abstract Base Class for Agent Board Data Adapters."""

import os
import re
from typing import Any, Dict, List, Optional, Set

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
BLOCKER_RE = re.compile(r"^(none|[A-Za-z0-9][A-Za-z0-9._-]*)$")

RECEIPT_STATUS = ("completed", "blocked", "failed")
RECEIPT_VERDICT = ("pass", "fail", "unverified", "not_applicable")
RECEIPT_NEXT = ("verify", "rework", "deliver", "await_user", "none")

TITLE_LIMIT = 120
FEED_LIMIT = 20


def valid_name(value: Any) -> bool:
    """Validate safe identifier to prevent path traversal."""
    return isinstance(value, str) and bool(NAME_RE.match(value))


def clean_text(value: Optional[str], limit: int = TITLE_LIMIT) -> Optional[str]:
    """Clean control characters and truncate text boundedly."""
    if value is None:
        return None
    value = str(value)
    # Filter printable characters and standard spaces
    value = "".join(ch for ch in value if ch == " " or ch.isprintable())
    value = " ".join(value.split())
    if not value:
        return None
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return value


def read_text(path: str, limit: int = 1 << 20) -> Optional[str]:
    """Safely read text with a bounded byte limit."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return None


def printable_single_line(value: str) -> bool:
    """True when value has no tabs, newlines or other control characters."""
    return all(ch == " " or ch.isprintable() for ch in value)


def ancestor_chain(
    task_id: str,
    parent: Dict[str, str],
    worker_of: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Root-first chain of {task, worker} following parent links (cycle-safe)."""
    chain: List[Dict[str, Any]] = []
    seen = set()
    cursor: Optional[str] = task_id
    while cursor and cursor not in seen:
        seen.add(cursor)
        chain.append({"task": cursor, "worker": worker_of.get(cursor)})
        cursor = parent.get(cursor)
    chain.reverse()
    return chain


def heuristic_root(task_id: str, known_tasks: Set[str]) -> str:
    """Longest known '-'-separated prefix of task_id (T005-verify -> T005)."""
    parts = task_id.split("-")
    for cut in range(len(parts) - 1, 0, -1):
        candidate = "-".join(parts[:cut])
        if candidate in known_tasks:
            return candidate
    return task_id


def build_heuristic_lineage(
    order: List[str],
    known_tasks: Set[str],
    owners: Dict[str, Optional[str]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Group tasks by heuristic root; each task's chain is its group prefix."""
    groups: Dict[str, List[str]] = {}
    for task_id in order:
        groups.setdefault(heuristic_root(task_id, known_tasks), []).append(task_id)
    lineage: Dict[str, List[Dict[str, Any]]] = {}
    for root, members in groups.items():
        if root in members and members[0] != root:
            members = [root] + [t for t in members if t != root]
        chain = [{"task": t, "worker": owners.get(t)} for t in members]
        for index, task_id in enumerate(members):
            lineage[task_id] = chain[: index + 1]
    return lineage


def propagate_rework(tasks: List[Dict[str, Any]]) -> None:
    """Badge the lineage predecessor of every verdict:fail task with 'rework'."""
    by_id = {task["id"]: task for task in tasks}
    for task in tasks:
        receipt = task["receipt"]
        if receipt is None or receipt.get("verdict") != "fail":
            continue
        chain = task["lineage"]["chain"]
        if len(chain) < 2:
            continue
        target = by_id.get(chain[-2]["task"])
        if target is not None and "rework" not in target["badges"]:
            target["badges"].append("rework")


def column_stats(tasks: List[Dict[str, Any]]) -> Dict[str, int]:
    stats = {"total": len(tasks), "todo": 0, "doing": 0, "blocked": 0, "done": 0}
    for task in tasks:
        if task["column"] in stats:
            stats[task["column"]] += 1
    return stats


def lane_counts(tasks: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """Per-owner doing/blocked counts in a single pass."""
    counts: Dict[str, Dict[str, int]] = {}
    for task in tasks:
        if task["column"] in ("doing", "blocked") and task["owner"]:
            per = counts.setdefault(task["owner"], {"doing": 0, "blocked": 0})
            per[task["column"]] += 1
    return counts


class BaseAdapter:
    """Base interface for all agent-board storage adapters."""

    def __init__(self, root_dir: str, **kwargs):
        self.root_dir = os.path.abspath(root_dir)
        self.kwargs = kwargs

    def list_boards(self) -> List[Dict[str, Any]]:
        """Return list of available boards/teams: [{'name': ..., 'status': ...}]."""
        raise NotImplementedError

    def load_board(self, board_id: Optional[str] = None) -> Dict[str, Any]:
        """Aggregate and return the Unified Core JSON Contract for the board."""
        raise NotImplementedError

    def get_mount_paths(self) -> List[str]:
        """Return list of file and directory paths for read-only sandbox mounting."""
        raise NotImplementedError
