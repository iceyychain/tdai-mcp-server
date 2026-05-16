"""
TDAI MCP Server — MCP Server wrapper for TencentDB Agent Memory.

Supports two backends:

1. **Gateway mode** (default, recommended)
   Starts the original Node.js TDAI Gateway (``TdaiGateway`` from
   ``src/gateway/server.ts``) as a subprocess and forwards all MCP
   tool calls via HTTP.  Benefits: full pipeline support (L1 dedup,
   L2/L3 extraction, BM25, RRF fusion, warmup, idle triggers).

2. **Direct mode** (fallback)
   Reads/writes the SQLite database directly from Python.  No Node.js
   dependency, but lacks the original extraction pipeline.

Set ``TDAI_BACKEND=direct`` to use the direct SQLite backend, or
``TDAI_BACKEND=gateway`` (default) for the Gateway forwarding mode.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP, Context

from .config import load_config, TdaiConfig
from .gateway_client import GatewayClient, GatewayHTTPError
from .gateway_process import GatewayProcess, GatewayProcessError
from .memory_llm import LLMConfig, MemoryLLM
from .memory_store import MemoryStore

logger = logging.getLogger("tdai-mcp-server")


# ---------------------------------------------------------------------------
# Lifespan context
# ---------------------------------------------------------------------------
@dataclass
class TdaiAppContext:
    config: TdaiConfig
    backend: str  # "gateway" | "direct"
    mode_label: str = ""

    # Gateway mode resources
    gw_process: GatewayProcess | None = None
    gw_client: GatewayClient | None = None

    # Direct mode resources
    store: MemoryStore | None = None
    llm: MemoryLLM | None = None

    # Session cache for gateway mode (used by store_conversation)
    _pending_sessions: dict[str, list[dict[str, str]]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Lifespan — select backend at startup
# ---------------------------------------------------------------------------
@asynccontextmanager
async def app_lifespan(mcp_server: FastMCP) -> AsyncIterator[TdaiAppContext]:
    cfg = load_config()
    backend = os.environ.get("TDAI_BACKEND", "gateway").lower()
    ctx = TdaiAppContext(config=cfg, backend=backend)

    if backend == "gateway":
        await _init_gateway_mode(ctx)
    else:
        _init_direct_mode(ctx)

    try:
        yield ctx
    finally:
        await _cleanup(ctx)


async def _init_gateway_mode(ctx: TdaiAppContext) -> None:
    cfg = ctx.config
    logger.info("TDAI MCP Server — BACKEND=gateway")
    ctx.mode_label = "Gateway forwarding"

    # Start the Gateway subprocess
    gw = GatewayProcess(
        data_dir=cfg.data_dir,
        port=8420,
        host="127.0.0.1",
        llm_api_key=cfg.llm_api_key,
        llm_base_url=cfg.llm_base_url,
        llm_model=cfg.llm_model,
    )
    try:
        await gw.start()
    except (GatewayProcessError, FileNotFoundError, OSError) as exc:
        logger.warning(
            "Gateway startup failed (%s) — falling back to direct SQLite mode. "
            "Data in ~/.openclaw/memory-tdai/ can still be read/written.",
            exc,
        )
        ctx.backend = "direct"
        _init_direct_mode(ctx)
        return

    ctx.gw_process = gw
    ctx.gw_client = GatewayClient(base_url=gw.base_url)

    # Warmup — wait for gateway health check
    import asyncio
    for attempt in range(5):
        healthy = await gw.health_check()
        if healthy:
            logger.info("Gateway health check passed")
            return
        await asyncio.sleep(1)

    logger.warning("Gateway health check timeout after 5s — continuing anyway")


def _init_direct_mode(ctx: TdaiAppContext) -> None:
    cfg = ctx.config
    logger.info("TDAI MCP Server — BACKEND=direct")
    ctx.mode_label = "Direct SQLite"

    store = MemoryStore(cfg.data_dir)
    store.connect()
    store.initialize_schema()
    ctx.store = store

    if cfg.llm_api_key:
        llm_cfg = LLMConfig(
            api_key=cfg.llm_api_key,
            base_url=cfg.llm_base_url,
            model=cfg.llm_model,
        )
        ctx.llm = MemoryLLM(llm_cfg)
        logger.info("LLM extraction layer enabled — model=%s", cfg.llm_model)
    else:
        logger.info("LLM extraction layer disabled (set LLM_API_KEY to enable)")


async def _cleanup(ctx: TdaiAppContext) -> None:
    if ctx.gw_client:
        await ctx.gw_client.close()
    if ctx.gw_process:
        await ctx.gw_process.stop()
    if ctx.llm:
        await ctx.llm.close()
    if ctx.store:
        ctx.store.close()
    logger.info("TDAI MCP Server stopped")


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "TencentDB Agent Memory",
    lifespan=app_lifespan,
)


# =========================================================================
# Helper — resolve backend dispatcher
# =========================================================================
def _backend(ctx: Context) -> str:
    return ctx.request_context.lifespan_context.backend


def _gw(ctx: Context) -> GatewayClient:
    c = ctx.request_context.lifespan_context.gw_client
    assert c is not None, "Gateway client not available (fallback to direct mode)"
    return c


def _store(ctx: Context) -> MemoryStore:
    s = ctx.request_context.lifespan_context.store
    assert s is not None, "Store not available"
    return s


def _llm(ctx: Context) -> MemoryLLM | None:
    return ctx.request_context.lifespan_context.llm


# =========================================================================
# Resources
# =========================================================================
@mcp.resource("memory://persona")
def get_persona_resource() -> str:
    ctx = mcp.get_context().request_context.lifespan_context
    if ctx.backend == "direct" or not ctx.gw_client:
        persona = ctx.store.get_active_persona() if ctx.store else None
        if persona:
            return persona["persona_text"]
        md = ctx.store.get_persona_md() if ctx.store else None
        return md or "No persona has been built yet."
    return "Use the 'get_persona' tool in gateway mode."


@mcp.resource("memory://scenarios")
def get_scenarios_resource() -> str:
    ctx = mcp.get_context().request_context.lifespan_context
    if ctx.backend == "direct" or not ctx.store:
        scenarios = ctx.store.list_l2_scenarios() if ctx.store else []
        if not scenarios:
            return "No scenarios found."
        lines = [f"## {s['name']}\n{s['content']}" for s in scenarios]
        return "\n\n".join(lines)
    return "Use the 'get_scenarios' tool in gateway mode."


@mcp.resource("memory://stats")
def get_stats_resource() -> str:
    ctx = mcp.get_context().request_context.lifespan_context
    stats: dict[str, Any] = {"backend": ctx.backend, "mode": ctx.mode_label}
    if ctx.store:
        stats.update(ctx.store.get_stats())
    stats["data_dir"] = str(ctx.config.data_dir)
    stats["llm_enabled"] = ctx.llm is not None or bool(ctx.config.llm_api_key)
    return json.dumps(stats, ensure_ascii=False, indent=2)


@mcp.resource("sessions://list")
def list_sessions_resource() -> str:
    ctx = mcp.get_context().request_context.lifespan_context
    if not ctx.store:
        return "Sessions listing not available in gateway mode (use search tools)."
    sessions = ctx.store.list_sessions(limit=50)
    if not sessions:
        return "No sessions recorded."
    lines = []
    for s in sessions:
        label = s.get("label") or s["session_key"]
        ts = s.get("updated_at", 0)
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
        lines.append(f"- **{label}** — updated {dt}")
    return "\n".join(lines)


# =========================================================================
# Tools — Gateway-aware dispatchers
# =========================================================================
@mcp.tool()
async def store_conversation(
    session_key: str,
    role: str,
    content: str,
    ctx: Context,
) -> str:
    """Store a single conversation turn (L0).

    In gateway mode, uses the full TDAI pipeline (capture + extraction).
    In direct mode, stores to SQLite with optional LLM extraction.

    Args:
        session_key: Unique identifier for the conversation session.
        role: Message role — 'user' or 'assistant'.
        content: The message content to store.
    """
    app = ctx.request_context.lifespan_context

    if app.backend == "gateway" and app.gw_client:
        try:
            result = await app.gw_client.capture(
                user_content=content if role == "user" else "",
                assistant_content=content if role == "assistant" else "",
                session_key=session_key,
                messages=[{"role": role, "content": content}],
            )
            return (
                f"Stored in session '{session_key}' via Gateway: "
                f"L0 recorded={result.l0_recorded}, "
                f"scheduler={result.scheduler_notified}"
            )
        except GatewayHTTPError as exc:
            # Fallback: buffer messages
            if session_key not in app._pending_sessions:
                app._pending_sessions[session_key] = []
            app._pending_sessions[session_key].append(
                {"role": role, "content": content}
            )
            return (
                f"Gateway temporarily unavailable — buffered message. "
                f"Use 'flush_session' to submit when Gateway is back. "
                f"({exc.message})"
            )

    # Direct mode
    store = app.store
    assert store is not None
    l0_id = store.store_l0_message(session_key, role, content)
    store.create_session(session_key)

    result = f"Stored L0 message #{l0_id} in session '{session_key}'."

    llm = app.llm
    if llm:
        try:
            memories = await llm.extract_memories(
                f"[{role}] {content}", max_memories=3
            )
            if memories:
                count = 0
                for mem in memories:
                    store.store_l1_memory(
                        session_key=session_key,
                        content=mem.get("content", ""),
                        memory_type=mem.get("type", "episodic"),
                        scene=mem.get("scene"),
                        source_l0=l0_id,
                    )
                    count += 1
                result += f" Extracted {count} L1 memory/memories."
        except Exception as exc:
            logger.warning("L1 extraction failed (non-fatal): %s", exc)
            result += " (L1 extraction skipped)"

    return result


@mcp.tool()
async def search_memories(
    ctx: Context,
    query: str,
    limit: int = 5,
    memory_type: str | None = None,
    scene: str | None = None,
) -> str:
    """Search through structured long-term memories (L1).

    In gateway mode, uses the full hybrid retrieval pipeline (BM25 +
    embedding + RRF fusion). In direct mode, uses SQLite LIKE matching.

    Args:
        query: The search text to match against stored memories.
        limit: Maximum number of results (default 5, max 20).
        memory_type: Filter by type — 'persona', 'episodic', or 'instruction'.
        scene: Filter by scene name.
    """
    limit = min(max(limit, 1), 20)

    if _backend(ctx) == "gateway":
        try:
            result = await _gw(ctx).search_memories(
                query=query, limit=limit,
                memory_type=memory_type, scene=scene,
            )
            if not result.results.strip():
                return "No matching memories found (gateway)."
            return f"**Strategy**: {result.strategy}  **Total**: {result.total}\n\n{result.results}"
        except GatewayHTTPError as exc:
            return f"Gateway search failed: {exc.message}"

    store = _store(ctx)
    results = store.search_l1_memories(
        query=query, limit=limit, memory_type=memory_type, scene=scene
    )
    if not results:
        return "No matching memories found."
    lines = [f"- [{r['memory_type']}] (score={r['score']}) {r['content']}" for r in results]
    return "\n".join(lines)


@mcp.tool()
async def search_conversations(
    ctx: Context,
    query: str,
    limit: int = 5,
    session_key: str | None = None,
) -> str:
    """Search through raw conversation history (L0).

    Args:
        query: The search text to match.
        limit: Maximum number of results (default 5, max 20).
        session_key: Optional session key to scope the search.
    """
    limit = min(max(limit, 1), 20)

    if _backend(ctx) == "gateway":
        client = _gw(ctx)
        try:
            result = await client.search_conversations(
                query=query, limit=limit, session_key=session_key
            )
            if not result.results.strip():
                return "No matching conversations found (gateway)."
            return f"**Total**: {result.total}\n\n{result.results}"
        except GatewayHTTPError as exc:
            return f"Gateway search failed: {exc.message}"

    store = _store(ctx)
    results = store.search_l0_conversations(
        query=query, limit=limit, session_key=session_key
    )
    if not results:
        return "No matching conversations found."
    lines = []
    for r in results:
        ts = datetime.fromtimestamp(r["timestamp"] / 1000, tz=timezone.utc).isoformat()
        lines.append(f"- [{ts}] **{r['role']}**: {r['content']}")
    return "\n".join(lines)


@mcp.tool()
async def get_persona(ctx: Context) -> str:
    """Retrieve the current user persona (L3).

    Returns the structured persona profile generated from accumulated
    memories, including communication style, preferences, and workflows.
    """
    if _backend(ctx) == "gateway":
        client = _gw(ctx)
        try:
            result = await client.recall(
                query="user persona",
                session_key="_persona_query_",
            )
            return result.context or "No persona context available."
        except GatewayHTTPError as exc:
            return f"Gateway persona retrieval failed: {exc.message}"

    store = _store(ctx)
    persona = store.get_active_persona()
    if persona:
        return f"**Persona v{persona['version']}**\n\n{persona['persona_text']}"
    md = store.get_persona_md()
    return md or "No persona has been built yet. Start storing conversations to generate one."


@mcp.tool()
async def get_scenarios(ctx: Context) -> str:
    """List all scenario blocks (L2)."""
    store = _store(ctx)
    scenarios = store.list_l2_scenarios()
    if not scenarios:
        return "No scenarios found."
    lines = []
    for s in scenarios:
        lines.append(f"### {s['name']}")
        if s.get("description"):
            lines.append(f"_{s['description']}_")
        lines.append(s["content"])
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
async def create_memory_session(
    ctx: Context,
    session_key: str,
    label: str | None = None,
    user_id: str = "default_user",
) -> str:
    """Create a new memory session.

    Args:
        session_key: Unique session identifier.
        label: Optional human-readable label for the session.
        user_id: User identifier (default: 'default_user').
    """
    store = _store(ctx)
    store.create_session(session_key=session_key, label=label, user_id=user_id)
    label_str = f' "{label}"' if label else ""
    return f"Session{label_str} created: {session_key}"


@mcp.tool()
async def get_session_memories(
    ctx: Context,
    session_key: str,
    limit: int = 50,
) -> str:
    """Get all L1 memories for a specific session.

    Args:
        session_key: The session to query.
        limit: Maximum number of memory entries (default 50).
    """
    store = _store(ctx)
    memories = store.get_l1_memories_by_session(session_key=session_key, limit=limit)
    if not memories:
        return f"No memories found for session '{session_key}'."
    lines = [f"- [{m['memory_type']}] {m['content']}" for m in memories]
    return "\n".join(lines)


@mcp.tool()
async def store_conversation_batch(
    session_key: str,
    messages: list[dict[str, str]],
    ctx: Context,
) -> str:
    """Store multiple conversation turns (L0 batch).

    Args:
        session_key: Session identifier.
        messages: List of {"role": str, "content": str} dicts.
    """
    store = _store(ctx)
    store.create_session(session_key)
    pairs = [(m["role"], m["content"]) for m in messages]
    count = store.store_l0_messages(session_key, pairs)
    return f"Stored {count} L0 messages in session '{session_key}'."


@mcp.tool()
async def generate_persona(ctx: Context) -> str:
    """Trigger persona generation from accumulated L1 memories (L3).

    Requires LLM_API_KEY to be configured.
    """
    app = ctx.request_context.lifespan_context

    if app.backend == "gateway":
        return (
            "Persona generation is handled automatically by the Gateway pipeline. "
            "Use 'get_persona' to retrieve the current persona."
        )

    llm = app.llm
    if not llm:
        return "LLM extraction is not configured. Set LLM_API_KEY to enable."

    store = app.store
    assert store is not None
    memories = store.conn.execute(
        "SELECT content FROM l1_memories ORDER BY created_at DESC LIMIT 100"
    ).fetchall()

    if not memories:
        return "No memories available to generate persona."

    texts = [m["content"] for m in memories]
    try:
        persona_text = await llm.generate_persona(texts)
    except Exception as exc:
        logger.error("Persona generation failed: %s", exc)
        return f"Persona generation failed: {exc}"

    store.store_l3_persona(persona_text)
    store.save_persona_md(persona_text)
    return f"**Persona generated (v{store.get_active_persona()['version']})**\n\n{persona_text}"


@mcp.tool()
async def generate_scenario(
    ctx: Context,
    scenario_name: str,
    session_key: str | None = None,
) -> str:
    """Generate a scenario block (L2) from memories.

    Requires LLM_API_KEY to be configured.

    Args:
        scenario_name: Name for the scenario (e.g. 'code-review', 'data-analysis').
        session_key: Optional session key to scope the memories used.
    """
    app = ctx.request_context.lifespan_context

    if app.backend == "gateway":
        return (
            "Scenario generation is handled automatically by the Gateway pipeline. "
            "Use 'get_scenarios' to list existing scenarios."
        )

    llm = app.llm
    if not llm:
        return "LLM extraction is not configured. Set LLM_API_KEY to enable."

    store = app.store
    assert store is not None

    if session_key:
        memories = store.get_l1_memories_by_session(session_key, limit=50)
    else:
        rows = store.conn.execute(
            "SELECT content FROM l1_memories ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        memories = [dict(r) for r in rows]

    texts = [m["content"] for m in memories if m.get("content")]
    if not texts:
        return "No memories available for scenario generation."

    try:
        scenario_content = await llm.generate_scenario(texts, scenario_name)
    except Exception as exc:
        logger.error("Scenario generation failed: %s", exc)
        return f"Scenario generation failed: {exc}"

    store.store_l2_scenario(name=scenario_name, content=scenario_content)
    return f"### {scenario_name}\n\n{scenario_content}"


@mcp.tool()
async def get_task_canvas(
    session_key: str,
    ctx: Context,
) -> str:
    """Get the Mermaid task canvas for a session (short-term memory compression).

    Args:
        session_key: Session identifier.
    """
    store = _store(ctx)
    canvas = store.get_task_canvas_md(session_key)
    if canvas:
        return canvas
    return f"No task canvas found for session '{session_key}'."


@mcp.tool()
async def offload_context(
    session_key: str,
    conversations: list[str],
    ctx: Context,
) -> str:
    """Offload verbose context into a Mermaid symbol canvas.

    Requires LLM_API_KEY to be configured.

    Args:
        session_key: Session identifier.
        conversations: List of conversation turn texts to compress.
    """
    app = ctx.request_context.lifespan_context

    if app.backend == "gateway":
        return (
            "Context offload is handled automatically by the Gateway pipeline. "
            "Use 'get_task_canvas' to retrieve the canvas."
        )

    llm = app.llm
    if not llm:
        return "LLM extraction is not configured. Set LLM_API_KEY to enable."

    store = app.store
    assert store is not None

    try:
        canvas = await llm.generate_task_canvas(conversations)
    except Exception as exc:
        logger.error("Context offload failed: %s", exc)
        return f"Context offload failed: {exc}"

    store.save_task_canvas(session_key, canvas)
    return f"**Mermaid Task Canvas**\n\n```mermaid\n{canvas}\n```"


@mcp.tool()
async def flush_session(
    session_key: str,
    ctx: Context,
) -> str:
    """Flush buffered messages to the Gateway.

    In gateway mode, if the Gateway was temporarily unavailable,
    this sends all buffered messages for a session.

    Args:
        session_key: The session to flush.
    """
    app = ctx.request_context.lifespan_context
    if app.backend != "gateway" or not app.gw_client:
        return "flush_session is only available in gateway mode."

    buffered = app._pending_sessions.pop(session_key, [])
    if not buffered:
        return f"No buffered messages for session '{session_key}'."

    total = len(buffered)
    for msg in buffered:
        try:
            await app.gw_client.capture(
                user_content=msg["content"] if msg["role"] == "user" else "",
                assistant_content=msg["content"] if msg["role"] == "assistant" else "",
                session_key=session_key,
                messages=[msg],
            )
        except GatewayHTTPError as exc:
            app._pending_sessions[session_key] = buffered
            return f"Gateway still unavailable: {exc.message}. Messages preserved."

    return f"Flushed {total} buffered messages for session '{session_key}'."


@mcp.tool()
async def get_stats(ctx: Context) -> str:
    """Get memory system statistics and health."""
    app = ctx.request_context.lifespan_context
    stats: dict[str, Any] = {
        "backend": app.backend,
        "mode": app.mode_label,
        "data_dir": str(app.config.data_dir),
        "llm_enabled": bool(app.config.llm_api_key),
    }
    if app.store:
        stats.update(app.store.get_stats())
    if app.gw_process:
        try:
            health = await app.gw_process.health_check()
            stats["gateway_healthy"] = health
        except Exception:
            stats["gateway_healthy"] = False
    return json.dumps(stats, ensure_ascii=False, indent=2)


# =========================================================================
# Entry point
# =========================================================================
def main() -> None:
    cfg = load_config()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [tdai-mcp] %(levelname)s %(message)s",
    )

    backend = os.environ.get("TDAI_BACKEND", "gateway")
    logger.info("TDAI MCP Server starting — backend=%s, transport=%s", backend, cfg.mcp_transport)

    if cfg.mcp_transport == "sse":
        logger.info("Listening via SSE on %s:%s", cfg.mcp_host, cfg.mcp_port)
        mcp.run(transport="sse", host=cfg.mcp_host, port=cfg.mcp_port)
    else:
        logger.info("Listening via STDIO")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
