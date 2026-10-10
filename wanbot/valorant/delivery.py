"""診断の説明・質問・保存済みの結果をDMで送る。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone

import discord

from wanbot.logging_config import log_event
from wanbot.valorant.presentation import INTRODUCTION, format_result
from wanbot.valorant.question_view import (
    AnswerHandler,
    QuestionView,
    format_question,
)
from wanbot.valorant.service import DiagnosisProgress, DiagnosisService
from wanbot.valorant.session import SessionError, SessionStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DiagnosisDelivery:
    def __init__(
        self,
        guild_id: int,
        service: DiagnosisService,
        *,
        on_answer: AnswerHandler,
        is_available: Callable[[], bool],
        timeout_seconds: int,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("制限時間は正の整数の秒数で指定してください。")

        self._guild_id = guild_id
        self._service = service
        self._on_answer = on_answer
        self._is_available = is_available
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._lock = asyncio.Lock()

    def _check_available(self) -> None:
        if not self._is_available():
            raise SessionError("診断機能は利用できません。")

    def _check_member(self, member: discord.Member) -> None:
        if member.guild.id != self._guild_id or member.bot:
            raise SessionError("診断の対象者が不正です。")

    async def _show(
        self,
        member: discord.Member,
        progress: DiagnosisProgress,
        *,
        created: bool = False,
    ) -> None:
        self._check_available()

        if progress.result is not None:
            await member.send(
                format_result(progress.result),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        session = progress.session
        if session.status is not SessionStatus.ACTIVE:
            raise SessionError("この診断は期限切れです。")

        view = QuestionView(session, self._on_answer)
        try:
            await member.send(
                format_question(session),
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            view.stop()
            if created:
                await self._service.cancel_unanswered(
                    user_id=member.id,
                    session_id=session.session_id,
                    now=self._clock(),
                )
            raise
        except BaseException:
            view.stop()
            raise

    async def send(self, member: discord.Member, invoker_id: int) -> None:
        self._check_member(member)

        async with self._lock:
            self._check_available()
            progress = await self._service.resume(
                member.id,
                now=self._clock(),
            )

            created = False
            if (
                progress is None
                or progress.session.status is SessionStatus.EXPIRED
            ):
                await member.send(
                    INTRODUCTION,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self._check_available()
                progress = await self._service.start(
                    user_id=member.id,
                    invoker_id=invoker_id,
                    timeout_seconds=self._timeout_seconds,
                    now=self._clock(),
                )
                created = True

            await self._show(member, progress, created=created)
            log_event(
                "valorant_diagnosis",
                "diagnosis_dm_sent" if created else "diagnosis_dm_resent",
                guild_id=self._guild_id,
                level=logging.INFO,
            )

    async def resend(self, member: discord.Member, invoker_id: int) -> None:
        """新しい診断は作らず、現在の質問か最新の結果を再表示する。"""
        self._check_member(member)

        async with self._lock:
            self._check_available()
            progress = await self._service.resume(
                member.id,
                now=self._clock(),
            )

            if progress is not None:
                # 期限切れの進行データは再開しない。
                await self._show(member, progress)
            else:
                result = await self._service.latest_result(member.id)
                if result is None:
                    raise SessionError("再表示できる診断はありません。")

                self._check_available()
                await member.send(
                    "【保存済みの診断結果・再表示】\n\n" + format_result(result),
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            log_event(
                "valorant_diagnosis",
                "diagnosis_dm_resent",
                guild_id=self._guild_id,
                level=logging.INFO,
            )
