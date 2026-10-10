"""VALORANT診断機能の起動・停止とボタン復旧。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

import discord

from wanbot.config import AppConfig, GuildConfig
from wanbot.logging_config import log_event
from wanbot.valorant.commands import register_diagnosis_command
from wanbot.valorant.controller import DiagnosisController
from wanbot.valorant.delivery import DiagnosisDelivery
from wanbot.valorant.question_view import QuestionView
from wanbot.valorant.log_commands import register_log_command
from wanbot.valorant.questions import load_questions
from wanbot.valorant.scoring import DiagnosisScorer
from wanbot.valorant.service import DiagnosisService
from wanbot.valorant.session import SessionStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ValorantDiagnosisFeature:
    def __init__(
        self,
        client,
        config: AppConfig,
        guild: GuildConfig,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._client = client
        self._config = config
        self._guild = guild
        self._clock = clock
        self._started = False
        self._closed = False
        self._command = None
        self._log_command = None
        self._restored_views: list[QuestionView] = []

        self._questions = load_questions()
        scorer = DiagnosisScorer(self._questions)
        self._service = DiagnosisService(config, guild.guild_id, scorer)

        self._controller = DiagnosisController(
            guild.guild_id,
            self._service,
            is_available=self._available,
            clock=clock,
        )
        self._delivery = DiagnosisDelivery(
            guild.guild_id,
            self._service,
            on_answer=self._controller.on_answer,
            is_available=self._available,
            timeout_seconds=guild.valorant_timeout_seconds,
            clock=clock,
        )

    def _available(self) -> bool:
        return (
            self._started
            and not self._closed
            and self._guild.feature_enabled("valorant_diagnosis")
            and self._client.feature_available(
                self._guild.guild_id,
                "valorant_diagnosis",
            )
        )

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("停止済みの診断機能は再利用できません。")
        if self._started:
            return

        try:
            sessions = await self._service.list_sessions(now=self._clock())
            for session in sessions:
                if session.status is not SessionStatus.ACTIVE:
                    continue
                view = QuestionView(session, self._controller.on_answer)
                self._restored_views.append(view)
                self._client.add_view(view)

            self._command = register_diagnosis_command(
                self._client.tree,
                self._config,
                self._guild.guild_id,
                is_available=self._available,
                send_diagnosis=self._delivery.send,
                resend_diagnosis=self._delivery.resend,
            )
            self._log_command = register_log_command(
                self._client.tree,
                self._config,
                self._guild.guild_id,
                question_set=self._questions,
                is_available=self._available,
                load_record=self._service.latest_record,
            )
        except BaseException:
            for view in self._restored_views:
                view.stop()
            self._restored_views.clear()
            raise

        self._started = True
        log_event(
            "valorant_diagnosis",
            "diagnosis_views_restored",
            guild_id=self._guild.guild_id,
        )

    async def stop(self) -> None:
        if self._closed:
            return

        self._closed = True
        self._started = False

        try:
            for view in self._restored_views:
                view.stop()
            self._restored_views.clear()

            guild_object = discord.Object(id=self._guild.guild_id)
            for name, command in (
                ("valo_role", self._command),
                ("valo_role_log", self._log_command),
            ):
                current = self._client.tree.get_command(name, guild=guild_object)
                if command is not None and current is command:
                    self._client.tree.remove_command(name, guild=guild_object)
        finally:
            await self._service.close()
