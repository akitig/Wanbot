from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

from wanbot.bump.discord_panel import DiscordBumpPanel
from wanbot.bump.guide import BumpGuideController
from wanbot.bump.repository import BumpRepository
from wanbot.bump.service import BumpService
from wanbot.config import AppConfig, ConfigError, GuildConfig
from wanbot.logging_config import log_event

if TYPE_CHECKING:
    from wanbot.bot import WanbotClient


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BumpFeature:
    def __init__(
        self,
        client: WanbotClient,
        config: AppConfig,
        guild: GuildConfig,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not guild.feature_enabled("bump"):
            raise ConfigError("BUMP機能が有効になっていません。")
        if guild.disboard_bot_id is None:
            raise ConfigError("BUMP機能にはDISBOARD_BOT_IDが必要です。")

        self._client = client
        self._guild_id = guild.guild_id
        self._started = False
        self._closed = False

        channel_id = guild.ids["BUMP_CHANNEL_ID"]

        self._repository = BumpRepository(config, guild.guild_id)

        self._guide = BumpGuideController(
            guild_id=guild.guild_id,
            channel_id=channel_id,
            repository=self._repository,
            is_available=self._available,
            command_id=guild.disboard_bump_command_id,
            clock=clock,
        )

        self._panel = DiscordBumpPanel(
            client=client,
            guild_id=guild.guild_id,
            channel_id=channel_id,
            repository=self._repository,
            on_guide=self._guide.handle,
        )

        self._service = BumpService(
            guild_id=guild.guild_id,
            channel_id=channel_id,
            disboard_bot_id=guild.disboard_bot_id,
            repository=self._repository,
            render_panel=self._panel.render,
            is_available=self._available,
            cooldown_seconds=guild.bump_cooldown_seconds,
            clock=clock,
        )

    def _available(self) -> bool:
        return (
            self._started
            and not self._closed
            and self._client.feature_available(self._guild_id, "bump")
        )

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("BUMP feature is closed")
        if self._started:
            return

        try:
            await self._service.start()
        except BaseException:
            await self.stop()
            raise

        self._started = True

    async def handle_message(self, message: discord.Message) -> bool:
        if not self._available():
            return False

        try:
            return await self._service.handle_message(message)
        except Exception as error:
            # 本文・埋め込み・イベント引数はログへ出さない。
            log_event(
                "bump",
                "message_processing_failed",
                guild_id=self._guild_id,
                level=logging.ERROR,
                error=error,
            )
            return False

    async def stop(self) -> None:
        if self._closed:
            return

        self._closed = True
        self._started = False

        try:
            # BumpService.close()が保存処理も終了する。
            await self._service.close()
        finally:
            await self._panel.close()
