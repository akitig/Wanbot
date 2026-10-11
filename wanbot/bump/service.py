"""BUMP成功の保存と、待機期限に応じたパネル更新を管理する。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from wanbot.bump.detection import is_bump_success_message
from wanbot.bump.repository import BumpRepository, BumpState
from wanbot.logging_config import log_event


# 第3引数は、成功直後の末尾への再投稿を依頼する場合にTrue。
PanelRenderer = Callable[[BumpState, datetime, bool], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BumpService:
    def __init__(
        self,
        *,
        guild_id: int,
        channel_id: int,
        disboard_bot_id: int,
        repository: BumpRepository,
        render_panel: PanelRenderer,
        is_available: Callable[[], bool],
        cooldown_seconds: int = 7200,
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if (
            type(cooldown_seconds) is not int
            or not 1 <= cooldown_seconds <= 2_147_483_647
        ):
            raise ValueError("待機時間は有効な正の整数の秒数で指定してください。")

        self._guild_id = guild_id
        self._channel_id = channel_id
        self._disboard_bot_id = disboard_bot_id
        self._repository = repository
        self._render_panel = render_panel
        self._is_available = is_available
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._sleep = sleep

        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._running = False
        self._closed = False
        self._last_display: tuple[BumpState, bool] | None = None
        self._delay = 30.0

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("終了済みのBUMPサービスは再利用できません。")
        if self._running:
            return

        self._running = True
        try:
            await self.refresh(force=True)
        except BaseException:
            self._running = False
            raise

        self._task = asyncio.create_task(
            self._run(),
            name=f"bump-refresh-{self._guild_id}",
        )

    def _accepting(self) -> bool:
        return self._running and not self._closed and self._is_available()

    async def handle_message(self, message: Any) -> bool:
        if not self._accepting():
            return False

        if not is_bump_success_message(
            message,
            guild_id=self._guild_id,
            channel_id=self._channel_id,
            disboard_bot_id=self._disboard_bot_id,
        ):
            return False

        async with self._lock:
            if not self._accepting():
                return False

            accepted = await self._repository.record_success(
                message_id=message.id,
                occurred_at=message.created_at,
                cooldown_seconds=self._cooldown_seconds,
            )
            if not accepted:
                return False

            log_event(
                "bump",
                "success_recorded",
                guild_id=self._guild_id,
            )

            # 保存に成功してから表示を更新する。
            await self._refresh_locked(repost=True)
            return True

    async def refresh(self, *, force: bool = False) -> None:
        async with self._lock:
            if not self._running or self._closed:
                return
            await self._refresh_locked(force=force)

    async def _refresh_locked(
        self,
        *,
        force: bool = False,
        repost: bool = False,
    ) -> None:
        state = await self._repository.read()
        now = self._clock()
        available = state.is_available(now)

        self._delay = 30.0
        if not available:
            remaining = (state.next_bump_at - now).total_seconds()
            self._delay = max(0.1, min(30.0, remaining))

        display = (state, available)
        if force or repost or display != self._last_display:
            await self._render_panel(state, now, repost)

            # 表示に成功した場合だけ更新済みとして記録する。
            self._last_display = display

    async def _run(self) -> None:
        while not self._closed:
            await self._sleep(self._delay)
            if not self._accepting():
                continue

            try:
                await self.refresh()
            except Exception as error:
                # 失敗してもタスクを終了せず、30秒後に再試行する。
                self._delay = 30.0
                log_event(
                    "bump",
                    "panel_refresh_failed",
                    guild_id=self._guild_id,
                    level=logging.ERROR,
                    error=error,
                )

    async def close(self) -> None:
        if self._closed:
            return

        self._closed = True
        self._running = False

        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        async with self._lock:
            await self._repository.close()
