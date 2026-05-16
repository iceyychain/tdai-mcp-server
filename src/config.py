"""Configuration management for TDAI MCP Server."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TdaiConfig:
    data_dir: Path = field(
        default_factory=lambda: Path.home() / ".openclaw" / "memory-tdai"
    )
    store_backend: str = "sqlite"
    recall_strategy: str = "hybrid"
    recall_max_results: int = 5

    # LLM extraction config (optional)
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.lkeap.cloud.tencent.com/v1"
    llm_model: str = "deepseek-v3.2"

    # MCP transport config
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8421


def load_config() -> TdaiConfig:
    data_dir_raw = os.environ.get("TDAI_DATA_DIR")
    data_dir: Path = (
        Path(data_dir_raw).resolve()
        if data_dir_raw
        else Path.home() / ".openclaw" / "memory-tdai"
    )

    return TdaiConfig(
        data_dir=data_dir,
        store_backend=os.environ.get("TDAI_STORE_BACKEND", "sqlite"),
        recall_strategy=os.environ.get("TDAI_RECALL_STRATEGY", "hybrid"),
        recall_max_results=int(os.environ.get("TDAI_RECALL_MAX_RESULTS", "5")),
        llm_api_key=os.environ.get("LLM_API_KEY"),
        llm_base_url=os.environ.get(
            "LLM_BASE_URL", "https://api.lkeap.cloud.tencent.com/v1"
        ),
        llm_model=os.environ.get("LLM_MODEL", "deepseek-v3.2"),
        mcp_transport=os.environ.get("MCP_TRANSPORT", "stdio"),
        mcp_host=os.environ.get("MCP_HOST", "127.0.0.1"),
        mcp_port=int(os.environ.get("MCP_PORT", "8421")),
    )
