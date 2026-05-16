# TDAI MCP Server

> [**English**](README.md) | [**中文版**](README.zh-CN.md)

TDAI MCP Server 是 [TencentDB Agent Memory](https://github.com/Tencent/TencentDB-Agent-Memory) 的 MCP Server 封装——一个分层记忆引擎，为 AI 智能体提供**短期上下文压缩**（Mermaid 符号图）和**长期个性化记忆**（L0→L1→L2→L3 分层）。

任何兼容 MCP 的客户端（Trae IDE、Claude Desktop、Cursor 等）都可以通过此服务为智能体赋予持久化、结构化的记忆能力。

---

## 架构

服务支持两种后端模式：

### 直接模式（无需 Node.js）

```
MCP 客户端  ──STDIO──▶  Python MCP Server  ──▶  SQLite (memory.db)
                           ├── L0: 对话轮次
                           ├── L1: 结构化记忆
                           ├── L2: 场景块
                           └── L3: 用户画像
```

### Gateway 模式（完整管线，需要 Node.js ≥ 22.16）

```
MCP 客户端  ──STDIO──▶  Python MCP Server  ──HTTP──▶  TDAI Gateway (Node.js)
                                                          ├── BM25 + 向量 + RRF 混合搜索
                                                          ├── 预热、空闲触发、去重
                                                          ├── L1 提取 → L2 场景 → L3 画像
                                                          └── 上下文卸载（Mermaid 符号图）
```

Gateway 模式会将原始的 [TDAI Gateway](https://github.com/Tencent/TencentDB-Agent-Memory/tree/main/src/gateway) 作为托管子进程启动。如果 Gateway 不可用（例如未安装 Node.js），会自动回退到直接模式。

---

## 快速开始

### 前置条件

- Python ≥ 3.11
- pip 安装：`mcp`、`httpx`、`aiofiles`

### 安装

```bash
# 克隆仓库
git clone https://github.com/your-username/tdai-mcp-server.git
cd tdai-mcp-server

# 安装 Python 依赖
pip install mcp httpx aiofiles

# （可选）Gateway 模式需要安装 TDAI Gateway 包
npm install -g @tencentdb-agent-memory/memory-tencentdb
```

### 运行

```bash
# 直接模式（默认，无需 Node.js）
export TDAI_BACKEND=direct
export TDAI_DATA_DIR=./data
python -m src.server
```

### 配置为 MCP Server

添加到你的 `mcp.json`（Trae IDE：`.trae/mcp.json` 或 `~/.trae-cn/mcp.json`，Claude Desktop：`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "tdai-memory": {
      "description": "TencentDB Agent Memory — 分层记忆引擎",
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "/path/to/tdai-mcp-server",
      "env": {
        "TDAI_BACKEND": "gateway",
        "TDAI_DATA_DIR": "/path/to/tdai-mcp-server/data",
        "TDAI_LLM_API_KEY": "your-api-key",
        "TDAI_LLM_BASE_URL": "https://api.deepseek.com",
        "TDAI_LLM_MODEL": "deepseek-chat",
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

> **用户级配置（对所有项目生效）：** 将 `mcp.json` 放在 `~/.trae/mcp.json`（Trae IDE）或 `~/Library/Application Support/Claude/claude_desktop_config.json`（Claude Desktop）。

### AI 智能体全局规则

要让 AI 智能体**自动**使用 tdai-memory（存储对话、搜索记忆、建立画像），添加一个全局规则文件：

**Trae IDE：** 创建 `~/.trae/rules/tdai-memory.md`（中文版为 `~/.trae-cn/rules/tdai-memory.md`）

```markdown
# TDAI Memory — 默认开启记忆功能

## 核心原则
默认使用 tdai-memory 记忆系统，无需用户额外指示。

## 自动执行规则

### 1. 存储对话
每次对话结束时，调用 `store_conversation` 存储本轮对话的关键信息：
- `session_key`：使用当前项目名或 `default`
- 存储用户的核心需求、偏好、重要决定

### 2. 检索记忆
在以下情况自动调用 `search_memories`：
- 用户提出新需求时，先查是否有相关历史记忆
- 用户提到之前讨论过的内容时
- 需要参考用户偏好或配置时

### 3. 管理画像
- 积累足够对话后，调用 `generate_persona` 生成用户画像
- 定期调用 `get_persona` 了解用户偏好

### 4. 会话管理
- 首次对话时调用 `create_memory_session` 创建会话
- 使用有意义的 `session_key`（如项目名）
```

> **注意：** 添加规则文件后需要重启 IDE 才能生效。

---

## 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `TDAI_BACKEND` | `gateway` | 后端模式：`gateway` 或 `direct` |
| `TDAI_DATA_DIR` | `~/.openclaw/memory-tdai/` | 记忆数据目录路径 |
| `MCP_TRANSPORT` | `stdio` | MCP 传输方式：`stdio` 或 `sse` |
| `MCP_HOST` | `127.0.0.1` | SSE 服务器主机 |
| `MCP_PORT` | `8421` | SSE 服务器端口 |
| `LLM_API_KEY` | — | LLM 提取的 API 密钥（直接模式） |
| `LLM_BASE_URL` | `https://api.lkeap.cloud.tencent.com/v1` | LLM API 端点 |
| `LLM_MODEL` | `deepseek-v3.2` | LLM 模型名称 |
| `TDAI_LLM_API_KEY` | — | LLM 提取的 API 密钥（Gateway 模式，`LLM_API_KEY` 的回退） |
| `TDAI_LLM_BASE_URL` | `https://api.lkeap.cloud.tencent.com/v1` | LLM API 端点（Gateway 模式） |
| `TDAI_LLM_MODEL` | `deepseek-v3.2` | LLM 模型名称（Gateway 模式） |
| `TDAI_LLM_MAX_TOKENS` | `4096` | LLM 调用的最大 Token 数 |
| `TDAI_LLM_TIMEOUT_MS` | `120000` | LLM 请求超时时间（毫秒） |
| `TDAI_GATEWAY_PORT` | `8420` | Gateway HTTP 服务器端口 |
| `TDAI_GATEWAY_HOST` | `127.0.0.1` | Gateway HTTP 服务器主机 |

---

## 工具列表

| 工具 | 说明 | L0 | L1 | L2 | L3 |
|---|---|---|---|---|---|
| `store_conversation` | 存储一轮对话 | ✅ | ✅¹ | — | — |
| `store_conversation_batch` | 批量存储消息 | ✅ | — | — | — |
| `search_memories` | 搜索结构化 L1 记忆 | — | ✅ | — | — |
| `search_conversations` | 搜索原始对话历史 | ✅ | — | — | — |
| `get_persona` | 获取用户画像 | — | — | — | ✅ |
| `get_scenarios` | 列出场景块 | — | — | ✅ | — |
| `generate_persona` | 触发画像生成¹ | — | ✅ | — | ✅ |
| `generate_scenario` | 生成场景块¹ | — | ✅ | ✅ | — |
| `create_memory_session` | 创建新会话 | — | — | — | — |
| `get_session_memories` | 列出会话的 L1 记忆 | — | ✅ | — | — |
| `get_task_canvas` | 获取 Mermaid 任务画布 | — | — | — | — |
| `offload_context` | 将上下文压缩为 Mermaid¹ | ✅ | — | — | — |
| `flush_session` | 刷新缓冲消息 | ✅ | — | — | — |
| `get_stats` | 记忆系统统计 | ✅ | ✅ | ✅ | ✅ |

> ¹ 需要配置 `LLM_API_KEY` / `TDAI_LLM_API_KEY`（直接模式）或 Gateway 管线。

---

## 资源

| URI | 说明 |
|---|---|
| `memory://persona` | 当前用户画像（L3） |
| `memory://scenarios` | 所有场景块（L2） |
| `memory://stats` | 记忆系统统计 |
| `sessions://list` | 列出所有记录的会话 |

---

## Gateway 模式

要使用完整的 TDAI 管线（BM25+嵌入+RRF 混合搜索、预热触发器、L1 去重、空闲触发的 L2/L3 提取），需要安装原始的 TencentDB-Agent-Memory 包：

```bash
# 全局安装 TDAI 包
npm install -g @tencentdb-agent-memory/memory-tencentdb
```

然后设置 `TDAI_BACKEND=gateway`（默认值）。MCP Server 会自动将 Gateway 作为托管子进程启动。

### 工作原理

1. Python MCP Server 将 Node.js Gateway 作为子进程启动
2. Gateway 监听 `http://127.0.0.1:8420`（可通过 `TDAI_GATEWAY_PORT` 配置）
3. 所有 MCP 工具调用通过 HTTP 转发到 Gateway
4. 如果 Gateway 启动失败（例如未安装 Node.js），自动回退到直接模式

### 故障排查

**Gateway 启动失败：**
- 确保已安装 Node.js ≥ 22.16：`node --version`
- 确保已安装 TDAI 包：`npm list -g @tencentdb-agent-memory/memory-tencentdb`
- 检查数据目录是否存在且可写
- 服务会自动回退到直接模式

**LLM 提取不工作：**
- 在 MCP 配置中设置 `TDAI_LLM_API_KEY`（或 `LLM_API_KEY`）
- 兼容任何 OpenAI 兼容的 API（DeepSeek、腾讯云 LKE 等）

---

## 数据互通

SQLite 模式（`data/store/memory.db`）与原始的 TencentDB-Agent-Memory 兼容。你可以：
- 将 `TDAI_DATA_DIR` 指向已有的 `~/.openclaw/memory-tdai/`，读取 OpenClaw 积累的记忆
- 使用此 MCP Server 写入记忆，然后从原始插件读取

---

## 项目结构

```
tdai-mcp-server/
├── src/
│   ├── __init__.py
│   ├── server.py            # MCP 服务入口 & 工具定义
│   ├── config.py            # 配置管理
│   ├── gateway_client.py    # TDAI Gateway API 的 HTTP 客户端
│   ├── gateway_process.py   # Gateway 子进程生命周期管理
│   ├── memory_llm.py        # LLM 提取层（直接模式）
│   └── memory_store.py      # SQLite 存储（直接模式）
├── docs/
│   └── 方案对比-直接模式-vs-Gateway转发.md  # 两种模式对比
├── .env.example             # 环境变量模板
├── .gitignore
├── pyproject.toml
├── README.md                # 英文版说明
└── README.zh-CN.md          # 中文版说明
```

---

## 许可证

MIT

## 致谢

基于 [TencentDB Agent Memory](https://github.com/Tencent/TencentDB-Agent-Memory)（MIT 许可证）构建——腾讯开源的层次化记忆引擎。