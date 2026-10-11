"""Wanbotの起動と、機能のライフサイクル管理。"""

from __future__ import annotations

import asyncio
import logging

import discord

from wanbot.bump.events import BumpEvents
from wanbot.bump.feature import BumpFeature
from wanbot.commands import WanbotCommandTree, register_status_command
from wanbot.config import AppConfig, ConfigError, GuildConfig
from wanbot.connection import ConnectionCheckClient
from wanbot.features.registry import FeatureManager, FeatureRegistry
from wanbot.logging_config import log_event
from wanbot.valorant.feature import ValorantDiagnosisFeature


def build_registry(
    client: WanbotClient,
    config: AppConfig,
) -> FeatureRegistry:
    registry = FeatureRegistry()

    def create_diagnosis(guild: GuildConfig) -> ValorantDiagnosisFeature:
        return ValorantDiagnosisFeature(client, config, guild)

    def create_bump(guild: GuildConfig) -> BumpFeature:
        return BumpFeature(client, config, guild)

    registry.register("bump", create_bump)
    registry.register("valorant_diagnosis", create_diagnosis)
    return registry


def validate_implementations(
    config: AppConfig,
    registry: FeatureRegistry,
) -> None:
    for guild in config.guilds.values():
        for feature in sorted(guild.enabled_features):
            if registry.get(feature) is None:
                prefix = guild.environment.upper()
                raise ConfigError(
                    f"{prefix}_ENABLED_FEATURES: "
                    f"{feature} はまだ実装・登録されていません。"
                )

    if config.dm_forward_enabled:
        raise ConfigError("DM_FORWARD_ENABLED: DM転送はまだ未実装です。")


class WanbotClient(ConnectionCheckClient):
    def __init__(
        self,
        config: AppConfig,
        registry: FeatureRegistry | None = None,
    ) -> None:
        # テストでは差し替えた機能一覧を使える。
        # 通常起動では実装済み機能を登録する。
        selected_registry = (
            registry if registry is not None else build_registry(self, config)
        )
        validate_implementations(config, selected_registry)
        super().__init__(config)

        self.feature_manager = FeatureManager(config, selected_registry)
        self._bump_events = BumpEvents(self)
        self._feature_start_lock = asyncio.Lock()
        self._features_started = False

        self.tree = WanbotCommandTree(self, fallback_to_global=False)
        register_status_command(self.tree, self, config)

    async def on_message(self, message: discord.Message) -> None:
        await self._bump_events.on_message(message)

    async def on_raw_message_edit(
        self,
        payload: discord.RawMessageUpdateEvent,
    ) -> None:
        await self._bump_events.on_raw_message_edit(payload)

    async def _sync_guild_commands(self) -> None:
        for guild in self.config.guilds.values():
            # 空の一覧も同期し、無効化した機能の古いコマンドを削除する。
            # グローバルコマンドや未登録サーバーは同期しない。
            await self.tree.sync(guild=discord.Object(id=guild.guild_id))
            log_event(
                "commands",
                "guild_commands_synced",
                guild_id=guild.guild_id,
            )

    async def on_ready(self) -> None:
        await super().on_ready()

        if self.connection_check_failed or self.is_closed():
            return

        async with self._feature_start_lock:
            if self._features_started or self.is_closed():
                return

            await self.feature_manager.start()

            try:
                await self._sync_guild_commands()
            except Exception as error:
                self.connection_check_failed = True
                log_event(
                    "commands",
                    "sync_failed",
                    level=logging.ERROR,
                    error=error,
                )
                await self.close()
                return

            self._features_started = True
            log_event("lifecycle", "runtime_ready")

    def feature_available(
        self,
        guild_id: int | None,
        feature: str,
    ) -> bool:
        if self.is_closed() or self.connection_check_failed or not self.is_ready():
            return False
        if guild_id is None:
            return False

        guild = self.get_guild(guild_id)
        if guild is None or guild.unavailable:
            return False

        return self.feature_manager.is_active(guild_id, feature)

    async def close(self) -> None:
        try:
            await self.feature_manager.stop()
        finally:
            await super().close()


async def run_bot(config: AppConfig) -> int:
    async with WanbotClient(config) as client:
        await client.start(config.discord_token)
        return 1 if client.connection_check_failed else 0
