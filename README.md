# agent-board-skill

通用多 Agent / 单 Agent 任务与状态可视化看板技能。支持受限 YAML/JSON 声明式任务流管理（原生 `.agent-board` 模式），无缝对接 `tmux-agent-teams` 团队编排（`.teams` 模式）。内置纯 Python 3 标准库零依赖 MiniYAML 解析器，提供 Sample-D 响应式泳道与任务详情抽屉，保障严格只读沙箱与写入权隔离。

```text
+===================================================================================+
|                              PRESENTATION TIER (Web UI)                           |
|   +-------------------+  +-------------------+  +-------------------+  +----------+
|   | Dynamic Swimlanes |  | Focus/Activity Feed |  | Task Detail Drawer|  | Lineage  |
+=======================+======================+======================+=============+
                                        | (GET /api/board)
                                        v
+===================================================================================+
|                      CORE SERVICE & SECURITY BOUNDARY (Focal Hub)                 |
|   +-------------------+     +-------------------------+     +-----------------+   |
|   |   miniyaml.py     | <-- |    serve.py (HTTP Hub)  | <-- | run-sandboxed.sh|   |
|   | (Stdlib YAML+JSON)|     | (Strict Read-Only 405)  |     | (macOS / Linux) |   |
+=======================+===============================+===========================+
                               |                         |
            (LOAD .agent-board)|                         |(LOAD .teams)
                               v                         v
+===================================================================================+
|                                STORAGE & ADAPTERS TIER                            |
|    +-----------------------------+          +-----------------------------+       |
|    |   Native Adapter (YAML/JSON)|          |   Teams Adapter (.teams TSV)|       |
|    +-----------------------------+          +-----------------------------+       |
|       |        |        |        |             |        |        |        |         |
|       v        v        v        v             v        v        v        x (BLOCKED)
|    board.yaml tasks/ receipts/ events.jsonl  board.tsv flow.tsv receipts/ artifacts/
+===================================================================================+
```

---

## 核心设计与架构规范

### 1. 写入权分离原则 (Write Ownership Isolation)

为了彻底杜绝多 Agent 在并发场景下的文件读写冲突，系统确立严格的单写者与文件归属原则：

| 文件路径 | 拥有者 / 写入方 | 写入机制 | 职责与设计考量 |
| :--- | :--- | :--- | :--- |
| **`.agent-board/board.yaml`** | 驱动者（Leader / 单 Agent） | 原子全量重写 (`tmp` $\to$ `rename`) | 声明看板标题、状态与泳道（Lanes）元数据 |
| **`.agent-board/tasks/<id>.yaml`** | 驱动者 / 派发方 | 单任务独立文件 | 任务卡片按 ID 拆分独立存储，多任务创建与修改互不干扰 |
| **`.agent-board/receipts/<id>.yaml`** | 该任务执行者（Worker） | 单任务单写 | 结构化执行回执（含 `status`, `verdict`, `next`, `summary` 等） |
| **`.agent-board/events.jsonl`** | 协作各方 | 追加写入（Append-Only） | 机器流转流水，永不重写，保障高频事件流转审计 |
| **`.agent-board/notes/<id>.md`** | 任务作者 | 只读消费 | 承载超长契约细节，由 `board.yaml` 或 `tasks/<id>.yaml` 按需引用 |

> **服务端只读防线**：`serve.py` 全程以只读模式运行，不创建、不修改、不执行任何文件内容。所有非 GET 请求直接拦截并返回 `HTTP 405 Method Not Allowed`。

---

### 2. 原生数据格式规范 (`.agent-board/`)

#### 2.1 看板元数据与泳道 (`board.yaml`)
```yaml
version: 1
board:
  name: payments-refactor
  title: "退款链路重构"
  status: active            # active | paused | done
lanes:                      # 泳道（角色/worker）。缺省时退化为通用 todo 单泳道
  - id: impl-a
    label: "实现 A"
    runtime: claude         # 自由文本，仅作展示
  - id: reviewer
    label: "评审"
```

#### 2.2 任务卡片 (`tasks/<task-id>.yaml`)
```yaml
id: T005
title: "改造退款回滚路径"
status: doing               # todo | doing | blocked | done (兼容 queued/active 别名)
owner: impl-a               # 对应 lanes[].id；null = 待派工池
parent: T004                # 可选，接力来源（Lineage 追踪源之一）
blocked_by: T002            # 可选，被谁阻塞（详情抽屉展示 Block At）
blocked_since: 1756366920   # 可选，阻塞起始 UTC Epoch 秒
updated_at: 1756366999      # UTC Epoch 秒
tags: [refund, hot]
detail: |                   # 契约摘要（支持多行块标量）
  契约详细内容写在这里。
detail_file: notes/T005.md  # 或引用 notes/ 下的长文详情
```

