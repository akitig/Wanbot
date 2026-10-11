from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

from wanbot.logging_config import log_event


def is_own_panel_message(
    message,
    *,
    bot_user_id: int,
    guild_id: int,
    channel_id: int,
) -> bool:
    if (
        message.author.id != bot_user_id
        or message.webhook_id is not None
    ):
        return False

    expected = f"wanbot:recruitment:begin:{guild_id}:{channel_id}"

    return any(
        getattr(component, "custom_id", None) == expected
        for row in message.components
        for component in getattr(row, "children", ())
    )


class DebouncedPanelMove:
    def __init__(
        self,
        *,
        guild_id: int,
        move: Callable[[], Awaitable[None]],
        is_available: Callable[[], bool],
        delay: float = 3.0,
    ) -> None:
        self._guild_id = guild_id
        self._move = move
        self._is_available = is_available
        self._delay = delay
        self._deadline = 0.0
        self._revision = 0
        self._task: asyncio.Task | None = None
        self._moving = False
        self._closed = False

    def notify(self) -> None:
        if self._closed:
            return

        self._revision += 1
        self._deadline = asyncio.get_running_loop().time() + self._delay

        if self._task is None:
            self._task = asyncio.create_task(
                self._run(),
                name=f"recruitment-panel-{self._guild_id}",
            )

    async def _run(self) -> None:
        try:
            while not self._closed:
                remaining = (
                    self._deadline - asyncio.get_running_loop().time()
                )
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    continue

                if not self._is_available():
                    return

                revision = self._revision
                self._moving = True

                try:
                    await self._move()
                except Exception as error:
                    log_event(
                        "recruitment",
                        "panel_move_failed",
                        guild_id=self._guild_id,
                        level=logging.ERROR,
                        error=error,
                    )
                    # 次の投稿が来たときに改めて移動する。
                    return
                finally:
                    self._moving = False

                if revision == self._revision:
                    return

                # 移動処理中に新しい投稿が来ていた場合も取りこぼさない。
        finally:
            self._task = None

    async def close(self) -> None:
        self._closed = True
        task = self._task

        if task is None:
            return

        # 待機だけなら中止できる。
        # 投稿・保存・削除の途中なら、その処理の終了を待つ。
        if not self._moving:
            task.cancel()

        with suppress(asyncio.CancelledError):
            await task
