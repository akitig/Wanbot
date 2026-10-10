"""Discordの処理を止めずに、サーバー別JSONを読み書きする。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from wanbot.config import AppConfig
from wanbot.logging_config import log_event
from wanbot.storage.json_store import GuildJsonStore, StorageError


class AsyncGuildJsonStore:
    """1つのイベントループ内で使用する非同期の保存窓口。"""

    def __init__(
        self,
        config: AppConfig,
        guild_id: int,
        document: str,
    ) -> None:
        self._store = GuildJsonStore(config, guild_id, document)
        self._pending: set[asyncio.Task] = set()
        self._closing = False

    async def read(self) -> dict[str, Any]:
        return await self._run("read", self._store.read)

    async def update(
        self,
        change: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        # changeは通常のdefで書く。
        # Discordへの通信などは行わず、データの変更だけを実施する。
        await self._run("update", self._store.update, change)

    async def _run(self, operation: str, function: Callable, *args):
        if self._closing:
            raise StorageError("終了処理中の保存先にはアクセスできません。")

        task = asyncio.create_task(
            asyncio.to_thread(function, *args),
            name=f"storage_{operation}",
        )
        self._pending.add(task)

        def completed(finished: asyncio.Task) -> None:
            self._pending.discard(finished)

            if finished.cancelled():
                return

            error = finished.exception()
            if error is not None:
                # データ本文や例外本文はログへ渡さない。
                log_event(
                    "storage",
                    f"{operation}_failed",
                    guild_id=self._store.guild_id,
                    level=logging.ERROR,
                    error=error,
                )

        task.add_done_callback(completed)

        # 呼び出し元が取り消されても、実行中の保存を途中で打ち切らない。
        return await asyncio.shield(task)

    async def close(self) -> None:
        """新しい操作を止め、開始済みの読み書きが終わるまで待つ。"""
        self._closing = True
        pending = tuple(self._pending)
        if pending:
            await asyncio.shield(
                asyncio.gather(*pending, return_exceptions=True)
            )

    async def __aenter__(self) -> AsyncGuildJsonStore:
        if self._closing:
            raise StorageError("終了済みの保存先は再利用できません。")
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()
