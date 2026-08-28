#!/usr/bin/env python3
"""miniyaml.py — Pure Python 3 stdlib restricted YAML parser and serializer.

Features:
  - Pure Python stdlib, zero external dependencies.
  - Restricted YAML subset:
    * Mappings (key: value, nested indentation)
    * Sequences (- item, - key: value, inline [a, b, c])
    * Scalar types (strings, ints, floats, booleans, null)
    * Block scalars (| and |-) for multiline markdown/text
    * Full-line and inline comments (#) with quote protection
  - Rejection of tab indentation (\t).
  - Automatic JSON fast-path and graceful json.loads fallback on YAML syntax error.
  - safe_load, safe_load_file, load, loads, dump, dumps.
"""

import io
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

__all__ = [
    "load",
    "loads",
    "safe_load",
    "safe_load_file",
    "dump",
    "dumps",
    "MiniYAMLError",
]


class MiniYAMLError(ValueError):
    """Raised when YAML syntax is invalid and JSON fallback fails."""

    def __init__(
        self,
        message: str,
        line_no: int = 0,
        col: int = 0,
        snippet: str = "",
    ):
        self.message = message
        self.line_no = line_no
        self.col = col
        self.snippet = snippet
        detail = f"MiniYAMLError at line {line_no}"
        if col:
            detail += f", col {col}"
        detail += f": {message}"
        if snippet:
            detail += f" (near {snippet!r})"
        super().__init__(detail)


_INT_RE = re.compile(r"^[-+]?(0|[1-9][0-9]*)$")
_FLOAT_RE = re.compile(r"^[-+]?[0-9]+\.[0-9]+([eE][-+]?[0-9]+)?$")
_BOOL_TRUE = {"true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"}
_BOOL_FALSE = {"false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"}
_NULL_VALS = {"null", "Null", "NULL", "~", ""}


def _strip_inline_comment(line: str) -> str:
    """Strip comment # unless it is enclosed within single or double quotes."""
    in_single = False
    in_double = False
    escape = False
    for i, ch in enumerate(line):
        if ch == "\\" and in_double:
            escape = not escape
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single and not escape:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i].rstrip()
        if escape and ch != "\\":
            escape = False
    return line.rstrip()


def _parse_scalar(val: str) -> Any:
    """Parse a scalar token into bool, null, int, float, or string."""
    val = val.strip()
    if not val or val in _NULL_VALS:
        return None
    if val in _BOOL_TRUE:
        return True
    if val in _BOOL_FALSE:
        return False
    if _INT_RE.match(val):
        try:
            return int(val)
        except ValueError:
            pass
    if _FLOAT_RE.match(val):
        try:
            return float(val)
        except ValueError:
            pass

    # Single-quoted string
    if len(val) >= 2 and val.startswith("'") and val.endswith("'"):
        return val[1:-1].replace("''", "'")

    # Double-quoted string
    if len(val) >= 2 and val.startswith('"') and val.endswith('"'):
        try:
            return json.loads(val)
        except Exception:
            try:
                return val[1:-1].encode("utf-8").decode("unicode_escape")
            except Exception:
                return val[1:-1]

    # Flow sequence [a, b, c]
    if val.startswith("[") and val.endswith("]"):
        try:
            return json.loads(val)
        except Exception:
            inner = val[1:-1].strip()
            if not inner:
                return []
            items = []
            cur: List[str] = []
            in_s = False
            in_d = False
            esc = False
            for ch in inner:
                if ch == "\\" and in_d:
                    esc = not esc
                    cur.append(ch)
                    continue
                if ch == "'" and not in_d:
                    in_s = not in_s
                elif ch == '"' and not in_s and not esc:
                    in_d = not in_d
                elif ch == "," and not in_s and not in_d:
                    items.append(_parse_scalar("".join(cur)))
                    cur = []
                    continue
                if esc and ch != "\\":
                    esc = False
                cur.append(ch)
            if cur:
                items.append(_parse_scalar("".join(cur)))
            return items

    return val