#### 2.3 执行回执 (`receipts/<task-id>.yaml`)
```yaml
agent_board_receipt_v1:
  task: T005
  worker: impl-a
  status: completed         # completed | blocked | failed
  verdict: pass             # pass | fail | unverified | not_applicable
  next: verify              # verify | rework | deliver | await_user | none
  blocker: none             # none | <task-id> | <短语>
  artifact: artifacts/T005.md
  summary: "一句话执行总结，≤120 字"
```

#### 2.4 事件流水 (`events.jsonl`)
```jsonl
{"ts":1756366000,"event":"create","task":"T005"}
{"ts":1756366100,"event":"dispatch","task":"T005","worker":"impl-a","parent":"T004"}
{"ts":1756366800,"event":"block","task":"T005","blocker":"T002"}
{"ts":1756366920,"event":"receipt","task":"T005","worker":"impl-a","status":"completed","next":"verify"}
{"ts":1756367000,"event":"handoff","task":"T005-verify","worker":"reviewer","parent":"T005"}
```

---

### 3. 三级 Lineage 降级链路

系统根据可用数据源自动降级推导任务接力与上下游流转关系：

| 优先级 | 推导来源 | 触发条件 | 标注来源 (`lineage.source`) |
| :---: | :--- | :--- | :--- |
| **Level 1** | `events.jsonl` / `flow.tsv` | 存在流转流水且链路完整 | `events` 或 `flow` |
| **Level 2** | `tasks[].parent` 字段 | 无事件流水但任务卡片声明了 `parent` | `parent-field` |
| **Level 3** | ID 命名规则启发式 | 均未提供（如 `T005` $\rightarrow$ `T005-verify`） | `heuristic`（UI 打上专属芯片） |

---

### 4. 纯标准库 MiniYAML 解析器

- **零外部依赖**：`miniyaml.py` 100% 依赖 Python 3 标准库，无需在沙箱中挂载 `site-packages`；
- **受限语法子集**：支持两空格缩进映射、列表项 `- `、标量类型（字符串、整数、布尔、`null`）、块标量 `|` 与 `#` 注释；
- **自动 JSON Fallback**：遇到非 YAML 格式但为合法 JSON 时自动通过 `json.loads` 解析，确保数据加载鲁棒；
- **容错隔离**：单个任务文件损坏仅记录 `warnings`，不中断核心服务，看板界面保留上一帧有效状态。

---

### 5. 严格沙箱隔离机制 (`run-sandboxed.sh`)

`run-sandboxed.sh` 支持跨平台运行（Linux `bwrap` / macOS `sandbox-exec` / 本地直跑），并按适配器实行白名单只读挂载：

| 适配器类型 | 只读挂载白名单 (ro-bind) | 物理禁止访问 (Blocked) |
| :--- | :--- | :--- |
| **`native`** | `.agent-board/` 目录（`board.yaml`、`tasks/`、`receipts/`、`events.jsonl`、`notes/`） | 目标项目仓库内其余所有源代码与敏感文件 |
| **`tmux-agent-teams`** | `.teams/` 下的 TSV、`receipts/`、`tasks/`、`mode.md`、`team.meta` | **`artifacts/`**（产物目录物理隔离，杜绝越界读取） |

---

## 快速安装

```bash
# 安装至 ~/.agents/skills/agent-board
node bin/install.js --force

# 安装至 Claude / Codex 技能目录
node bin/install.js --claude --force
node bin/install.js --codex --force
```

---

## 快速使用

### 1. 原生模式 (`.agent-board/`)
```bash
# 自动探测或显式指定 native 适配器
~/.agents/skills/agent-board/board/run-sandboxed.sh --root . --adapter native --port 8737
```

### 2. 团队编排模式 (`.teams/`)
```bash
# 指定 teams 适配器
~/.agents/skills/agent-board/board/run-sandboxed.sh --root .teams --adapter teams --port 8737
```

浏览器打开 `http://127.0.0.1:8737` 即可实时查看任务看板。

---

## 自动化测试套件

```bash
npm test

# 或独立运行各专项测试
bash tests/miniyaml.sh          # MiniYAML 语法解析与边界测试 (14 PASS)
bash tests/native-adapter.sh    # 原生模式数据契约与 Lineage 降级测试 (9 PASS)
bash tests/teams-adapter.sh     # Teams 模式 TSV 与 Markdown 契约兼容测试 (8 PASS)
bash tests/board-sandbox.sh     # macOS/Linux 沙箱隔离与 405 只读断言测试 (26 PASS)
```

---

## License

MIT
