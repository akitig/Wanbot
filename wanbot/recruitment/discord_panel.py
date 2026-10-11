from __future__ import annotations

import asyncio

import discord

from wanbot.recruitment.panel import (
    BeginHandler,
    RecruitmentPanelView,
    panel_embed,
)
from wanbot.recruitment.repository import RecruitmentPanelRepository


class RecruitmentPanelTargetError(RuntimeError):
    """募集パネルの操作対象が設定と一致しない場合のエラー。"""


class DiscordRecruitmentPanel:
    def __init__(
        self,
        *,
        client: discord.Client,
        guild_id: int,
        channel_id: int,
        repository: RecruitmentPanelRepository,
        on_begin: BeginHandler,
    ) -> None:
        self._client = client
        self._guild_id = guild_id
        self._channel_id = channel_id
        self._repository = repository
        self._lock = asyncio.Lock()
        self._closed = False
        self._pending: discord.Message | None = None
        self._obsolete: discord.Message | None = None
        self._view = RecruitmentPanelView(
            guild_id=guild_id,
            channel_id=channel_id,
            on_begin=on_begin,
        )

    def _channel(self) -> discord.TextChannel:
        channel = self._client.get_channel(self._channel_id)
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.guild.id != self._guild_id
            or self._client.user is None
        ):
            raise RecruitmentPanelTargetError("Invalid recruitment channel")
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
            raise RecruitmentPanelTargetError("Invalid recruitment panel")

    async def _finish_pending(self) -> None:
        if self._pending is not None:
            self._check_message(self._pending)
            await self._repository.set_panel_message_id(self._pending.id)
            self._pending = None

        # 新しいIDの保存が済んでから、古いパネルを削除する。
        if self._obsolete is not None:
            self._check_message(self._obsolete)
            try:
                await self._obsolete.delete()
            except discord.NotFound:
                pass
            self._obsolete = None

    async def _fetch_saved(
        self,
        channel: discord.TextChannel,
    ) -> discord.Message | None:
        message_id = await self._repository.read()
        if message_id is None:
            return None

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            return None

        self._check_message(message)
        return message

    async def _create(
        self,
        channel: discord.TextChannel,
        previous: discord.Message | None,
    ) -> None:
        message = await channel.send(
            embed=panel_embed(),
            view=self._view,
            allowed_mentions=discord.AllowedMentions.none(),
            silent=True,
        )
        self._pending = message
        self._obsolete = previous
        await self._finish_pending()

    async def ensure(self) -> None:
        async with self._lock:
            if self._closed:
                raise RuntimeError("Recruitment panel is closed")

            channel = self._channel()
            await self._finish_pending()
            message = await self._fetch_saved(channel)

            if message is not None:
                try:
                    await message.edit(
                        content=None,
                        embed=panel_embed(),
                        view=self._view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.NotFound:
                    pass
                else:
                    return

            await self._create(channel, None)

    async def move_to_bottom(self) -> None:
        async with self._lock:
            if self._closed:
                raise RuntimeError("Recruitment panel is closed")

            channel = self._channel()

            # 前回の保存・削除が途中なら、先に完了させる。
            await self._finish_pending()

            previous = await self._fetch_saved(channel)
            await self._create(channel, previous)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            self._view.stop()