def _parse_yaml_lines(text: str) -> Any:
    """Parse YAML text using indentation-based token stack."""
    raw_lines = text.splitlines()
    processed: List[Tuple[int, int, str, Any]] = []

    i = 0
    while i < len(raw_lines):
        line_no = i + 1
        raw_line = raw_lines[i]

        # Disallow tabs in leading whitespace
        lstripped = raw_line.lstrip()
        leading_ws = raw_line[: len(raw_line) - len(lstripped)]
        if "\t" in leading_ws:
            raise MiniYAMLError("Tabs are not allowed for indentation", line_no, 1, raw_line)

        clean_line = _strip_inline_comment(raw_line)
        if not clean_line.strip():
            i += 1
            continue

        indent = len(clean_line) - len(clean_line.lstrip(" "))
        content = clean_line.strip()

        # Reject unsupported features like anchor/alias or multi-doc
        if content.startswith("---") or content.startswith("..."):
            i += 1
            continue
        if re.search(r"(&|\*)[A-Za-z0-9_-]+", content):
            raise MiniYAMLError("YAML anchors and aliases are not supported", line_no, indent, content)

        # Check for block scalar: key: | or key: |- or - key: |
        is_list_item = content.startswith("- ") or content == "-"
        actual_content = content[2:].strip() if content.startswith("- ") else content

        if ":" in actual_content:
            k, sep, v = actual_content.partition(":")
            v = v.strip()
            if v in ("|", "|-", ">", ">-"):
                key_name = k.strip()
                block_lines: List[str] = []
                base_indent: Optional[int] = None
                j = i + 1
                while j < len(raw_lines):
                    next_raw = raw_lines[j]
                    next_clean = next_raw.rstrip()
                    if not next_clean.strip():
                        block_lines.append("")
                        j += 1
                        continue
                    next_indent = len(next_clean) - len(next_clean.lstrip(" "))
                    if next_indent <= indent:
                        break
                    if base_indent is None:
                        base_indent = next_indent
                    block_lines.append(next_clean[base_indent:] if len(next_clean) >= base_indent else "")
                    j += 1

                block_text = "\n".join(block_lines)
                if v in ("|-", ">-"):
                    block_text = block_text.rstrip("\n")
                elif not block_text.endswith("\n"):
                    block_text = block_text + "\n"

                if is_list_item:
                    processed.append((line_no, indent, "list_item_block_scalar", (key_name, block_text)))
                else:
                    processed.append((line_no, indent, "block_scalar", (key_name, block_text)))
                i = j
                continue

        processed.append((line_no, indent, "raw", content))
        i += 1

    if not processed:
        return None

    # Construct AST structure using stack
    # Stack entries: (indent_level, container, key_name)
    root: Any = None
    first_tok = processed[0]
    if first_tok[2] == "list_item_block_scalar" or (first_tok[2] == "raw" and str(first_tok[3]).startswith("-")):
        root = []
    else:
        root = {}

    stack: List[Tuple[int, Any, Optional[str]]] = [(-1, root, None)]

    for line_no, indent, line_type, payload in processed:
        # Pop containers that are deeper than or equal to current indent
        while len(stack) > 1 and indent <= stack[-1][0]:
            top_indent, top_container, top_key = stack.pop()
            if top_key is not None and isinstance(top_container, dict) and not top_container:
                grandparent = stack[-1][1]
                if isinstance(grandparent, dict) and grandparent.get(top_key) is top_container:
                    grandparent[top_key] = None

        parent = stack[-1][1]

        if line_type == "block_scalar":
            key_name, block_text = payload
            if not isinstance(parent, dict):
                raise MiniYAMLError(
                    f"Unexpected mapping key in list context: {key_name}",
                    line_no,
                    indent,
                    str(payload))
            parent[key_name] = block_text
            continue

        if line_type == "list_item_block_scalar":
            key_name, block_text = payload
            # If parent is an empty dict placeholder from `key:`, convert it to list
            if isinstance(parent, dict) and not parent and len(stack) > 1:
                key_in_gp = stack[-1][2]
                grandparent = stack[-2][1]
                if isinstance(grandparent, dict) and key_in_gp:
                    new_list: List[Any] = []
                    grandparent[key_in_gp] = new_list
                    stack[-1] = (stack[-1][0], new_list, key_in_gp)
                    parent = new_list

            if not isinstance(parent, list):
                raise MiniYAMLError("Unexpected list item in mapping context", line_no, indent, str(payload))
            item_dict = {key_name: block_text}
            parent.append(item_dict)
            stack.append((indent, item_dict, key_name))
            continue

        content = str(payload)

        # Handle list item
        if content.startswith("-"):
            item_val = content[1:].strip()
            # If parent is an empty dict placeholder from `key:`, convert it to list
            if isinstance(parent, dict) and not parent and len(stack) > 1:
                key_in_gp = stack[-1][2]
                grandparent = stack[-2][1]
                if isinstance(grandparent, dict) and key_in_gp:
                    new_list_obj: List[Any] = []
                    grandparent[key_in_gp] = new_list_obj
                    stack[-1] = (stack[-1][0], new_list_obj, key_in_gp)
                    parent = new_list_obj

            if not isinstance(parent, list):
                raise MiniYAMLError(f"Unexpected list item in mapping context: {content}", line_no, indent, content)

            if not item_val:
                item_dict = {}
                parent.append(item_dict)
                stack.append((indent, item_dict, None))
            elif ":" in item_val:
                k, _, v = item_val.partition(":")
                key = k.strip()
                val_str = v.strip()
                item_dict = {}
                parent.append(item_dict)
                if val_str:
                    item_dict[key] = _parse_scalar(val_str)
                    stack.append((indent, item_dict, key))
                else:
                    sub_dict: Dict[str, Any] = {}
                    item_dict[key] = sub_dict
                    stack.append((indent, item_dict, key))
                    stack.append((indent + 2, sub_dict, None))
            else:
                parent.append(_parse_scalar(item_val))
            continue

        # Handle key: value mapping
        if ":" in content:
            k, _, v = content.partition(":")
            key = k.strip()
            val_str = v.strip()

            if not isinstance(parent, dict):
                raise MiniYAMLError(f"Unexpected mapping key in list context: {key}", line_no, indent, content)

            if not val_str:
                sub_container: Dict[str, Any] = {}
                parent[key] = sub_container
                stack.append((indent, sub_container, key))
            else:
                parent[key] = _parse_scalar(val_str)
            continue

        raise MiniYAMLError(f"Malformed YAML syntax: {content}", line_no, indent, content)

    # Final cleanup of any trailing empty placeholder dicts
    while len(stack) > 1:
        top_indent, top_container, top_key = stack.pop()
        if top_key is not None and isinstance(top_container, dict) and not top_container:
            grandparent = stack[-1][1]
            if isinstance(grandparent, dict) and grandparent.get(top_key) is top_container:
                grandparent[top_key] = None

    return root


