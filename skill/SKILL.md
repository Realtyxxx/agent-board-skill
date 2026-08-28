---
name: agent-board
description: "通用多 Agent / 单 Agent 任务与状态可视化看板技能。支持受限 YAML/JSON 声明式任务流管理（原生 .agent-board 模式），无缝对接 tmux-agent-teams 团队编排（.teams 模式）。内置纯 Python 3 标准库零依赖 MiniYAML 解析器，提供 Sample-D 响应式泳道与任务详情抽屉，保障严格只读沙箱与写入权隔离。触发词：看板, 任务看板, agent board, kanban, todo board, 查看进度, 查看任务, 任务追踪。"
---

# agent-board — 通用多 Agent 任务与状态可视化看板

`agent-board` 是一个面向多 Agent 协作与任务追踪的通用轻量看板技能，采用**数据与展示分离、严格读写隔离、零第三方依赖**的设计原则。

```
+-----------------------------------------------------------------------------------+
|                               agent-board Web UI                                  |
|  (Sample-D: 动态角色泳道 + 焦点决策面板 + 回执混流 + 轨迹时间线 + 任务详情抽屉)   |
+-----------------------------------------+-----------------------------------------+
                                          | GET /api/board (Unified JSON Contract)
                                          v
+-----------------------------------------------------------------------------------+
|                        board/serve.py (Python stdlib 只读服务)                    |
|                        - GET /                  -> index.html                     |
|                        - GET /api/board         -> 核心契约 JSON                  |
|                        - GET /api/boards        -> 看板/团队列表                  |
|                        - POST/PUT/DELETE...     -> 405 Method Not Allowed         |
+--------------------+--------------------------------------+-----------------------+
                     |                                      |
                     v                                      v
+------------------------------------+   +------------------------------------------+
|          native 适配器             |   |          tmux-agent-teams 适配器         |
|        (.agent-board/ 根目录)      |   |             (.teams/ 根目录)             |
|------------------------------------|   |------------------------------------------|
| - board.yaml (元数据/泳道/任务)    |   | - board.tsv / flow.tsv                   |
| - tasks/<id>.yaml (独立任务卡片)   |   | - worktrees.tsv / agents.tsv             |
| - receipts/<id>.yaml (执行回执)    |   | - workers.tsv / team-meta.env            |
| - events.jsonl (追加式事件流)      |   | - tasks/<id>.md / receipts/<id>.md       |
|------------------------------------|   |------------------------------------------|
| 三级 Lineage 降级:                 |   | 三级 Lineage 降级:                       |
| events.jsonl -> parent -> 启发式   |   | flow.tsv -> 派工顺序 -> 启发式           |
+------------------------------------+   +------------------------------------------+
```

---

## 1. 适用场景与双模式支持

| 模式                   | 适用场景                                                         | 数据存储目录     | 核心特征                                                                          |
| :--------------------- | :--------------------------------------------------------------- | :--------------- | :-------------------------------------------------------------------------------- |
| **`native`**           | 单 Agent 待办追踪、多步骤工作流、或基于 YAML/JSON 的任务卡片驱动 | `.agent-board/`  | 支持人类可读的受限 YAML 与机器可读 JSON；任务卡片与回执完全独立解耦；支持事件流。 |
| **`tmux-agent-teams`** | 分布式 tmux pane 多 Agent 团队编排                               | `.teams/<team>/` | 兼容 TSV 表格、Markdown 回执、Git Worktree 状态与 MR/分支联动。                   |

---

## 2. 原生数据层规范 (`.agent-board/`)

### 2.1 目录结构

