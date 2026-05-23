from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = await aiosqlite.connect(self.path)
            self._connection.row_factory = aiosqlite.Row
            await self._connection.execute("PRAGMA foreign_keys = ON")
            await self._connection.execute("PRAGMA journal_mode = WAL")
            await self._connection.commit()
        except Exception:
            logger.exception("Could not connect to SQLite database at %s", self.path)
            raise

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database is not connected")
        return self._connection

    async def migrate(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        try:
            schema = schema_path.read_text(encoding="utf-8")
            await self.connection.executescript(schema)
            await self.connection.commit()
        except Exception:
            logger.exception("Could not migrate SQLite database with schema %s", schema_path)
            raise

    async def close(self) -> None:
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:
                logger.exception("Could not close SQLite database at %s", self.path)
                raise
            finally:
                self._connection = None
