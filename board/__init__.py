"""agent-board package."""
try:
    from .miniyaml import MiniYAMLError, dump, dumps, load, loads, safe_load, safe_load_file
except (ImportError, ValueError):
    from miniyaml import MiniYAMLError, dump, dumps, load, loads, safe_load, safe_load_file

__all__ = [
    "MiniYAMLError",
    "load",
    "loads",
    "safe_load",
    "safe_load_file",
    "dump",
    "dumps",
]
