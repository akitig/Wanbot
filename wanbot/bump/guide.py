from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

import discord

from wanbot.bump.panel import command_guide
from wanbot.bump.repository import BumpRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BumpGuideController:
    def __init__(
        self,
        *,
        guild_id: int,
        channel_id: int,
        repository: BumpRepository,
        is_available: Callable[[], bool],
        command_id: int | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._guild_id = guild_id
        self._channel_id = channel_id
        self._repository = repository
        self._is_available = is_available
        self._clock = clock
        self._guide = command_guide(command_id)

    def _correct_scope(self, interaction: discord.Interaction) -> bool:
        return (
            interaction.guild_id == self._guild_id
            and interaction.channel_id == self._channel_id
        )

    async def _reply(
        self,
        interaction: discord.Interaction,
        content: str,
    ) -> None:
        await interaction.followup.send(
            content,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def handle(self, interaction: discord.Interaction) -> None:
        # 保存データの読み込み前に応答を受け付ける。
        # 案内はボタンを押した本人だけに表示する。
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not self._correct_scope(interaction):
            await self._reply(
                interaction,
                "このサーバー・チャンネルでは利用できません。",
            )
            return

        if not self._is_available():
            await self._reply(
                interaction,
                "現在BUMP案内を利用できません。しばらくしてからお試しください。",
            )
            return

        state = await self._repository.read()

        # データの読み込み中に停止した場合も案内しない。
        if not self._is_available():
            await self._reply(
                interaction,
                "現在BUMP案内を利用できません。しばらくしてからお試しください。",
            )
            return

        now = self._clock()

        if not state.is_available(now):
            timestamp = int(state.next_bump_at.timestamp())
            await self._reply(
                interaction,
                "現在はBUMPの待機中です。\n"
                f"次回の目安：<t:{timestamp}:F>（<t:{timestamp}:R>）",
            )
            return

        await self._reply(interaction, self._guide)
