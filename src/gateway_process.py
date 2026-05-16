"""Gateway process manager — starts the original TS Gateway as a subprocess."""

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("tdai-mcp-server.gateway")

GATEWAY_READY_MARKER = "Gateway listening on"


class GatewayProcessError(Exception):
    pass


class GatewayProcess:
    """Manages lifecycle of the TDAI Gateway Node.js subprocess.

    Architecture::

        MCP Server (Python)           Gateway (Node.js)
        ┌──────────────────┐         ┌─────────────────────┐
        │  gateway_client   │──HTTP──▶  TdaiGateway        │
        │  (httpx)          │         │  ├── TdaiCore       │
        │                   │         │  ├── SQLite/vec     │
        │  gateway_process  │──stdio──▶  ├── LLM extraction │
        │  (subprocess)     │         │  └── L0-L3 pipeline │
        └──────────────────┘         └─────────────────────┘
    """

    def __init__(
        self,
        data_dir: str | Path | None = None,
        port: int = 8420,
        host: str = "127.0.0.1",
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        llm_model: str | None = None,
        package_path: str | None = None,
    ) -> None:
        self._data_dir = Path(data_dir).resolve() if data_dir else None
        self._port = port
        self._host = host
        self._llm_api_key = llm_api_key
        self._llm_base_url = llm_base_url
        self._llm_model = llm_model
        self._package_path = package_path
        self._process: asyncio.subprocess.Process | None = None
        self._ready_event = asyncio.Event()
        self._startup_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    async def start(self) -> None:
        executable, args = self._resolve_command()
        env = self._build_env()

        logger.info(
            "Starting Gateway: %s %s (port=%s, data_dir=%s)",
            executable, " ".join(args), self._port, self._data_dir or "(default)",
        )

        self._process = await asyncio.create_subprocess_exec(
            executable,
            *args,
            env={**os.environ, **env},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._startup_task = asyncio.create_task(
            self._wait_for_ready()
        )

        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=30.0)
            logger.info("Gateway ready at %s", self.base_url)
        except asyncio.TimeoutError:
            await self._dump_stderr()
            raise GatewayProcessError(
                "Gateway did not become ready within 30s. "
                "Check that Node.js >= 22.16 is installed and "
                "@tencentdb-agent-memory/memory-tencentdb is available."
            ) from None

    async def stop(self) -> None:
        if self._process is None:
            return

        logger.info("Stopping Gateway...")

        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()

        try:
            if sys.platform == "win32":
                self._process.send_signal(signal.CTRL_C_EVENT)
            else:
                self._process.send_signal(signal.SIGTERM)

            try:
                await asyncio.wait_for(self._process.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Gateway did not stop gracefully, killing...")
                self._process.kill()
                await self._process.wait()
        except ProcessLookupError:
            pass

        self._process = None
        logger.info("Gateway stopped")

    async def health_check(self) -> bool:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/health", timeout=5.0
                )
                return resp.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _resolve_command(self) -> tuple[str, list[str]]:
        if self._package_path:
            pkg_dir = Path(self._package_path).resolve()
            server_ts = pkg_dir / "src" / "gateway" / "server.ts"
            if server_ts.exists():
                tsx_bin = pkg_dir / "node_modules" / "tsx" / "dist" / "cli.mjs"
                if tsx_bin.exists():
                    return ("node", [str(tsx_bin), str(server_ts)])
                return ("npx", ["tsx", str(server_ts)])

        # The npm package does not have a 'gateway' CLI command.
        # Run directly with tsx from the globally installed package.
        # Resolve global npm root dynamically (cross-platform).
        global_npm_root = self._resolve_global_npm_root()
        if global_npm_root:
            pkg_dir = global_npm_root / "@tencentdb-agent-memory" / "memory-tencentdb"
            server_ts = pkg_dir / "src" / "gateway" / "server.ts"
            if server_ts.exists():
                tsx_bin = pkg_dir / "node_modules" / "tsx" / "dist" / "cli.mjs"
                if tsx_bin.exists():
                    return ("node", [str(tsx_bin), str(server_ts)])
                return ("npx", ["tsx", str(server_ts)])

        # Fallback: try to find via npx
        return ("npx", ["tsx", "@tencentdb-agent-memory/memory-tencentdb/src/gateway/server.ts"])

    @staticmethod
    def _resolve_global_npm_root() -> Path | None:
        """Resolve the global npm root directory (cross-platform)."""
        try:
            result = subprocess.run(
                ["npm", "root", "-g"],
                capture_output=True, text=True, timeout=10.0,
            )
            if result.returncode == 0:
                path = result.stdout.strip()
                if path:
                    return Path(path)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return None

    def _build_env(self) -> dict[str, str]:
        env: dict[str, str] = {
            "TDAI_GATEWAY_PORT": str(self._port),
            "TDAI_GATEWAY_HOST": self._host,
        }

        if self._data_dir:
            env["TDAI_DATA_DIR"] = str(self._data_dir)

        if self._llm_api_key:
            env["TDAI_LLM_API_KEY"] = self._llm_api_key
        if self._llm_base_url:
            env["TDAI_LLM_BASE_URL"] = self._llm_base_url
        if self._llm_model:
            env["TDAI_LLM_MODEL"] = self._llm_model

        return env

    async def _wait_for_ready(self) -> None:
        assert self._process is not None
        assert self._process.stdout is not None

        while True:
            line = await self._process.stdout.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip()
            logger.info("[gateway] %s", decoded)

            if GATEWAY_READY_MARKER in decoded:
                self._ready_event.set()

    async def _dump_stderr(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        try:
            stderr = await asyncio.wait_for(
                self._process.stderr.read(), timeout=5.0
            )
            if stderr:
                logger.error(
                    "Gateway stderr:\n%s",
                    stderr.decode("utf-8", errors="replace"),
                )
        except asyncio.TimeoutError:
            pass