```text
.agent-board/
├── board.yaml               # [必需] 看板元数据、泳道 (Lanes)、全局配置
├── tasks/                   # [推荐] 独立任务卡片目录 (一任务一文件，写入隔离)
│   ├── T001.yaml
│   ├── T002.yaml
│   └── T005-verify.yaml
├── receipts/                # [可选] 执行回执目录 (由 Worker 完成任务后写入)
│   ├── T001.yaml
│   └── T005-verify.yaml
├── events.jsonl             # [可选] 机器追加式时间线事件流水 (最高优先级 Lineage)
└── notes/                   # [可选] 详情扩展长文档 (Markdown)
    ├── T001.md
    └── T005-verify.md
```

### 2.2 `board.yaml` 示例

```yaml
version: 1
board:
  name: payments-refactor
  title: "退款链路重构"
  description: "重构退款重试与对账"
  status: active
  mode: pipeline

lanes:
  - id: impl-a
    label: "实现 A"
    role: worker
    runtime: claude
  - id: reviewer
    label: "代码评审"
    role: reviewer
    runtime: codex
```

### 2.3 `tasks/<id>.yaml` 示例

```yaml
id: T005-verify
title: "验证退款路径改造"
owner: reviewer
priority: high
parent: T005
tags:
  - refund
  - p1
detail: |
  1. 验证退款重试幂等性
  2. 运行 tests/refund-idempotency.sh
```

### 2.4 `receipts/<id>.yaml` 示例

```yaml
agent_board_receipt_v1:
  task: T005-verify
  worker: reviewer
  status: completed
  verdict: pass
  next: deliver
  blocker: none
  artifact: artifacts/T005.md
  summary: "测试用例全部通过，断言无数据漂移"
```

---

## 3. MiniYAML 解析器子集规范

为了保证零第三方依赖（无需安装 `PyYAML`），看板内置纯标准库 `miniyaml.py`：

1. **缩进**: 统一使用 2 空格缩进，**严禁使用 Tab 制表符**。
2. **标量类型**: 支持整数、浮点数、布尔值（`true`/`false`/`yes`/`no`/`on`/`off`）、空值（`null`/`~`/留空）、单双引号字符串与转义。
3. **块标量**: 支持 `|`（保留换行）与 `|-`（去除末尾换行），用于多行任务描述。
4. **注释**: 支持 `#` 整行注释与行尾注释（引号内部的 `#` 安全保护）。
5. **JSON Fallback**: 遇到以 `{` 或 `[` 开头的合法 JSON，或 YAML 解析遇到语法异常时，自动回退到 `json.loads`。

---

## 4. 启动与运行指令

### 4.1 沙箱启动（推荐）

```bash
# 1. 原生模式（自动探测或指定 .agent-board）
bash board/run-sandboxed.sh --root .agent-board --port 8737

# 2. tmux-agent-teams 模式
bash board/run-sandboxed.sh --adapter teams --root .teams --port 8737

# 3. 诊断挂载计划或 Seatbelt 策略
bash board/run-sandboxed.sh --root .agent-board --print-plan
bash board/run-sandboxed.sh --root .agent-board --print-profile
```

### 4.2 直跑服务

```bash
python3 board/serve.py --root .agent-board --port 8737
```

---

## 5. 安全与写入隔离保证

1. **服务端绝对只读**: `serve.py` 拦截所有非 `GET`/`HEAD` 请求（`POST`, `PUT`, `DELETE`, `PATCH` 一律返回 `405 Method Not Allowed`），服务端零磁盘写入。
2. **内核级沙箱物理隔离**:
   - Linux 环境采用 `bwrap` 命名空间隔离；
   - macOS 环境采用 `sandbox-exec` 动态 Seatbelt Profile 强制 `(deny default)`；
   - **`artifacts/` 目录绝对不挂载**，看板进程从操作系统内核层无法读取 Worker 交付产物全文，杜绝信息越界与泄露。
3. **输入校验与路径穿越防护**: 所有的 `board` / `team` 参数和文件名必须通过 `^[A-Za-z0-9][A-Za-z0-9._-]*$` 校验，外部笔记路径若包含 `..` 自动拦截并报警。
