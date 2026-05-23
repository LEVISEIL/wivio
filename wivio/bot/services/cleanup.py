from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import time

logger = logging.getLogger(__name__)


class CleanupScheduler:
    def __init__(self, downloads_dir: Path, ttl_seconds: int, interval_seconds: int) -> None:
        self.downloads_dir = downloads_dir
        self.ttl_seconds = ttl_seconds
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self._run(), name="cleanup-scheduler")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while not self._stopped.is_set():
            await self.cleanup_once()
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def cleanup_once(self) -> None:
        await asyncio.to_thread(self._cleanup_sync)

    def _cleanup_sync(self) -> None:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        threshold = time.time() - self.ttl_seconds
        removed = 0

        for path in self.downloads_dir.iterdir():
            try:
                if path.name == ".gitkeep":
                    continue
                if path.stat().st_mtime >= threshold:
                    continue
                if path.is_dir():
                    for child in sorted(path.rglob("*"), reverse=True):
                        if child.is_file():
                            child.unlink(missing_ok=True)
                        elif child.is_dir():
                            child.rmdir()
                    path.rmdir()
                    removed += 1
                elif path.is_file():
                    path.unlink(missing_ok=True)
                    removed += 1
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.warning("Could not cleanup %s: %s", path, exc)

        if removed:
            logger.info("Cleanup removed %s expired download items", removed)
