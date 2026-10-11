from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from wanbot.config import AppConfig, ConfigError, GuildConfig
from wanbot.logging_config import log_event
from wanbot.welcome.archive import WelcomeArchiveChannel
from wanbot.welcome.channels import WelcomeChannelError, WelcomeChannels
from wanbot.welcome.commands import register_welcome_command
from wanbot.welcome.lifecycle import WelcomeLifecycle
from wanbot.welcome.questionnaire_manager import WelcomeQuestionnaires
from wanbot.welcome.questionnaire_panel_repository import (
    QuestionnairePanelRepository,
)
from wanbot.welcome.questionnaire_repository import QuestionnaireRepository
from wanbot.welcome.repository import WelcomeRepository, WelcomeSession

if TYPE_CHECKING:
    from wanbot.bot import WanbotClient


ARCHIVE_RETRY_SECONDS = 30.0


class WelcomeFeature:
    def __init__(
        self,
        client: WanbotClient,
        config: AppConfig,
        guild: GuildConfig,
    ) -> None:
        if not guild.feature_enabled("welcome"):
            raise ConfigError("Welcome機能が有効になっていません。")

        self._client = client
        self._config = config
        self._guild_id = guild.guild_id
        self._started = False
        self._closed = False
        self._registered = False
        self._pending: set[asyncio.Task] = set()
        self._worker: asyncio.Task | None = None
        self._reconcile_needed = True

        self._repository = WelcomeRepository(config, guild.guild_id)
        self._channels = WelcomeChannels(
            client=client,
            guild_config=guild,
            repository=self._repository,
        )
        self._archive = WelcomeArchiveChannel(
            client=client,
            guild_config=guild,
        )
        self._lifecycle = WelcomeLifecycle(
            guild_id=guild.guild_id,
            repository=self._repository,
            channels=self._channels,
            archive=self._archive,
        )
        self._question_repository = QuestionnaireRepository(
            config, guild.guild_id
        )
        self._question_panel_repository = QuestionnairePanelRepository(
            config, guild.guild_id
        )
        self._questionnaires = WelcomeQuestionnaires(
            client=client,
            guild_id=guild.guild_id,
            welcome_repository=self._repository,
            questionnaire_repository=self._question_repository,
            panel_repository=self._question_panel_repository,
            lock=self._lifecycle.operation_lock,
            is_available=self._available,
        )

    def _available(self) -> bool:
        return (
            self._started
            and not self._closed
            and self._client.feature_available(self._guild_id, "welcome")
        )

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("Welcome feature is closed")
        if self._started:
            return

        try:
            register_welcome_command(
                self._client.tree,
                self._config,
                self._guild_id,
                is_available=self._available,
                create_welcome=self.create,
            )
            self._registered = True
        except BaseException:
            await self.stop()
            raise

        self._started = True
        try:
            await self._questionnaires.refresh()
        except Exception as error:
            log_event(
                "welcome",
                "questionnaire_restore_failed",
                guild_id=self._guild_id,
                level=logging.ERROR,
                error=error,
            )

        self._worker = asyncio.create_task(
            self._archive_worker(),
            name=f"welcome-archive-worker-{self._guild_id}",
        )

    async def _run(self, operation, *, name):
        task = asyncio.create_task(
            operation,
            name=f"welcome-{name}-{self._guild_id}",
        )
        self._pending.add(task)
        task.add_done_callback(self._completed)
        return await asyncio.shield(task)

    async def create(self, member: discord.Member) -> WelcomeSession:
        if not self._available():
            raise WelcomeChannelError("Welcome is unavailable")

        return await self._run(
            self._create_with_questions(member),
            name="create",
        )

    async def _create_with_questions(self, member):
        session = await self._lifecycle.ensure(member)
        await self._questionnaires.ensure(session)
        return session

    async def member_left(self, member) -> WelcomeSession | None:
        if not self._available():
            raise WelcomeChannelError("Welcome is unavailable")

        return await self._run(
            self._lifecycle.member_left(member),
            name="archive",
        )

    async def retry_archives(self):
        if not self._available():
            return None

        return await self._run(
            self._lifecycle.resume_pending(),
            name="archive-retry",
        )

    async def reconcile_members(self):
        if not self._available():
            return None

        guild = self._client.get_guild(self._guild_id)
        return await self._run(
            self._lifecycle.reconcile_members(guild),
            name="membership-reconciliation",
        )

    async def _archive_worker(self):
        while not self._closed:
            await asyncio.sleep(ARCHIVE_RETRY_SECONDS)

            if self._available():
                try:
                    await self._run(
                        self._questionnaires.refresh(),
                        name="questionnaire-refresh",
                    )
                except Exception:
                    # 保存データ読み込みなどの失敗は次の周期で再試行。
                    pass

            try:
                result = await self.retry_archives()
            except Exception:
                # 詳細は追跡中のタスクの完了処理で安全に記録する。
                # 一度失敗しても、次回の再試行を続ける。
                continue

            if result is not None and result[0] > 0:
                log_event(
                    "welcome",
                    "pending_archives_completed",
                    guild_id=self._guild_id,
                )

            if self._reconcile_needed:
                try:
                    result = await self.reconcile_members()
                except Exception:
                    # 起動後の確認も、失敗時は次の周期で再試行する。
                    continue

                if result is not None and result[1] == 0:
                    self._reconcile_needed = False
                    log_event(
                        "welcome",
                        "membership_reconciliation_completed",
                        guild_id=self._guild_id,
                    )

    def _completed(self, task: asyncio.Task) -> None:
        self._pending.discard(task)
        if task.cancelled():
            return

        error = task.exception()
        if error is not None:
            log_event(
                "welcome",
                "operation_failed",
                guild_id=self._guild_id,
                level=logging.ERROR,
                error=error,
            )

    async def stop(self) -> None:
        if self._closed:
            return

        self._closed = True
        self._started = False

        try:
            if self._registered:
                self._client.tree.remove_command(
                    "welcome",
                    guild=discord.Object(id=self._guild_id),
                )
                self._registered = False
        finally:
            try:
                if self._worker is not None:
                    self._worker.cancel()
                    await asyncio.gather(
                        self._worker,
                        return_exceptions=True,
                    )

                pending = tuple(self._pending)
                if pending:
                    await asyncio.shield(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            finally:
                try:
                    await self._questionnaires.close()
                finally:
                    try:
                        await self._question_repository.close()
                    finally:
                        try:
                            await self._question_panel_repository.close()
                        finally:
                            await self._repository.close()
