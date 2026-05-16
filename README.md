# TDAI MCP Server

> [**中文版**](README.zh-CN.md) | [**English**](README.md)

MCP Server wrapper for [TencentDB Agent Memory](https://github.com/Tencent/TencentDB-Agent-Memory) — a hierarchical memory engine that provides **short-term compression** (Mermaid symbol canvas) and **long-term personalized memory** (L0→L1→L2→L3 layering) for AI agents.

Any MCP-compatible client (Trae IDE, Claude Desktop, Cursor, etc.) can leverage this server to give its agent persistent, structured memory.

---

## Architecture

The server supports two backend modes:

### Direct mode (no Node.js required)

```
MCP Client  ──STDIO──▶  Python MCP Server  ──▶  SQLite (memory.db)
                           ├── L0: conversation turns
                           ├── L1: structured memories
                           ├── L2: scenario blocks
                           └── L3: user persona
```

### Gateway mode (full pipeline, requires Node.js ≥ 22.16)

```
MCP Client  ──STDIO──▶  Python MCP Server  ──HTTP──▶  TDAI Gateway (Node.js)
                                                          ├── BM25 + Vector + RRF hybrid search
                                                          ├── Warmup, idle triggers, dedup
                                                          ├── L1 extraction → L2 scenario → L3 persona
                                                          └── Context offload (Mermaid canvas)
```

Gateway mode starts the original [TDAI Gateway](https://github.com/Tencent/TencentDB-Agent-Memory/tree/main/src/gateway) as a managed subprocess. If the Gateway is unavailable (e.g., Node.js not installed), it automatically falls back to Direct mode.

---

## Quick Start

### Prerequisites

- Python ≥ 3.11
- pip install: `mcp`, `httpx`, `aiofiles`

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/tdai-mcp-server.git
cd tdai-mcp-server

# Install Python dependencies
pip install mcp httpx aiofiles

# (Optional) For Gateway mode — install the TDAI Gateway package
npm install -g @tencentdb-agent-memory/memory-tencentdb
```

### Run

```bash
# Direct mode (default, no Node.js needed)
export TDAI_BACKEND=direct
export TDAI_DATA_DIR=./data
python -m src.server
```

### Configure as MCP Server

Add to your `mcp.json` (Trae: `.trae/mcp.json` or `~/.trae-cn/mcp.json`, Claude Desktop: `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "tdai-memory": {
      "description": "TencentDB Agent Memory — hierarchical memory engine",
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

> **User-level config (works for all projects):** Place the `mcp.json` in `~/.trae/mcp.json` (Trae) or `~/Library/Application Support/Claude/claude_desktop_config.json` (Claude).

### Global Rule for AI Agent

To make your AI agent **automatically** use tdai-memory (store conversations, search memories, build persona), add a global rule file:

**Trae IDE:** Create `~/.trae/rules/tdai-memory.md` (or `~/.trae-cn/rules/tdai-memory.md` for Chinese version)

```markdown
# TDAI Memory — Enable memory by default

## Core principle
Use tdai-memory by default without requiring explicit instructions.

## Auto-execution rules

### 1. Store conversations
Call `store_conversation` at the end of each conversation turn:
- `session_key`: use the current project name or `default`
- Store the user's core needs, preferences, and important decisions

### 2. Search memories
Automatically call `search_memories` when:
- The user makes a new request — check for relevant history first
- The user references something discussed before
- User preferences or configuration need to be referenced

### 3. Manage persona
- Call `generate_persona` after accumulating enough conversations
- Periodically call `get_persona` to understand user preferences

### 4. Session management
- Call `create_memory_session` at the start of a conversation
- Use a meaningful `session_key` (e.g., project name)
```

> **Note:** Restart your IDE after adding the rule file for it to take effect.

---

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `TDAI_BACKEND` | `gateway` | Backend mode: `gateway` or `direct` |
| `TDAI_DATA_DIR` | `~/.openclaw/memory-tdai/` | Path to memory data directory |
| `MCP_TRANSPORT` | `stdio` | MCP transport: `stdio` or `sse` |
| `MCP_HOST` | `127.0.0.1` | SSE server host |
| `MCP_PORT` | `8421` | SSE server port |
| `LLM_API_KEY` | — | API key for LLM extraction (Direct mode) |
| `LLM_BASE_URL` | `https://api.lkeap.cloud.tencent.com/v1` | LLM API endpoint |
| `LLM_MODEL` | `deepseek-v3.2` | LLM model name |
| `TDAI_LLM_API_KEY` | — | API key for LLM extraction (Gateway mode, fallback for `LLM_API_KEY`) |
| `TDAI_LLM_BASE_URL` | `https://api.lkeap.cloud.tencent.com/v1` | LLM API endpoint (Gateway mode) |
| `TDAI_LLM_MODEL` | `deepseek-v3.2` | LLM model name (Gateway mode) |
| `TDAI_LLM_MAX_TOKENS` | `4096` | Max tokens for LLM calls |
| `TDAI_LLM_TIMEOUT_MS` | `120000` | LLM request timeout in milliseconds |
| `TDAI_GATEWAY_PORT` | `8420` | Gateway HTTP server port |
| `TDAI_GATEWAY_HOST` | `127.0.0.1` | Gateway HTTP server host |

---

## Tools

| Tool | Description | L0 | L1 | L2 | L3 |
|---|---|---|---|---|---|
| `store_conversation` | Store a conversation turn | ✅ | ✅¹ | — | — |
| `store_conversation_batch` | Batch store messages | ✅ | — | — | — |
| `search_memories` | Search structured L1 memories | — | ✅ | — | — |
| `search_conversations` | Search raw conversation history | ✅ | — | — | — |
| `get_persona` | Retrieve user persona profile | — | — | — | ✅ |
| `get_scenarios` | List scenario blocks | — | — | ✅ | — |
| `generate_persona` | Trigger persona generation¹ | — | ✅ | — | ✅ |
| `generate_scenario` | Generate scenario block¹ | — | ✅ | ✅ | — |
| `create_memory_session` | Create a new session | — | — | — | — |
| `get_session_memories` | List session's L1 memories | — | ✅ | — | — |
| `get_task_canvas` | Get Mermaid task canvas | — | — | — | — |
| `offload_context` | Compress context to Mermaid¹ | ✅ | — | — | — |
| `flush_session` | Flush buffered messages | ✅ | — | — | — |
| `get_stats` | Memory system statistics | ✅ | ✅ | ✅ | ✅ |

> ¹ Requires `LLM_API_KEY` / `TDAI_LLM_API_KEY` to be configured (Direct mode) or Gateway pipeline.

---

## Resources

| URI | Description |
|---|---|
| `memory://persona` | Current user persona (L3) |
| `memory://scenarios` | All scenario blocks (L2) |
| `memory://stats` | Memory system statistics |
| `sessions://list` | List all recorded sessions |

---

## Gateway mode

For the full TDAI pipeline (hybrid BM25+embedding+RRF search, warmup triggers, L1 dedup, idle-triggered L2/L3 extraction), you need the original TencentDB-Agent-Memory package:

```bash
# Install the TDAI package globally
npm install -g @tencentdb-agent-memory/memory-tencentdb
```

Then set `TDAI_BACKEND=gateway` (default). The MCP Server automatically starts the Gateway as a managed subprocess.

### How it works

1. The Python MCP Server starts the Node.js Gateway as a subprocess
2. Gateway listens on `http://127.0.0.1:8420` (configurable via `TDAI_GATEWAY_PORT`)
3. All MCP tool calls are forwarded to the Gateway via HTTP
4. If Gateway fails to start (e.g., Node.js not installed), it falls back to Direct mode

### Troubleshooting

**Gateway fails to start:**
- Ensure Node.js ≥ 22.16 is installed: `node --version`
- Ensure the TDAI package is installed: `npm list -g @tencentdb-agent-memory/memory-tencentdb`
- Check that the data directory exists and is writable
- The server will automatically fall back to Direct mode

**LLM extraction not working:**
- Set `TDAI_LLM_API_KEY` (or `LLM_API_KEY`) in your MCP config
- Compatible with any OpenAI-compatible API (DeepSeek, Tencent Cloud LKE, etc.)

---

## Data Interoperability

The SQLite schema (`data/store/memory.db`) is compatible with the original TencentDB-Agent-Memory. You can:
- Point `TDAI_DATA_DIR` to an existing `~/.openclaw/memory-tdai/` and read memories accumulated by OpenClaw
- Write memories with this MCP Server and read them back from the original plugin

---

## Project Structure

```
tdai-mcp-server/
├── src/
│   ├── __init__.py
│   ├── server.py            # MCP server entry point & tool definitions
│   ├── config.py            # Configuration management
│   ├── gateway_client.py    # HTTP client for TDAI Gateway API
│   ├── gateway_process.py   # Gateway subprocess lifecycle manager
│   ├── memory_llm.py        # LLM extraction layer (Direct mode)
│   └── memory_store.py      # SQLite store (Direct mode)
├── docs/
│   └── 方案对比-直接模式-vs-Gateway转发.md  # Mode comparison (Chinese)
├── .env.example             # Environment variable template
├── .gitignore
├── pyproject.toml
└── README.md
```

---

## License

MIT

## Acknowledgments

Built on top of [TencentDB Agent Memory](https://github.com/Tencent/TencentDB-Agent-Memory) (MIT License) — an open-source hierarchical memory engine by Tencent.