def loads(text: str) -> Any:
    """Parse YAML string (or JSON string via fast-path / fallback)."""
    if not isinstance(text, str):
        raise TypeError(f"Expected str, got {type(text).__name__}")

    stripped = text.strip()
    if not stripped:
        return None

    # Fast path: If it starts with JSON brackets, try json.loads directly
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(text)
        except Exception:
            pass

    try:
        return _parse_yaml_lines(text)
    except Exception as yaml_err:
        # Fallback to json.loads if YAML parsing raised error
        try:
            return json.loads(text)
        except Exception:
            if isinstance(yaml_err, MiniYAMLError):
                raise yaml_err
            raise MiniYAMLError(str(yaml_err), line_no=1, col=1, snippet=stripped[:40])


def load(fp) -> Any:
    """Read and parse from a file-like object."""
    return loads(fp.read())


def safe_load(stream_or_text: Union[str, io.IOBase, Any]) -> Any:
    """Safe load YAML or JSON from a string or file stream."""
    if hasattr(stream_or_text, "read"):
        return loads(stream_or_text.read())
    if isinstance(stream_or_text, (bytes, bytearray)):
        return loads(stream_or_text.decode("utf-8", errors="replace"))
    return loads(str(stream_or_text))


def safe_load_file(filepath: str) -> Any:
    """Read and parse YAML or JSON from a file path."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        return loads(f.read())


def dumps(obj: Any, indent_level: int = 0) -> str:
    """Serialize Python object to clean 2-space indented MiniYAML string."""
    indent = "  " * indent_level
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, (int, float)):
        return str(obj)
    if isinstance(obj, str):
        if "\n" in obj:
            lines = obj.splitlines()
            res = "|\n"
            for line in lines:
                res += f"{indent}  {line}\n"
            return res.rstrip("\n")
        if any(c in obj for c in (":", "#", "[", "]", "{", "}", '"', "'", "\t", "\n")):
            return json.dumps(obj, ensure_ascii=False)
        return obj
    if isinstance(obj, list):
        if not obj:
            return "[]"
        res = []
        for item in obj:
            if isinstance(item, dict):
                first = True
                for k, v in item.items():
                    val_str = dumps(v, indent_level + 2)
                    if first:
                        if isinstance(v, (dict, list)) and v:
                            res.append(f"{indent}- {k}:\n{val_str}")
                        elif isinstance(v, str) and "\n" in v:
                            res.append(f"{indent}- {k}: {val_str.lstrip()}")
                        else:
                            res.append(f"{indent}- {k}: {val_str}")
                        first = False
                    else:
                        if isinstance(v, (dict, list)) and v:
                            res.append(f"{indent}  {k}:\n{val_str}")
                        elif isinstance(v, str) and "\n" in v:
                            res.append(f"{indent}  {k}: {val_str.lstrip()}")
                        else:
                            res.append(f"{indent}  {k}: {val_str}")
            else:
                res.append(f"{indent}- {dumps(item, indent_level + 1)}")
        return "\n".join(res)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        res = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                val_str = dumps(v, indent_level + 1)
                res.append(f"{indent}{k}:\n{val_str}")
            elif isinstance(v, str) and "\n" in v:
                res.append(f"{indent}{k}: {dumps(v, indent_level)}")
            else:
                res.append(f"{indent}{k}: {dumps(v, indent_level)}")
        return "\n".join(res)
    return str(obj)


def dump(obj: Any, fp) -> None:
    """Serialize Python object to file-like object."""
    fp.write(dumps(obj))
