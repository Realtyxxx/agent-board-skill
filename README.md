# agent-board-skill

通用多 Agent / 单 Agent 任务与状态可视化看板技能。支持声明式受限 YAML / JSON 任务流（原生 `.agent-board` 模式）与 `tmux-agent-teams` 团队编排（`.teams` 模式），提供基于 Python 3 纯标准库的 MiniYAML 解析器、Sample-D Web 前端与跨平台只读沙箱隔离。

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

## 核心特性

| 特性                   | 说明                                                                                                                                 |
| :--------------------- | :----------------------------------------------------------------------------------------------------------------------------------- |
| **纯标准库零依赖**     | 核心模块（`miniyaml.py`、`serve.py`、`adapters/`）全部基于 Python 3 标准库，无需 `pip install` 任何第三方包。                        |
| **MiniYAML 解析器**    | 支持受限 YAML 子集（缩进映射、列表项、标量类型、块标量 `\|`、注释），解析异常自动降级 `json.loads`。                                 |
| **双模式适配器**       | 1. `native` 适配器：读取 `.agent-board/` YAML/JSON 规范；<br>2. `tmux-agent-teams` 适配器：读取 `.teams/` TSV 表格与 Markdown 回执。 |
| **三级 Lineage 降级**  | `events.jsonl` / `flow.tsv` $\rightarrow$ 任务卡片 `parent` 字段 $\rightarrow$ ID 前缀命名启发式。                                   |
| **Sample-D 响应式 UI** | 动态角色泳道、Attention 焦点面板、Activity 回执混流、轨迹时间线及滑出式任务详情抽屉。                                                |
| **严格只读与沙箱隔离** | 支持 Linux `bwrap` 与 macOS `sandbox-exec`；`artifacts/` 产物目录绝对不挂载；非 GET 请求统一返回 HTTP 405。                          |

---

## 快速安装

```bash
# 克隆仓库后安装至 ~/.agents/skills/agent-board
node bin/install.js --force

# 或安装至 Claude / Codex skills 目录
node bin/install.js --claude --force
node bin/install.js --codex --force
```

---

## 快速开始

### 1. 原生模式 (`.agent-board/`)

在项目根目录创建 `.agent-board/board.yaml`：

```yaml
version: 1
board:
  name: my-project
  title: "我的项目任务看板"
  status: active

lanes:
  - id: worker-a
    label: "研发席位 A"
    runtime: claude
```

启动看板：

```bash
bash board/run-sandboxed.sh --root .agent-board --port 8737
```

### 2. 团队模式 (`.teams/`)

```bash
bash board/run-sandboxed.sh --adapter teams --root .teams --port 8737
```

打开浏览器访问 `http://127.0.0.1:8737`。

---

## 测试套件

运行完整自动化测试套件：

```bash
# 运行全部 4 大测试套件
npm test

# 或单独运行特定套件
bash tests/miniyaml.sh          # MiniYAML 14 组边界测试
bash tests/native-adapter.sh    # 原生适配器与三级 Lineage 测试
bash tests/teams-adapter.sh     # Teams 适配器兼容性测试
bash tests/board-sandbox.sh     # 只读沙箱与安全隔离断言测试
```

---

## License

MIT
