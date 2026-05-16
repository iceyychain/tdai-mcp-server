# TDAI MCP Server

MCP Server wrapper for [TencentDB Agent Memory](https://github.com/Tencent/TencentDB-Agent-Memory) — a hierarchical memory engine that provides **short-term compression** (Mermaid symbol canvas) and **long-term personalized memory** (L0→L1→L2→L3 layering) for AI agents.

Any MCP-compatible client (Trae IDE, Claude Desktop, Cursor, etc.) can leverage this server to give its agent persistent, structured memory.

---

## Architecture

The server supports two backend modes:

### Direct mode (default, no Node.js required)

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
- `pip` install: `mcp`, `httpx`, `aiofiles`

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/tdai-mcp-server.git
cd tdai-mcp-server

# Install dependencies
pip install mcp httpx aiofiles
```

### Run

```bash
# Direct mode (default, no Node.js needed)
export TDAI_BACKEND=direct
export TDAI_DATA_DIR=./data
python -m src.server
```

### Configure as MCP Server

Add to your `mcp.json` (Trae: `.trae/mcp.json`, Claude Desktop: `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "tdai-memory": {
      "description": "TencentDB Agent Memory — hierarchical memory engine",
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "/path/to/tdai-mcp-server",
      "env": {
        "TDAI_BACKEND": "direct",
        "TDAI_DATA_DIR": "/path/to/tdai-mcp-server/data",
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

> **User-level config (works for all projects):** Place the `mcp.json` in `~/.trae/mcp.json` (Trae) or `~/Library/Application Support/Claude/claude_desktop_config.json` (Claude).

---

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `TDAI_BACKEND` | `gateway` | Backend mode: `gateway` or `direct` |
| `TDAI_DATA_DIR` | `~/.openclaw/memory-tdai/` | Path to memory data directory |
| `MCP_TRANSPORT` | `stdio` | MCP transport: `stdio` or `sse` |
| `MCP_HOST` | `127.0.0.1` | SSE server host |
| `MCP_PORT` | `8421` | SSE server port |
| `LLM_API_KEY` | — | API key for LLM-based extraction (direct mode) |
| `LLM_BASE_URL` | `https://api.lkeap.cloud.tencent.com/v1` | LLM API endpoint |
| `LLM_MODEL` | `deepseek-v3.2` | LLM model name |

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

> ¹ Requires `LLM_API_KEY` to be configured (Direct mode) or Gateway pipeline.

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
# Install the TDAI package
npm install -g @tencentdb-agent-memory/memory-tencentdb

# Or use a local clone
git clone https://github.com/Tencent/TencentDB-Agent-Memory.git
```

Then set `TDAI_BACKEND=gateway` (default). The MCP Server automatically starts the Gateway as a subprocess.

---

## Data Interoperability

The SQLite schema (`data/store/memory.db`) is compatible with the original TencentDB-Agent-Memory. You can:
- Point `TDAI_DATA_DIR` to an existing `~/.openclaw/memory-tdai/` and read memories accumulated by OpenClaw
- Write memories with this MCP Server and read them back from the original plugin

---

## License

MIT

## Acknowledgments

Built on top of [TencentDB Agent Memory](https://github.com/Tencent/TencentDB-Agent-Memory) (MIT License) — an open-source hierarchical memory engine by Tencent.
