"""Wanbotの起動と、機能のライフサイクル管理。"""

from __future__ import annotations

import asyncio
import logging

import discord

from wanbot.commands import WanbotCommandTree, register_status_command
from wanbot.config import AppConfig, ConfigError
from wanbot.connection import ConnectionCheckClient
from wanbot.features.registry import FeatureManager, FeatureRegistry
from wanbot.logging_config import log_event


def build_registry() -> FeatureRegistry:
    """後続Issueで、実装した機能の生成処理をここへ登録する。"""
    return FeatureRegistry()


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
        registry: FeatureRegistry,
    ) -> None:
        validate_implementations(config, registry)
        super().__init__(config)

        self.feature_manager = FeatureManager(config, registry)
        self._feature_start_lock = asyncio.Lock()
        self._features_started = False

        self.tree = WanbotCommandTree(self, fallback_to_global=False)
        register_status_command(self.tree, self, config)

    async def _sync_test_commands(self) -> None:
        for guild in self.config.guilds.values():
            if guild.environment != "test":
                continue

            await self.tree.sync(guild=discord.Object(id=guild.guild_id))
            log_event(
                "commands",
                "test_commands_synced",
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
                await self._sync_test_commands()
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
    registry = build_registry()
    validate_implementations(config, registry)

    async with WanbotClient(config, registry) as client:
        await client.start(config.discord_token)
        return 1 if client.connection_check_failed else 0
