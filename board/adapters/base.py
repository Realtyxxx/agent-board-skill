#!/usr/bin/env python3
"""base.py — Abstract Base Class for Agent Board Data Adapters."""

import os
import re
from typing import Any, Dict, List, Optional

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
