"""Memory store layer — reads/writes the TencentDB-Agent-Memory SQLite database."""

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MemoryStore:
    """Direct SQLite wrapper for the TencentDB-Agent-Memory memory database.

    This mirrors the storage layer of the original TypeScript TDAI Core,
    enabling interoperability without needing to run the Node.js plugin.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._db: Path = data_dir / "store" / "memory.db"
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def ensure_dirs(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        (self._data_dir / "store").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "l0").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "l1").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "l2_scenes").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "l3_persona").mkdir(parents=True, exist_ok=True)

    def connect(self) -> None:
        self.ensure_dirs()
        self._conn = sqlite3.connect(str(self._db))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        assert self._conn is not None
        return self._conn

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def initialize_schema(self) -> None:
        c = self.conn
        c.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                session_key TEXT NOT NULL,
                user_id     TEXT NOT NULL DEFAULT 'default_user',
                label       TEXT,
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS l0_conversations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                timestamp   INTEGER NOT NULL,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS l1_memories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT NOT NULL,
                content     TEXT NOT NULL,
                memory_type TEXT DEFAULT 'episodic',
                scene       TEXT,
                score       REAL DEFAULT 0.0,
                embedding   BLOB,
                created_at  INTEGER NOT NULL,
                source_l0   INTEGER,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS l2_scenarios (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT,
                content     TEXT NOT NULL,
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS l3_persona (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                version     INTEGER NOT NULL DEFAULT 1,
                persona_text TEXT NOT NULL,
                created_at  INTEGER NOT NULL,
                is_active   INTEGER NOT NULL DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_l0_session ON l0_conversations(session_key);
            CREATE INDEX IF NOT EXISTS idx_l1_session ON l1_memories(session_key);
            CREATE INDEX IF NOT EXISTS idx_l1_type ON l1_memories(memory_type);
        """)

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------
    def create_session(
        self, session_key: str, label: str | None = None, user_id: str = "default_user"
    ) -> str:
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.conn.execute(
            """INSERT OR IGNORE INTO sessions (id, session_key, user_id, label, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_key, session_key, user_id, label, now, now),
        )
        self.conn.commit()
        return session_key

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # L0 — Raw conversation storage
    # ------------------------------------------------------------------
    def store_l0_message(
        self, session_key: str, role: str, content: str
    ) -> int:
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        c = self.conn.execute(
            """INSERT INTO l0_conversations (session_key, role, content, timestamp)
               VALUES (?, ?, ?, ?)""",
            (session_key, role, content, now),
        )
        self.conn.commit()
        return c.lastrowid  # type: ignore[return-value]

    def store_l0_messages(
        self, session_key: str, messages: Sequence[tuple[str, str]]
    ) -> int:
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        data = [(session_key, role, content, now) for role, content in messages]
        self.conn.executemany(
            """INSERT INTO l0_conversations (session_key, role, content, timestamp)
               VALUES (?, ?, ?, ?)""",
            data,
        )
        self.conn.commit()
        return len(data)

    def search_l0_conversations(
        self, query: str, limit: int = 5, session_key: str | None = None
    ) -> list[dict[str, Any]]:
        like = f"%{query}%"
        if session_key:
            rows = self.conn.execute(
                """SELECT * FROM l0_conversations
                   WHERE session_key = ? AND content LIKE ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (session_key, like, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT * FROM l0_conversations
                   WHERE content LIKE ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (like, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # L1 — Structured memories
    # ------------------------------------------------------------------
    def store_l1_memory(
        self,
        session_key: str,
        content: str,
        memory_type: str = "episodic",
        scene: str | None = None,
        source_l0: int | None = None,
    ) -> int:
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        c = self.conn.execute(
            """INSERT INTO l1_memories
               (session_key, content, memory_type, scene, created_at, source_l0)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_key, content, memory_type, scene, now, source_l0),
        )
        self.conn.commit()
        return c.lastrowid  # type: ignore[return-value]

    def search_l1_memories(
        self,
        query: str,
        limit: int = 5,
        memory_type: str | None = None,
        scene: str | None = None,
    ) -> list[dict[str, Any]]:
        like = f"%{query}%"
        parts = ["SELECT * FROM l1_memories WHERE content LIKE ?"]
        params: list[Any] = [like]

        if memory_type:
            parts.append("AND memory_type = ?")
            params.append(memory_type)
        if scene:
            parts.append("AND scene = ?")
            params.append(scene)

        parts.append("ORDER BY score DESC, created_at DESC LIMIT ?")
        params.append(limit)

        rows = self.conn.execute(" ".join(parts), params).fetchall()
        return [dict(r) for r in rows]

    def get_l1_memories_by_session(
        self, session_key: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM l1_memories
               WHERE session_key = ?
               ORDER BY created_at DESC LIMIT ?""",
            (session_key, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # L2 — Scenarios
    # ------------------------------------------------------------------
    def store_l2_scenario(
        self, name: str, content: str, description: str | None = None
    ) -> int:
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        c = self.conn.execute(
            """INSERT OR REPLACE INTO l2_scenarios
               (name, description, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (name, description, content, now, now),
        )
        self.conn.commit()
        return c.lastrowid  # type: ignore[return-value]

    def list_l2_scenarios(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM l2_scenarios ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # L3 — Persona
    # ------------------------------------------------------------------
    def store_l3_persona(self, persona_text: str) -> int:
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.conn.execute(
            "UPDATE l3_persona SET is_active = 0 WHERE is_active = 1"
        )
        c = self.conn.execute(
            """INSERT INTO l3_persona (version, persona_text, created_at, is_active)
               VALUES ((SELECT COALESCE(MAX(version), 0) + 1 FROM l3_persona), ?, ?, 1)""",
            (persona_text, now),
        )
        self.conn.commit()
        return c.lastrowid  # type: ignore[return-value]

    def get_active_persona(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM l3_persona WHERE is_active = 1 ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def get_persona_history(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM l3_persona ORDER BY version DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # File-based memory access (mirrors the original file structure)
    # ------------------------------------------------------------------
    def _read_text_file(self, *parts: str) -> str | None:
        path = self._data_dir.joinpath(*parts)
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
        return None

    def _write_text_file(self, content: str, *parts: str) -> Path:
        path = self._data_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def get_persona_md(self) -> str | None:
        return self._read_text_file("l3_persona", "persona.md")

    def save_persona_md(self, content: str) -> Path:
        return self._write_text_file(content, "l3_persona", "persona.md")

    def get_scenario_md(self, name: str) -> str | None:
        return self._read_text_file("l2_scenes", f"{name}.md")

    def get_task_canvas_md(self, session_key: str) -> str | None:
        return self._read_text_file("offload", session_key, "canvas.md")

    def get_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        l0 = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM l0_conversations"
        ).fetchone()
        stats["l0_conversations"] = l0["cnt"] if l0 else 0

        l1 = self.conn.execute("SELECT COUNT(*) as cnt FROM l1_memories").fetchone()
        stats["l1_memories"] = l1["cnt"] if l1 else 0

        l2 = self.conn.execute("SELECT COUNT(*) as cnt FROM l2_scenarios").fetchone()
        stats["l2_scenarios"] = l2["cnt"] if l2 else 0

        l3 = self.conn.execute("SELECT COUNT(*) as cnt FROM l3_persona").fetchone()
        stats["l3_versions"] = l3["cnt"] if l3 else 0

        sessions = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM sessions"
        ).fetchone()
        stats["sessions"] = sessions["cnt"] if sessions else 0

        return stats

    # ------------------------------------------------------------------
    # Context offload support (Mermaid canvas)
    # ------------------------------------------------------------------
    def save_task_canvas(self, session_key: str, mermaid_canvas: str) -> Path:
        return self._write_text_file(
            mermaid_canvas, "offload", session_key, "canvas.md"
        )

    def save_offload_log(self, session_key: str, content: str) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return self._write_text_file(
            content, "offload", session_key, f"ref_{ts}.md"
        )
