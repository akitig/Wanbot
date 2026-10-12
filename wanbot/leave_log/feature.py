"""退出記録の保存と、定期的な照合・通知を管理する。"""

import asyncio
import logging

from wanbot.config import ConfigError
from wanbot.leave_log.delivery import DepartureDelivery
from wanbot.leave_log.processor import DepartureProcessor
from wanbot.leave_log.repository import DepartureRepository
from wanbot.leave_log.result_repository import DepartureResultRepository
from wanbot.logging_config import log_event


POLL_SECONDS = 10.0


class LeaveLogFeature:
    def __init__(self, client, config, guild):
        if not guild.feature_enabled("leave_log"):
            raise ConfigError("退出記録が有効になっていません。")

        self._client = client
        self._guild_id = guild.guild_id
        self._started = False
        self._closed = False
        self._worker = None
        self._storage_lock = asyncio.Lock()

        self._repository = DepartureRepository(config, self._guild_id)
        self._results = DepartureResultRepository(config, self._guild_id)
        self._delivery = DepartureDelivery(
            client=client,
            config=config,
            guild_id=self._guild_id,
            repository=self._repository,
            results=self._results,
            is_available=self._available,
        )
        self._processor = DepartureProcessor(
            guild_id=self._guild_id,
            repository=self._repository,
            results=self._results,
            delivery=self._delivery,
            get_guild=lambda: client.get_guild(self._guild_id),
            is_available=self._available,
        )

    def _available(self):
        return (
            self._started
            and not self._closed
            and self._client.feature_available(self._guild_id, "leave_log")
        )

    async def start(self):
        if self._closed:
            raise RuntimeError("Leave log feature is closed")
        if self._started:
            return
        self._started = True
        self._worker = asyncio.create_task(
            self._run(), name=f"leave-log-worker-{self._guild_id}"
        )

    async def record(self, snapshot):
        if snapshot.guild_id != self._guild_id:
            raise ValueError("Departure guild does not match")
        async with self._storage_lock:
            if not self._available():
                return False
            await self._repository.add(snapshot)
            return True

    async def _run(self):
        while not self._closed:
            try:
                if self._available():
                    await self._processor.tick()
            except Exception as error:
                log_event(
                    "leave_log",
                    "worker_failed",
                    guild_id=self._guild_id,
                    level=logging.ERROR,
                    error=error,
                )
            await asyncio.sleep(POLL_SECONDS)

    async def stop(self):
        if self._closed:
            return
        self._closed = True
        self._started = False
        try:
            if self._worker is not None:
                self._worker.cancel()
                await asyncio.gather(self._worker, return_exceptions=True)
        finally:
            async with self._storage_lock:
                try:
                    await self._repository.close()
                finally:
                    await self._results.close()
