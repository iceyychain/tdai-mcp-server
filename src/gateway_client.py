"""HTTP client that forwards MCP tool calls to the TDAI Gateway API."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("tdai-mcp-server.gateway_client")


@dataclass
class GatewayHealth:
    status: str
    version: str
    uptime: int
    stores: dict[str, bool]


@dataclass
class RecallResult:
    context: str
    strategy: str | None = None
    memory_count: int = 0


@dataclass
class CaptureResult:
    l0_recorded: int
    scheduler_notified: bool


@dataclass
class MemorySearchResult:
    results: str
    total: int
    strategy: str


@dataclass
class ConversationSearchResult:
    results: str
    total: int


class GatewayHTTPError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"Gateway HTTP {status}: {message}")


class GatewayClient:
    """HTTP client for the TDAI Gateway API.

    Maps directly to the endpoints defined in
    ``src/gateway/server.ts`` and ``src/gateway/types.ts``.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8420") -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(30.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    async def health(self) -> GatewayHealth:
        resp = await self._client.get("/health")
        self._raise_on_error(resp)
        data = resp.json()
        return GatewayHealth(
            status=data["status"],
            version=data["version"],
            uptime=data["uptime"],
            stores=data["stores"],
        )

    # ------------------------------------------------------------------
    # Recall — memory retrieval before LLM turn
    # POST /recall { query, session_key, user_id? }
    # ------------------------------------------------------------------
    async def recall(
        self, query: str, session_key: str, user_id: str = "default_user"
    ) -> RecallResult:
        resp = await self._client.post(
            "/recall",
            json={
                "query": query,
                "session_key": session_key,
                "user_id": user_id,
            },
        )
        self._raise_on_error(resp)
        data = resp.json()
        return RecallResult(
            context=data.get("context", ""),
            strategy=data.get("strategy"),
            memory_count=data.get("memory_count", 0),
        )

    # ------------------------------------------------------------------
    # Capture — store conversation turn
    # POST /capture { user_content, assistant_content, session_key, session_id?, messages? }
    # ------------------------------------------------------------------
    async def capture(
        self,
        user_content: str,
        assistant_content: str,
        session_key: str,
        session_id: str | None = None,
        messages: list[dict[str, str]] | None = None,
    ) -> CaptureResult:
        body: dict[str, Any] = {
            "user_content": user_content,
            "assistant_content": assistant_content,
            "session_key": session_key,
        }
        if session_id:
            body["session_id"] = session_id
        if messages:
            body["messages"] = messages

        resp = await self._client.post("/capture", json=body)
        self._raise_on_error(resp)
        data = resp.json()
        return CaptureResult(
            l0_recorded=data.get("l0_recorded", 0),
            scheduler_notified=data.get("scheduler_notified", False),
        )

    # ------------------------------------------------------------------
    # Search Memories (L1)
    # POST /search/memories { query, limit?, type?, scene? }
    # ------------------------------------------------------------------
    async def search_memories(
        self,
        query: str,
        limit: int = 5,
        memory_type: str | None = None,
        scene: str | None = None,
    ) -> MemorySearchResult:
        body: dict[str, Any] = {"query": query, "limit": limit}
        if memory_type:
            body["type"] = memory_type
        if scene:
            body["scene"] = scene

        resp = await self._client.post("/search/memories", json=body)
        self._raise_on_error(resp)
        data = resp.json()
        return MemorySearchResult(
            results=data.get("results", ""),
            total=data.get("total", 0),
            strategy=data.get("strategy", ""),
        )

    # ------------------------------------------------------------------
    # Search Conversations (L0)
    # POST /search/conversations { query, limit?, session_key? }
    # ------------------------------------------------------------------
    async def search_conversations(
        self,
        query: str,
        limit: int = 5,
        session_key: str | None = None,
    ) -> ConversationSearchResult:
        body: dict[str, Any] = {"query": query, "limit": limit}
        if session_key:
            body["session_key"] = session_key

        resp = await self._client.post("/search/conversations", json=body)
        self._raise_on_error(resp)
        data = resp.json()
        return ConversationSearchResult(
            results=data.get("results", ""),
            total=data.get("total", 0),
        )

    # ------------------------------------------------------------------
    # Session End
    # POST /session/end { session_key, user_id? }
    # ------------------------------------------------------------------
    async def session_end(
        self, session_key: str, user_id: str = "default_user"
    ) -> bool:
        resp = await self._client.post(
            "/session/end",
            json={"session_key": session_key, "user_id": user_id},
        )
        self._raise_on_error(resp)
        data = resp.json()
        return data.get("flushed", False)

    # ------------------------------------------------------------------
    # Seed — batch import historical data
    # POST /seed
    # ------------------------------------------------------------------
    async def seed(
        self,
        data: Any,
        session_key: str | None = None,
        strict_round_role: bool = False,
        auto_fill_timestamps: bool = True,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "data": data,
            "strict_round_role": strict_round_role,
            "auto_fill_timestamps": auto_fill_timestamps,
        }
        if session_key:
            body["session_key"] = session_key

        resp = await self._client.post("/seed", json=body)
        self._raise_on_error(resp)
        return resp.json()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _raise_on_error(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", resp.text)
            except Exception:
                detail = resp.text
            raise GatewayHTTPError(resp.status_code, detail)
