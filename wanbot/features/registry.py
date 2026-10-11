"""サーバーごとの機能登録・起動・停止を管理する。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from wanbot.config import AppConfig, FEATURE_NAMES, GuildConfig
from wanbot.logging_config import log_event


class Feature(Protocol):
    """各機能が実装する共通の入口。"""

    async def start(self) -> None:
        """設定検証後、必要なコマンド・処理などを準備する。"""
        ...

    async def stop(self) -> None:
        """タイマー・コマンドなど、この機能の処理を片付ける。"""
        ...


FeatureFactory = Callable[[GuildConfig], Feature]


class FeatureRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, FeatureFactory] = {}

    def register(self, name: str, factory: FeatureFactory) -> None:
        if name not in FEATURE_NAMES:
            raise ValueError("Unknown feature name")
        if name in self._factories:
            raise ValueError("Feature is already registered")
        self._factories[name] = factory

    def get(self, name: str) -> FeatureFactory | None:
        return self._factories.get(name)


class FeatureManager:
    def __init__(self, config: AppConfig, registry: FeatureRegistry) -> None:
        self._config = config
        self._registry = registry
        self._active: dict[tuple[int, str], Feature] = {}
        self._started = False
        self._closed = False

    def is_active(self, guild_id: int | None, feature: str) -> bool:
        if guild_id is None:
            return False
        return (guild_id, feature) in self._active

    def get_active(
        self,
        guild_id: int | None,
        feature: str,
    ) -> Feature | None:
        if guild_id is None:
            return None
        return self._active.get((guild_id, feature))

    async def start(self) -> None:
        # 再接続などで再度呼ばれても、二重起動しない。
        if self._started or self._closed:
            return
        self._started = True

        for guild in self._config.guilds.values():
            for name in sorted(guild.enabled_features):
                factory = self._registry.get(name)
                if factory is None:
                    log_event(
                        name,
                        "implementation_missing",
                        guild_id=guild.guild_id,
                        level=logging.ERROR,
                    )
                    continue

                feature: Feature | None = None
                try:
                    feature = factory(guild)
                    await feature.start()
                except Exception as error:
                    log_event(
                        name,
                        "startup_failed",
                        guild_id=guild.guild_id,
                        level=logging.ERROR,
                        error=error,
                    )
                    # 起動途中で作ったタイマーなども、可能な限り片付ける。
                    if feature is not None:
                        await self._stop_feature(guild.guild_id, name, feature)
                    continue

                self._active[(guild.guild_id, name)] = feature
                log_event(name, "started", guild_id=guild.guild_id)

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True

        # 停止に入った機能への新しい操作を許可しない。
        active = list(self._active.items())
        self._active.clear()

        for (guild_id, name), feature in reversed(active):
            await self._stop_feature(guild_id, name, feature)

    async def _stop_feature(
        self,
        guild_id: int,
        name: str,
        feature: Feature,
    ) -> None:
        try:
            await feature.stop()
        except Exception as error:
            log_event(
                name,
                "shutdown_failed",
                guild_id=guild_id,
                level=logging.ERROR,
                error=error,
            )
        else:
            log_event(name, "stopped", guild_id=guild_id)
