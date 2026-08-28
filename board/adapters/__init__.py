"""board.adapters — Adapter registry and factory."""

import os
from typing import Optional

try:
    from .base import BaseAdapter
    from .native import NativeAdapter
    from .teams import TeamsAdapter
except (ImportError, ValueError):
    from base import BaseAdapter
    from native import NativeAdapter
    from teams import TeamsAdapter

__all__ = ["BaseAdapter", "NativeAdapter", "TeamsAdapter", "get_adapter"]


def get_adapter(root_dir: str,
                adapter_type: Optional[str] = None) -> BaseAdapter:
    """Factory function returning the appropriate adapter."""
    root_dir = os.path.abspath(root_dir)

    if adapter_type in ("native", "agent-board"):
        return NativeAdapter(root_dir)
    if adapter_type in ("teams", "tmux-agent-teams"):
        return TeamsAdapter(root_dir)

    # Auto-detect
    base_name = os.path.basename(root_dir)
    if base_name == ".teams" or os.path.isdir(
            os.path.join(root_dir, ".teams")):
        return TeamsAdapter(root_dir)

    # Check for .teams signature files
    if any(
        os.path.isfile(os.path.join(root_dir, f))
        for f in ("board.tsv", "workers.tsv", "team-meta.env", "team.meta")
    ):
        return TeamsAdapter(root_dir)

    # Default to NativeAdapter
    return NativeAdapter(root_dir)
