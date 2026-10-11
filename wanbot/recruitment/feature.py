from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from wanbot.config import AppConfig, ConfigError, GuildConfig
from wanbot.recruitment.controller import RecruitmentController
from wanbot.recruitment.discord_panel import DiscordRecruitmentPanel
from wanbot.recruitment.publisher import RecruitmentPublisher
from wanbot.recruitment.repository import RecruitmentPanelRepository
from wanbot.recruitment.sticky import (
    DebouncedPanelMove,
    is_own_panel_message,
)

if TYPE_CHECKING:
    from wanbot.bot import WanbotClient


class RecruitmentFeature:
    def __init__(
        self,
        client: WanbotClient,
        config: AppConfig,
        guild: GuildConfig,
    ) -> None:
        if not guild.feature_enabled("recruitment"):
            raise ConfigError("募集機能が有効になっていません。")

        self._client = client
        self._guild_id = guild.guild_id
        self._channel_id = guild.ids["VALO_RECRUIT_CHANNEL_ID"]
        self._started = False
        self._closed = False

        self._repository = RecruitmentPanelRepository(
            config,
            guild.guild_id,
        )
        self._publisher = RecruitmentPublisher(
            client=client,
            guild=guild,
            is_available=self._available,
        )
        self._controller = RecruitmentController(
            guild=guild,
            publisher=self._publisher,
            is_available=self._available,
        )
        self._panel = DiscordRecruitmentPanel(
            client=client,
            guild_id=guild.guild_id,
            channel_id=self._channel_id,
            repository=self._repository,
            on_begin=self._controller.begin,
        )
        self._mover = DebouncedPanelMove(
            guild_id=guild.guild_id,
            move=self._panel.move_to_bottom,
            is_available=self._available,
        )

    def _available(self) -> bool:
        return (
            self._started
            and not self._closed
            and self._client.feature_available(
                self._guild_id,
                "recruitment",
            )
        )

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("Recruitment feature is closed")
        if self._started:
            return

        try:
            await self._panel.ensure()
        except BaseException:
            await self.stop()
            raise

        self._started = True

    async def handle_message(self, message: discord.Message) -> None:
        if (
            not self._available()
            or message.guild is None
            or message.guild.id != self._guild_id
            or message.channel.id != self._channel_id
        ):
            return

        user = self._client.user
        if user is None:
            return

        if is_own_panel_message(
            message,
            bot_user_id=user.id,
            guild_id=self._guild_id,
            channel_id=self._channel_id,
        ):
            return

        self._mover.notify()

    async def stop(self) -> None:
        if self._closed:
            return

        self._closed = True
        self._started = False

        try:
            await self._mover.close()
        finally:
            try:
                self._controller.close()
            finally:
                try:
                    await self._panel.close()
                finally:
                    await self._repository.close()
