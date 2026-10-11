from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import discord

from wanbot.bump.panel import BumpPanelView, GuideHandler, panel_embed
from wanbot.bump.repository import BumpRepository, BumpState
from wanbot.logging_config import log_event


class BumpPanelTargetError(RuntimeError):
    """パネルの操作対象が設定と一致しない場合のエラー。"""


class DiscordBumpPanel:
    def __init__(
        self,
        *,
        client: discord.Client,
        guild_id: int,
        channel_id: int,
        repository: BumpRepository,
        on_guide: GuideHandler,
    ) -> None:
        self._client = client
        self._guild_id = guild_id
        self._channel_id = channel_id
        self._repository = repository
        self._on_guide = on_guide
        self._lock = asyncio.Lock()
        self._view: BumpPanelView | None = None
        self._pending: tuple[
            discord.Message,
            BumpPanelView,
            discord.Message | None,
        ] | None = None
        self._closed = False

    def _channel(self) -> discord.TextChannel:
        channel = self._client.get_channel(self._channel_id)

        if (
            not isinstance(channel, discord.TextChannel)
            or channel.guild.id != self._guild_id
        ):
            raise BumpPanelTargetError("Invalid BUMP channel")

        if self._client.user is None:
            raise BumpPanelTargetError("Bot user is unavailable")

        return channel

    def _check_message(self, message: discord.Message) -> None:
        user = self._client.user

        if (
            user is None
            or message.guild is None
            or message.guild.id != self._guild_id
            or message.channel.id != self._channel_id
            or message.author.id != user.id
            or message.webhook_id is not None
        ):
            raise BumpPanelTargetError("Invalid BUMP panel message")

    def _make_view(
        self,
        state: BumpState,
        now: datetime,
    ) -> BumpPanelView:
        return BumpPanelView(
            guild_id=self._guild_id,
            channel_id=self._channel_id,
            available=state.is_available(now),
            on_guide=self._on_guide,
        )

    def _activate_view(self, view: BumpPanelView) -> None:
        previous = self._view
        self._view = view

        if previous is not None and previous is not view:
            previous.stop()

    async def _finish_pending(self) -> None:
        pending = self._pending
        if pending is None:
            return

        message, view, previous = pending
        self._check_message(message)

        # 保存に失敗した場合はpendingを残す。
        # 次の更新時に、追加投稿せず同じIDの保存を再試行する。
        await self._repository.set_panel_message_id(message.id)

        self._pending = None
        self._activate_view(view)

        if previous is None:
            return

        self._check_message(previous)

        try:
            await previous.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as error:
            # 新しいパネルは既に保存済みなので維持する。
            log_event(
                "bump",
                "old_panel_delete_failed",
                guild_id=self._guild_id,
                level=logging.WARNING,
                error=error,
            )

    async def render(
        self,
        state: BumpState,
        now: datetime,
        repost: bool,
    ) -> None:
        async with self._lock:
            if self._closed:
                raise RuntimeError("BUMP panel is closed")

            channel = self._channel()

            if self._pending is not None:
                await self._finish_pending()
                state = await self._repository.read()
                repost = False

            previous = None

            if state.panel_message_id is not None:
                try:
                    previous = await channel.fetch_message(
                        state.panel_message_id
                    )
                except discord.NotFound:
                    previous = None

                if previous is not None:
                    self._check_message(previous)

            view = self._make_view(state, now)
            embed = panel_embed(state, now=now)

            if previous is not None and not repost:
                try:
                    await previous.edit(
                        content=None,
                        embed=embed,
                        view=view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.NotFound:
                    # 取得後に削除された場合も再作成する。
                    previous = None
                except BaseException:
                    view.stop()
                    raise
                else:
                    self._activate_view(view)
                    return

            try:
                message = await channel.send(
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except BaseException:
                view.stop()
                raise

            self._pending = (message, view, previous)
            await self._finish_pending()

    async def close(self) -> None:
        async with self._lock:
            self._closed = True

            if self._view is not None:
                self._view.stop()
                self._view = None

            if self._pending is not None:
                self._pending[1].stop()
                self._pending = None
