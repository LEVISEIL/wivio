import os
import time
from pathlib import Path

import pytest

from bot.services.cleanup import CleanupScheduler


@pytest.mark.asyncio
async def test_cleanup_removes_expired_items_and_keeps_fresh_items(tmp_path: Path) -> None:
    old_dir = tmp_path / "old-job"
    old_dir.mkdir()
    old_file = old_dir / "video.mp4"
    old_file.write_bytes(b"old")

    old_root_file = tmp_path / "old.tmp"
    old_root_file.write_bytes(b"old")

    fresh_file = tmp_path / "fresh.tmp"
    fresh_file.write_bytes(b"fresh")

    gitkeep = tmp_path / ".gitkeep"
    gitkeep.write_text("")

    old_timestamp = time.time() - 120
    os.utime(old_file, (old_timestamp, old_timestamp))
    os.utime(old_dir, (old_timestamp, old_timestamp))
    os.utime(old_root_file, (old_timestamp, old_timestamp))

    scheduler = CleanupScheduler(tmp_path, ttl_seconds=60, interval_seconds=60)

    await scheduler.cleanup_once()

    assert not old_dir.exists()
    assert not old_root_file.exists()
    assert fresh_file.exists()
    assert gitkeep.exists()
