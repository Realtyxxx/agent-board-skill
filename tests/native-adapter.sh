#!/bin/bash
# tests/native-adapter.sh — Comprehensive test suite for NativeAdapter (.agent-board/)
# Tests:
#   1. Aggregation of board.yaml, tasks/*.yaml, receipts/*.yaml, events.jsonl, notes/*.md
#   2. Column state calculation (todo, doing, blocked, done)
#   3. 3-level lineage fallback (events.jsonl -> parent field -> heuristic ID prefix)
#   4. Detail file resolution & traversal attack prevention
#   5. Dynamic lane perception for undeclared owners
#   6. Invalid receipt sanitization and warnings
#   7. Rework badge propagation on verdict: fail

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
BOARD_DIR="$REPO_ROOT/board"

TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/native-adapter-test.XXXXXX")
TEST_ROOT=$(cd "$TEST_ROOT" && pwd -P)
DATA_DIR="$TEST_ROOT/.agent-board"

cleanup() {
  rm -rf "$TEST_ROOT" 2>/dev/null || true
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

pass() {
  printf 'ok - %s\n' "$1"
}

mkdir -p "$DATA_DIR/tasks" "$DATA_DIR/receipts" "$DATA_DIR/notes"

# 1. Setup board.yaml
cat > "$DATA_DIR/board.yaml" <<'EOF'
version: 1
board:
  name: demo-native
  title: "Native 看板测试 🎯"
  status: active
  mode: pipeline
  description: "测试原生 .agent-board 聚合能力"
lanes:
  - id: impl-a
    label: "实现 A"
    role: worker
    runtime: claude
  - id: reviewer
    label: "代码评审"
    role: reviewer
    runtime: codex
EOF

# 2. Setup tasks
cat > "$DATA_DIR/tasks/T001.yaml" <<'EOF'
id: T001
title: "已完成的任务"
owner: impl-a
priority: high
tags: [core, p0]
detail: "T001 契约详情"
EOF

cat > "$DATA_DIR/tasks/T002.yaml" <<'EOF'
id: T002
title: "进行中的任务"
owner: impl-a
priority: medium
tags: [feature]
detail: "T002 契约详情"
EOF

cat > "$DATA_DIR/tasks/T003.yaml" <<'EOF'
id: T003
title: "待派工的任务"
owner: null
priority: low
detail: "T003 待派工"
EOF

cat > "$DATA_DIR/tasks/T005.yaml" <<'EOF'
id: T005
title: "主线功能实现"
owner: impl-a
priority: high
detail: "实现核心业务逻辑"
EOF

cat > "$DATA_DIR/tasks/T005-verify.yaml" <<'EOF'
id: T005-verify
title: "主线功能验证"
owner: reviewer
priority: high
parent: T005
detail: "验证 T005 实现"
EOF

cat > "$DATA_DIR/tasks/T009.yaml" <<'EOF'
id: T009
title: "回执非法的任务"
owner: impl-a
priority: medium
EOF

cat > "$DATA_DIR/tasks/T010.yaml" <<'EOF'
id: T010
title: "引用外部笔记的任务"
owner: impl-a
priority: medium
detail_file: "notes/T010.md"
EOF

cat > "$DATA_DIR/tasks/T011.yaml" <<'EOF'
id: T011
title: "恶意路径穿越任务"
owner: impl-a
priority: low
detail_file: "../../secret.txt"
EOF

cat > "$DATA_DIR/tasks/T012.yaml" <<'EOF'
id: T012
title: "新动态席位任务"
owner: new-agent
priority: medium
detail: "由未在 board.yaml 声明的 agent 执行"
EOF

# 3. Setup notes
cat > "$DATA_DIR/notes/T010.md" <<'EOF'
# T010 长文档正文
这是来自 notes/T010.md 的外部长说明。
包含代码示例：
```python
def success():
    return True
```
EOF

# 4. Setup receipts
cat > "$DATA_DIR/receipts/T001.yaml" <<'EOF'
agent_board_receipt_v1:
  task: T001
  worker: impl-a
  status: completed
  verdict: pass
  next: deliver
  blocker: none
  artifact: artifacts/T001.md
  summary: "T001 实施完成并通过"
EOF

cat > "$DATA_DIR/receipts/T005.yaml" <<'EOF'
agent_board_receipt_v1:
  task: T005
  worker: impl-a
  status: completed
  verdict: pass
  next: verify
  blocker: none
  artifact: artifacts/T005.md
  summary: "T005 实施完成"
EOF

cat > "$DATA_DIR/receipts/T005-verify.yaml" <<'EOF'
agent_board_receipt_v1:
  task: T005-verify
  worker: reviewer
  status: completed
  verdict: fail
  next: rework
  blocker: none
  artifact: artifacts/T005-verify.md
  summary: "验证发现边界缺陷，要求返工"
EOF

cat > "$DATA_DIR/receipts/T009.yaml" <<'EOF'
agent_board_receipt_v1:
  task: T009
  worker: impl-a
  status: totally_hacked
  verdict: unknown_v
  next: none
  blocker: none
EOF

# 5. Setup events.jsonl
cat > "$DATA_DIR/events.jsonl" <<'EOF'
{"ts": 1756366000, "event": "create", "task": "T005", "actor": "leader"}
{"ts": 1756366100, "event": "dispatch", "task": "T005", "worker": "impl-a"}
{"ts": 1756366800, "event": "receipt", "task": "T005", "worker": "impl-a", "status": "completed", "verdict": "pass", "next": "verify"}
{"ts": 1756366900, "event": "dispatch", "task": "T005-verify", "worker": "reviewer", "parent": "T005"}
EOF

export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

python3 - "$DATA_DIR" <<'EOF'
import sys
import os
from board.adapters.native import NativeAdapter

data_dir = sys.argv[1]
adapter = NativeAdapter(data_dir)

# ---------------------------------------------------------------------------
# Test 1: list_boards
# ---------------------------------------------------------------------------
boards = adapter.list_boards()
assert len(boards) >= 1, "list_boards returned empty"
assert boards[0]["name"] == "demo-native"
assert boards[0]["status"] == "active"
print("ok - 1. list_boards found demo-native")

# ---------------------------------------------------------------------------
# Test 2: load_board aggregation & columns
# ---------------------------------------------------------------------------
data = adapter.load_board("demo-native")
assert data["board"]["name"] == "demo-native"
assert data["board"]["title"] == "Native 看板测试 🎯"
assert data["adapter"] == "native"

tasks = {t["id"]: t for t in data["tasks"]}
assert len(tasks) >= 9, f"expected at least 9 tasks, got {len(tasks)}"

assert tasks["T001"]["column"] == "done", f"T001 column: {tasks['T001']['column']}"
assert tasks["T001"]["receipt"]["verdict"] == "pass"

assert tasks["T002"]["column"] == "doing", f"T002 column: {tasks['T002']['column']}"
assert tasks["T003"]["column"] == "todo", f"T003 column: {tasks['T003']['column']}"
assert tasks["T009"]["column"] == "blocked", f"T009 column: {tasks['T009']['column']}"
assert tasks["T009"]["receipt"] is None, "T009 receipt was not sanitized"
assert "invalid-receipt:T009" in data["warnings"], "invalid-receipt:T009 not recorded in warnings"
print("ok - 2. Column mapping and invalid receipt sanitization passed")

# ---------------------------------------------------------------------------
# Test 3: Rework badge propagation
# ---------------------------------------------------------------------------
assert tasks["T005-verify"]["column"] == "done"
assert "verdict:fail" in tasks["T005-verify"]["badges"]
assert "rework" in tasks["T005"]["badges"], f"T005 badges missing rework: {tasks['T005']['badges']}"
print("ok - 3. Rework badge successfully propagated to predecessor")

# ---------------------------------------------------------------------------
# Test 4: Lineage Level 1 (events.jsonl)
# ---------------------------------------------------------------------------
assert tasks["T005-verify"]["lineage"]["source"] == "events"
chain_tasks = [node["task"] for node in tasks["T005-verify"]["lineage"]["chain"]]
assert chain_tasks == ["T005", "T005-verify"], f"unexpected events chain: {chain_tasks}"
print("ok - 4. Lineage Level 1 (events.jsonl) verified")

# ---------------------------------------------------------------------------
# Test 5: Detail file and traversal security
# ---------------------------------------------------------------------------
assert tasks["T010"]["detail"] is not None
assert "T010 长文档正文" in tasks["T010"]["detail"]
assert tasks["T011"]["detail"] is None, "Path traversal was not blocked"
assert "invalid-detail-path:T011" in data["warnings"], "invalid-detail-path:T011 not recorded"
print("ok - 5. Detail file and path traversal security verified")

# ---------------------------------------------------------------------------
# Test 6: Dynamic lane perception
# ---------------------------------------------------------------------------
lane_ids = [l["id"] for l in data["lanes"]]
assert "new-agent" in lane_ids, f"new-agent not in lanes: {lane_ids}"
new_lane = next(l for l in data["lanes"] if l["id"] == "new-agent")
assert new_lane["new"] is True
assert new_lane["doing_count"] == 1
print("ok - 6. Dynamic lane perception verified")

# ---------------------------------------------------------------------------
# Test 7: Lineage Level 2 (parent field without events.jsonl)
# ---------------------------------------------------------------------------
os.remove(os.path.join(data_dir, "events.jsonl"))
data_no_events = adapter.load_board("demo-native")
tasks_ne = {t["id"]: t for t in data_no_events["tasks"]}
assert tasks_ne["T005-verify"]["lineage"]["source"] == "parent-field"
chain_p = [node["task"] for node in tasks_ne["T005-verify"]["lineage"]["chain"]]
assert chain_p == ["T005", "T005-verify"], f"unexpected parent chain: {chain_p}"
print("ok - 7. Lineage Level 2 (parent-field) verified")

# ---------------------------------------------------------------------------
# Test 8: Lineage Level 3 (heuristic without events or parent)
# ---------------------------------------------------------------------------
# Overwrite T005-verify to remove parent
t_verify_path = os.path.join(data_dir, "tasks", "T005-verify.yaml")
with open(t_verify_path, "w", encoding="utf-8") as f:
    f.write("id: T005-verify\ntitle: Verify\nowner: reviewer\n")

data_heuristic = adapter.load_board("demo-native")
tasks_h = {t["id"]: t for t in data_heuristic["tasks"]}
assert tasks_h["T005-verify"]["lineage"]["source"] == "heuristic"
assert "heuristic-lineage" in tasks_h["T005-verify"]["badges"]
chain_h = [node["task"] for node in tasks_h["T005-verify"]["lineage"]["chain"]]
assert chain_h == ["T005", "T005-verify"], f"unexpected heuristic chain: {chain_h}"
print("ok - 8. Lineage Level 3 (heuristic) verified")

# ---------------------------------------------------------------------------
# Test 9: get_mount_paths strictly excludes artifacts
# ---------------------------------------------------------------------------
os.makedirs(os.path.join(data_dir, "artifacts"), exist_ok=True)
mount_paths = adapter.get_mount_paths()
for p in mount_paths:
    assert "/artifacts" not in p, f"artifacts found in mount_paths: {p}"
print("ok - 9. get_mount_paths excludes artifacts")

print("\nALL NATIVE ADAPTER TESTS PASSED CLEANLY.")
EOF

pass "native-adapter.sh execution completed successfully"
