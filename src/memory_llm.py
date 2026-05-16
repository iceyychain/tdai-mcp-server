"""Optional LLM-based memory extraction layer.

Mimics the original TDAI Core's L1/L2/L3 extraction pipeline
using any OpenAI-compatible API.
"""

import json
from dataclasses import dataclass

import httpx


@dataclass
class LLMConfig:
    api_key: str
    base_url: str = "https://api.lkeap.cloud.tencent.com/v1"
    model: str = "deepseek-v3.2"
    timeout_s: int = 120


class MemoryLLM:
    """Thin LLM client for structured memory extraction."""

    def __init__(self, config: LLMConfig) -> None:
        self._cfg = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            timeout=httpx.Timeout(config.timeout_s),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # L1 — Extract structured memories from conversation turn
    # ------------------------------------------------------------------
    async def extract_memories(
        self, conversation: str, max_memories: int = 5
    ) -> list[dict[str, str]]:
        prompt = f"""Extract key memories from the following conversation turn.
Return a JSON array of objects, each with "content" (str), "type" (one of:
persona, episodic, instruction), and "scene" (str or null).

Max {max_memories} memories.

Conversation:
{conversation}"""

        resp = await self._llm_call(prompt)
        return self._parse_json_array(resp)

    # ------------------------------------------------------------------
    # L2 — Generate scenario summary from multiple memories
    # ------------------------------------------------------------------
    async def generate_scenario(
        self, memories: list[str], scenario_name: str
    ) -> str:
        prompt = f"""You are building a scenario summary for the memory system.
Scenario name: {scenario_name}

Summarize the following memories into a coherent Markdown document
that captures the user's interaction patterns and context in this scenario:

{json.dumps(memories, ensure_ascii=False, indent=2)}"""

        return await self._llm_call(prompt)

    # ------------------------------------------------------------------
    # L3 — Generate user persona from accumulated memories
    # ------------------------------------------------------------------
    async def generate_persona(self, memories: list[str]) -> str:
        prompt = f"""Based on the following memories, synthesize a user persona
as a structured Markdown document. Include sections for:
- Communication style & preferences
- Common tasks & workflows
- Known tools & expertise
- Recurring instructions or rules
- Goals & intentions

Memories:
{json.dumps(memories, ensure_ascii=False, indent=2)}"""

        return await self._llm_call(prompt)

    # ------------------------------------------------------------------
    # Context offload — generate Mermaid task canvas
    # ------------------------------------------------------------------
    async def generate_task_canvas(
        self, conversation_turns: list[str]
    ) -> str:
        prompt = f"""You are generating a Mermaid flowchart that represents
the task state transitions in the following conversation turns.
Use node_id references (e.g., node_1, node_2) for each step.

Output ONLY valid Mermaid syntax, no explanation.

Conversation turns:
{json.dumps(conversation_turns, ensure_ascii=False, indent=2)}"""

        return await self._llm_call(prompt)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    async def _llm_call(self, prompt: str) -> str:
        body = {
            "model": self._cfg.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a precise memory extraction assistant. "
                    "Output only the requested format.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
        }
        resp = await self._client.post("/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _parse_json_array(self, text: str) -> list[dict[str, str]]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            start = cleaned.index("\n") + 1
            end = cleaned.rindex("```")
            cleaned = cleaned[start:end].strip()
        try:
            result = json.loads(cleaned)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
        return []
