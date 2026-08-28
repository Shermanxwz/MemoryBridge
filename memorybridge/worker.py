from __future__ import annotations

import asyncio
import logging
import time

from .config import Settings
from .service import MemoryService
from .snapshot import SnapshotManager

log = logging.getLogger("memorybridge.worker")


async def run() -> None:
    settings = Settings()
    service = MemoryService(settings)
    snapshots = SnapshotManager(settings)
    last_snapshot = 0.0
    try:
        await service.ensure()
        while True:
            try:
                await service.index_pending(limit=50)
            except Exception as exc:
                log.warning("index worker degraded: %s", exc)
            now = time.time()
            if settings.snapshot_interval > 0 and now - last_snapshot >= settings.snapshot_interval:
                for collection in settings.effective_snapshot_collections:
                    try:
                        await snapshots.create_archive(collection)
                    except Exception as exc:
                        log.warning("snapshot failed for %s: %s", collection, exc)
                last_snapshot = now
            await asyncio.sleep(max(1, settings.worker_interval))
    finally:
        await service.close()
        await snapshots.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
