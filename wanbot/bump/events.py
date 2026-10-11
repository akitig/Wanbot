from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from wanbot.bump.feature import BumpFeature
from wanbot.logging_config import log_event

if TYPE_CHECKING:
    from wanbot.bot import WanbotClient


class BumpEvents:
    def __init__(self, client: WanbotClient) -> None:
        self._client = client

    def _feature(
        self,
        guild_id: int | None,
        channel_id: int,
    ) -> BumpFeature | None:
        guild = self._client.config.guild_for(guild_id)

        if (
            guild is None
            or not guild.feature_enabled("bump")
            or guild.ids.get("BUMP_CHANNEL_ID") != channel_id
            or not self._client.feature_available(guild_id, "bump")
        ):
            return None

        feature = self._client.feature_manager.get_active(
            guild_id,
            "bump",
        )
        return feature if isinstance(feature, BumpFeature) else None

    async def on_message(self, message: discord.Message) -> None:
        guild_id = message.guild.id if message.guild is not None else None
        feature = self._feature(guild_id, message.channel.id)

        if feature is None:
            return

        guild = self._client.config.guild_for(guild_id)
        if (
            message.author.id != guild.disboard_bot_id
            or message.author.bot is not True
        ):
            return

        await feature.handle_message(message)

    async def on_raw_message_edit(
        self,
        payload: discord.RawMessageUpdateEvent,
    ) -> None:
        # 本文・埋め込みに関係しない更新では取得しない。
        if not {"content", "embeds"}.intersection(payload.data):
            return

        feature = self._feature(payload.guild_id, payload.channel_id)
        if feature is None:
            return

        guild = self._client.config.guild_for(payload.guild_id)
        author = payload.data.get("author")
        if isinstance(author, dict):
            author_id = author.get("id")
            if (
                author_id is not None
                and str(author_id) != str(guild.disboard_bot_id)
            ):
                return

        channel = self._client.get_channel(payload.channel_id)
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.guild.id != payload.guild_id
        ):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return
        except discord.HTTPException as error:
            log_event(
                "bump",
                "edited_message_fetch_failed",
                guild_id=payload.guild_id,
                level=logging.WARNING,
                error=error,
            )
            return

        # 取得中の停止や、実際の投稿者も改めて確認する。
        await self.on_message(message)
