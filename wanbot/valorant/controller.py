"""回答ボタンから診断サービスを呼び、Discordの表示を更新する。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone

import discord

from wanbot.logging_config import log_event
from wanbot.storage.json_store import StorageError
from wanbot.valorant.presentation import format_result
from wanbot.valorant.question_view import (
    AnswerSelection,
    QuestionView,
    format_question,
)
from wanbot.valorant.service import DiagnosisService
from wanbot.valorant.session import SessionError


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DiagnosisController:
    """サーバーごとに1つ作り、診断サービスと組み合わせる。"""

    def __init__(
        self,
        guild_id: int,
        service: DiagnosisService,
        *,
        is_available: Callable[[], bool],
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._guild_id = guild_id
        self._service = service
        self._is_available = is_available
        self._clock = clock

        # 保存だけでなく表示更新まで順番に実行する。
        # 遅れて終わった表示更新で、質問が巻き戻るのを防ぐ。
        self._lock = asyncio.Lock()

    async def _notice(
        self,
        interaction: discord.Interaction,
        content: str,
    ) -> None:
        options = {
            "ephemeral": True,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if interaction.response.is_done():
            await interaction.followup.send(content, **options)
        else:
            await interaction.response.send_message(content, **options)

    async def on_answer(
        self,
        interaction: discord.Interaction,
        selection: AnswerSelection,
    ) -> None:
        if (
            selection.guild_id != self._guild_id
            or interaction.user.id != selection.user_id
            or interaction.guild_id not in (None, self._guild_id)
        ):
            await self._notice(
                interaction,
                "この診断には回答できません。",
            )
            return

        if not self._is_available():
            await self._notice(
                interaction,
                "現在、このサーバーの診断機能は利用できません。",
            )
            return

        # 保存処理を待つ前に、ボタン操作を受け付けたことを返す。
        # thinking=Falseにより、元の質問メッセージを更新対象にする。
        await interaction.response.defer(thinking=False)

        async with self._lock:
            # 待機中に機能が停止した場合も、保存へ進まない。
            if not self._is_available():
                await self._notice(
                    interaction,
                    "現在、このサーバーの診断機能は利用できません。",
                )
                return

            try:
                progress = await self._service.answer(
                    user_id=selection.user_id,
                    session_id=selection.session_id,
                    actor_id=interaction.user.id,
                    question_id=selection.question_id,
                    choice_id=selection.choice_id,
                    now=self._clock(),
                )
            except SessionError:
                await self._notice(
                    interaction,
                    "この回答は受け付けられません。"
                    "回答済み・期限切れ、または古い診断のボタンです。",
                )
                return
            except StorageError as error:
                log_event(
                    "valorant_diagnosis",
                    "answer_storage_failed",
                    guild_id=self._guild_id,
                    level=logging.ERROR,
                    error=error,
                )
                await self._notice(
                    interaction,
                    "保存処理を完了できませんでした。"
                    "画面は進めていません。管理者にお知らせください。",
                )
                return

            view = None
            if progress.result is not None:
                content = format_result(progress.result)
            else:
                content = format_question(progress.session)
                view = QuestionView(progress.session, self.on_answer)

            try:
                await interaction.edit_original_response(
                    content=content,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException as error:
                if view is not None:
                    view.stop()

                log_event(
                    "valorant_diagnosis",
                    "answer_display_failed",
                    guild_id=self._guild_id,
                    level=logging.ERROR,
                    error=error,
                )
                await self._notice(
                    interaction,
                    "回答は保存しましたが、表示を更新できませんでした。"
                    "管理者にお知らせください。",
                )
