"""Wanbotの起動と、機能のライフサイクル管理。"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from datetime import datetime, timezone
from uuid import uuid4

import discord

from wanbot.bump.events import BumpEvents
from wanbot.bump.feature import BumpFeature
from wanbot.commands import WanbotCommandTree, register_status_command
from wanbot.config import AppConfig, ConfigError, GuildConfig
from wanbot.connection import ConnectionCheckClient
from wanbot.dm_forward import DMForwarder
from wanbot.features.registry import FeatureManager, FeatureRegistry
from wanbot.logging_config import log_event
from wanbot.leave_log.feature import LeaveLogFeature
from wanbot.leave_log.raw_event import capture_raw_departure
from wanbot.recruitment.feature import RecruitmentFeature
from wanbot.valorant.feature import ValorantDiagnosisFeature
from wanbot.welcome.feature import WelcomeFeature


def build_registry(
    client: WanbotClient,
    config: AppConfig,
) -> FeatureRegistry:
    registry = FeatureRegistry()

    def create_diagnosis(guild: GuildConfig) -> ValorantDiagnosisFeature:
        return ValorantDiagnosisFeature(client, config, guild)

    def create_bump(guild: GuildConfig) -> BumpFeature:
        return BumpFeature(client, config, guild)

    def create_recruitment(guild: GuildConfig) -> RecruitmentFeature:
        return RecruitmentFeature(client, config, guild)

    def create_welcome(guild: GuildConfig) -> WelcomeFeature:
        return WelcomeFeature(client, config, guild)

    def create_leave_log(guild: GuildConfig) -> LeaveLogFeature:
        return LeaveLogFeature(client, config, guild)

    registry.register("leave_log", create_leave_log)
    registry.register("welcome", create_welcome)
    registry.register("recruitment", create_recruitment)
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
        self._welcome_startup_complete = asyncio.Event()
        self._dm_forwarder = DMForwarder(
            self,
            config,
            is_available=lambda: (
                self._features_started
                and self.is_ready()
                and not self.is_closed()
                and not self.connection_check_failed
            ),
        )

        self.tree = WanbotCommandTree(self, fallback_to_global=False)
        register_status_command(self.tree, self, config)

    async def on_voice_state_update(self, member, before, after):
        feature = self.feature_manager.get_active(member.guild.id, "welcome")
        if isinstance(feature, WelcomeFeature):
            feature.handle_voice_change(before, after)

    async def on_disconnect(self):
        for guild_id in self.config.guilds:
            feature = self.feature_manager.get_active(guild_id, "welcome")
            if isinstance(feature, WelcomeFeature):
                feature.reset_voice_cleanup()
        await super().on_disconnect()

    async def on_guild_unavailable(self, guild):
        feature = self.feature_manager.get_active(guild.id, "welcome")
        if isinstance(feature, WelcomeFeature):
            feature.reset_voice_cleanup()

    async def on_raw_member_remove(
        self,
        payload: discord.RawMemberRemoveEvent,
    ) -> None:
        guild_id = payload.guild_id
        config = self.config.guild_for(guild_id)

        if (
            config is not None
            and config.feature_enabled("leave_log")
            and not self.is_closed()
        ):
            try:
                # 起動待ちや通信の前に、退出時点の情報をコピーする。
                snapshot = capture_raw_departure(
                    payload,
                    event_id=uuid4().hex,
                    observed_at=datetime.now(timezone.utc),
                )
                await self._welcome_startup_complete.wait()
                if self.feature_available(guild_id, "leave_log"):
                    feature = self.feature_manager.get_active(
                        guild_id, "leave_log"
                    )
                    if isinstance(feature, LeaveLogFeature):
                        await feature.record(snapshot)
            except Exception as error:
                log_event(
                    "leave_log",
                    "departure_capture_failed",
                    guild_id=guild_id,
                    level=logging.ERROR,
                    error=error,
                )

        # 退出記録の失敗で、Welcomeの終了処理を飛ばさない。
        await self._archive_departed_welcome(payload)

    async def _archive_departed_welcome(
        self,
        payload: discord.RawMemberRemoveEvent,
    ) -> None:
        guild_id = payload.guild_id
        guild = self.config.guild_for(guild_id)

        if (
            payload.user.bot
            or guild is None
            or not guild.feature_enabled("welcome")
            or self.is_closed()
        ):
            return

        await self._welcome_startup_complete.wait()

        if not self.feature_available(guild_id, "welcome"):
            return

        feature = self.feature_manager.get_active(guild_id, "welcome")
        if not isinstance(feature, WelcomeFeature):
            return

        # キャッシュにMemberがない場合も、退出処理に必要な情報を渡す。
        member = SimpleNamespace(
            id=payload.user.id,
            bot=payload.user.bot,
            guild=SimpleNamespace(id=guild_id),
        )

        try:
            closed = await feature.member_left(member)
        except Exception as error:
            log_event(
                "welcome",
                "member_left_archive_failed",
                guild_id=guild_id,
                level=logging.ERROR,
                error=error,
            )
            return

        if closed is not None:
            log_event(
                "welcome",
                "member_left_archived",
                guild_id=guild_id,
            )

    async def on_member_join(self, member: discord.Member) -> None:
        guild_id = member.guild.id
        guild = self.config.guild_for(guild_id)

        if (
            member.bot
            or guild is None
            or not guild.feature_enabled("welcome")
            or self.is_closed()
        ):
            return

        # 起動準備中の参加通知も、準備完了後に処理する。
        await self._welcome_startup_complete.wait()

        if not self.feature_available(guild_id, "welcome"):
            return

        feature = self.feature_manager.get_active(guild_id, "welcome")
        if not isinstance(feature, WelcomeFeature):
            return

        try:
            await feature.create(member)
        except Exception as error:
            # 1人分の作成失敗で、Bot全体を停止させない。
            # 名前や例外本文は一般ログへ出力しない。
            log_event(
                "welcome",
                "automatic_creation_failed",
                guild_id=guild_id,
                level=logging.ERROR,
                error=error,
            )
            return

        log_event(
            "welcome",
            "automatic_creation_completed",
            guild_id=guild_id,
        )

    async def _update_welcome_rules(self, payload):
        guild_id = payload.guild_id
        guild = self.config.guild_for(guild_id)
        if (
            guild is None
            or not guild.feature_enabled("welcome")
            or payload.channel_id != guild.ids.get("RULES_CHANNEL_ID")
            or payload.message_id != guild.ids.get("RULES_MESSAGE_ID")
            or self.is_closed()
        ):
            return

        await self._welcome_startup_complete.wait()
        if not self.feature_available(guild_id, "welcome"):
            return

        feature = self.feature_manager.get_active(guild_id, "welcome")
        if not isinstance(feature, WelcomeFeature):
            return

        try:
            await feature.notify_rules_change(
                getattr(payload, "user_id", None)
            )
        except Exception as error:
            log_event(
                "welcome",
                "rules_checklist_refresh_failed",
                guild_id=guild_id,
                level=logging.ERROR,
                error=error,
            )

    async def on_raw_reaction_add(self, payload):
        await self._update_welcome_rules(payload)

    async def on_raw_reaction_remove(self, payload):
        await self._update_welcome_rules(payload)

    async def on_raw_reaction_clear(self, payload):
        await self._update_welcome_rules(payload)

    async def on_raw_reaction_clear_emoji(self, payload):
        await self._update_welcome_rules(payload)

    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            if self.config.dm_forward_enabled:
                await self._welcome_startup_complete.wait()
                await self._dm_forwarder.handle(message)
            return

        await self._bump_events.on_message(message)

        guild_id = message.guild.id if message.guild is not None else None
        guild = self.config.guild_for(guild_id)

        if (
            guild is not None
            and guild.feature_enabled("welcome")
            and self.feature_available(guild_id, "welcome")
        ):
            welcome = self.feature_manager.get_active(guild_id, "welcome")
            if isinstance(welcome, WelcomeFeature):
                try:
                    await welcome.handle_welcome_message(message)
                except Exception as error:
                    log_event(
                        "welcome",
                        "welcome_message_refresh_failed",
                        guild_id=guild_id,
                        level=logging.ERROR,
                        error=error,
                    )

        if (
            guild is None
            or not guild.feature_enabled("recruitment")
            or message.channel.id != guild.ids.get("VALO_RECRUIT_CHANNEL_ID")
            or not self.feature_available(guild_id, "recruitment")
        ):
            return

        feature = self.feature_manager.get_active(guild_id, "recruitment")
        if isinstance(feature, RecruitmentFeature):
            await feature.handle_message(message)

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
            self._welcome_startup_complete.set()
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
        # 終了中に待機中の参加処理が動き出さないようにする。
        self.connection_check_failed = True
        self._welcome_startup_complete.set()
        try:
            await self.feature_manager.stop()
        finally:
            await super().close()


async def run_bot(config: AppConfig) -> int:
    async with WanbotClient(config) as client:
        await client.start(config.discord_token)
        return 1 if client.connection_check_failed else 0
