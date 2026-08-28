#!/bin/bash
# tests/miniyaml.sh — Comprehensive boundary test suite for miniyaml.py
# Validates 14 test cases including scalar casting, indentation, sequences,
# block scalars, comments, JSON fallback, and error handling.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
BOARD_DIR="$REPO_ROOT/board"
MINIYAML="$BOARD_DIR/miniyaml.py"

TEST_TMP=$(mktemp -d "${TMPDIR:-/tmp}/miniyaml-test.XXXXXX")
cleanup() {
  rm -rf "$TEST_TMP" 2>/dev/null || true
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

pass() {
  printf 'ok - %s\n' "$1"
}

[ -f "$MINIYAML" ] || fail "missing miniyaml.py at $MINIYAML"

export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

python3 - <<'EOF'
import json
import os
import sys
from board.miniyaml import loads, dumps, safe_load, safe_load_file, MiniYAMLError

# ---------------------------------------------------------------------------
# TC-01: Basic scalars
# ---------------------------------------------------------------------------
y1 = """
k_int: 42
k_neg_int: -10
k_flt: 3.14159
k_sci: 1.5e-3
k_bool_t1: true
k_bool_t2: yes
k_bool_t3: ON
k_bool_f1: false
k_bool_f2: no
k_bool_f3: off
k_null1: null
k_null2: ~
k_null3: 
k_str: active
"""
d1 = loads(y1)
assert d1["k_int"] == 42, f"TC-01 k_int failed: {d1['k_int']}"
assert d1["k_neg_int"] == -10, f"TC-01 k_neg_int failed: {d1['k_neg_int']}"
assert abs(d1["k_flt"] - 3.14159) < 1e-6, f"TC-01 k_flt failed: {d1['k_flt']}"
assert abs(d1["k_sci"] - 0.0015) < 1e-6, f"TC-01 k_sci failed: {d1['k_sci']}"
assert d1["k_bool_t1"] is True
assert d1["k_bool_t2"] is True
assert d1["k_bool_t3"] is True
assert d1["k_bool_f1"] is False
assert d1["k_bool_f2"] is False
assert d1["k_bool_f3"] is False
assert d1["k_null1"] is None
assert d1["k_null2"] is None
assert d1["k_null3"] is None
assert d1["k_str"] == "active"
print("ok - TC-01: Basic scalars parsed correctly")

# ---------------------------------------------------------------------------
# TC-02: Quotes and Escapes
# ---------------------------------------------------------------------------
y2 = """
s_single: 'hello : world #not_comment'
s_single_esc: 'it''s a quote'
s_double: "line1\\nline2\\t\\"quoted\\""
url: "https://example.com/api#anchor"
"""
d2 = loads(y2)
assert d2["s_single"] == "hello : world #not_comment"
assert d2["s_single_esc"] == "it's a quote"
assert d2["s_double"] == 'line1\nline2\t"quoted"'
assert d2["url"] == "https://example.com/api#anchor"
print("ok - TC-02: Quotes and escapes parsed correctly")

# ---------------------------------------------------------------------------
# TC-03: Nested Mappings
# ---------------------------------------------------------------------------
y3 = """
board:
  name: payments-refactor
  config:
    mode: pipeline
    retry:
      max_attempts: 3
      interval: 10
"""
d3 = loads(y3)
assert d3["board"]["name"] == "payments-refactor"
assert d3["board"]["config"]["mode"] == "pipeline"
assert d3["board"]["config"]["retry"]["max_attempts"] == 3
assert d3["board"]["config"]["retry"]["interval"] == 10
print("ok - TC-03: Nested mappings parsed correctly")

# ---------------------------------------------------------------------------
# TC-04: Scalar Lists
# ---------------------------------------------------------------------------
y4 = """
fruits:
  - apple
  - banana
  - cherry
  - 100
  - true
"""
d4 = loads(y4)
assert d4["fruits"] == ["apple", "banana", "cherry", 100, True]
print("ok - TC-04: Scalar lists parsed correctly")

# ---------------------------------------------------------------------------
# TC-05: Object Lists
# ---------------------------------------------------------------------------
y5 = """
lanes:
  - id: impl-a
    label: "Worker A"
    runtime: claude
  - id: reviewer
    label: "Code Reviewer"
    runtime: codex
"""
d5 = loads(y5)
assert len(d5["lanes"]) == 2
assert d5["lanes"][0]["id"] == "impl-a"
assert d5["lanes"][0]["label"] == "Worker A"
assert d5["lanes"][1]["runtime"] == "codex"
print("ok - TC-05: Object lists parsed correctly")

# ---------------------------------------------------------------------------
# TC-06: Block Scalars (| and |-)
# ---------------------------------------------------------------------------
y6 = """
task:
  id: T005
  detail: |
    Line 1 of description.
    Line 2 with code:
      def run():
          return 42
    Line 3 ends.
  stripped_detail: |-
    No trailing newline here.
"""
d6 = loads(y6)
assert "def run():\n      return 42" in d6["task"]["detail"]
assert d6["task"]["detail"].endswith("\n")
assert not d6["task"]["stripped_detail"].endswith("\n")
print("ok - TC-06: Block scalars (| and |-) parsed correctly")

# ---------------------------------------------------------------------------
# TC-07: Flow Sequences [a, b, c]
# ---------------------------------------------------------------------------
y7 = """
tags: [hot, refund, "p:1", 100, false]
empty_list: []
"""
d7 = loads(y7)
assert d7["tags"] == ["hot", "refund", "p:1", 100, False]
assert d7["empty_list"] == []
print("ok - TC-07: Flow sequences parsed correctly")

# ---------------------------------------------------------------------------
# TC-08: Comment Stripping and Blank Lines
# ---------------------------------------------------------------------------
y8 = """
# Top header comment
version: 1 # version number

# Middle section comment
board:
  # Lane comment
  name: alpha # inline comment

# Trailing comment
"""
d8 = loads(y8)
assert d8["version"] == 1
assert d8["board"]["name"] == "alpha"
print("ok - TC-08: Comments and blank lines filtered correctly")

# ---------------------------------------------------------------------------
# TC-09: Fast-Path JSON String
# ---------------------------------------------------------------------------
j9 = '{"version": 1, "board": {"name": "json-board", "status": "active"}, "tasks": [{"id": "T1"}]}'
d9 = loads(j9)
assert d9["version"] == 1
assert d9["board"]["name"] == "json-board"
assert d9["tasks"][0]["id"] == "T1"
print("ok - TC-09: Fast-path JSON parsed correctly")

# ---------------------------------------------------------------------------
# TC-10: Unicode, Chinese & Emoji
# ---------------------------------------------------------------------------
y10 = """
board:
  title: "退款链路重构 🚀"
  description: "处理 ¥100.00 订单退款，确保幂等性 🎯"
"""
d10 = loads(y10)
assert d10["board"]["title"] == "退款链路重构 🚀"
assert "¥100.00" in d10["board"]["description"]
print("ok - TC-10: Unicode & Chinese & Emoji parsed correctly")

# ---------------------------------------------------------------------------
# TC-11: Tab Indentation Rejection
# ---------------------------------------------------------------------------
y11 = "board:\n\tname: tab_error"
try:
    loads(y11)
    raise AssertionError("TC-11 failed: tab was not rejected")
except MiniYAMLError as e:
    assert "Tabs are not allowed" in str(e)
    print("ok - TC-11: Tab indentation rejected with MiniYAMLError")

# ---------------------------------------------------------------------------
# TC-12: Unparsable YAML with JSON Fallback
# ---------------------------------------------------------------------------
# Input has braces / JSON-like syntax that is invalid simple YAML but valid JSON
j12 = '{"error": false, "data": {"items": [1, 2, 3]}}'
d12 = loads(j12)
assert d12["data"]["items"] == [1, 2, 3]
print("ok - TC-12: Automatic JSON fallback succeeded")

# ---------------------------------------------------------------------------
# TC-13: safe_load and safe_load_file
# ---------------------------------------------------------------------------
tmp_file = "/tmp/miniyaml_tc13.yaml"
with open(tmp_file, "w", encoding="utf-8") as f:
    f.write("name: safe_file_test\ncount: 99\n")
try:
    d13 = safe_load_file(tmp_file)
    assert d13["name"] == "safe_file_test"
    assert d13["count"] == 99
    
    with open(tmp_file, "r", encoding="utf-8") as f:
        d13_stream = safe_load(f)
    assert d13_stream["name"] == "safe_file_test"
    print("ok - TC-13: safe_load and safe_load_file succeeded")
finally:
    if os.path.exists(tmp_file):
        os.remove(tmp_file)

# ---------------------------------------------------------------------------
# TC-14: Dumps and Dump Roundtrip
# ---------------------------------------------------------------------------
sample_obj = {
    "version": 1,
    "board": {
        "name": "roundtrip",
        "active": True,
        "score": 98.5
    },
    "lanes": [
        {"id": "w1", "name": "Worker 1"},
        {"id": "w2", "name": "Worker 2"}
    ],
    "detail": "Line 1\nLine 2\nLine 3"
}
serialized = dumps(sample_obj)
reloaded = loads(serialized)
assert reloaded["version"] == 1
assert reloaded["board"]["name"] == "roundtrip"
assert reloaded["lanes"][0]["id"] == "w1"
assert "Line 2" in reloaded["detail"]
print("ok - TC-14: dumps and reload roundtrip succeeded")

print("\nALL 14 MINIYAML TESTS PASSED CLEANLY.")
EOF

pass "miniyaml.sh execution completed successfully"
