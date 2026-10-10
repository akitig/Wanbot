"""Discordに表示する質問文と回答ボタン。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import discord

from wanbot.logging_config import log_event
from wanbot.valorant.session import DiagnosisSession


@dataclass(frozen=True)
class AnswerSelection:
    guild_id: int
    user_id: int
    session_id: str
    question_id: str
    choice_id: str


AnswerHandler = Callable[
    [discord.Interaction, AnswerSelection],
    Awaitable[None],
]


def format_question(session: DiagnosisSession) -> str:
    question = session.current_question
    if question is None:
        raise ValueError("進行中の診断だけ質問を表示できます。")

    number = len(session.answers) + 1
    total = len(session.question_set.questions)
    deadline = int(session.expires_at.timestamp())

    lines = [
        f"**VALORANT診断（{number} / {total}）**",
        "",
        question.text,
        "",
    ]
    for choice in question.choices:
        lines.extend([f"**{choice.id.upper()}**：{choice.text}", ""])

    lines.extend(
        [
            "下のボタンから回答してください。回答後の変更はできません。",
            f"回答期限：<t:{deadline}:F>（<t:{deadline}:R>）",
        ]
    )

    content = "\n".join(lines)
    if len(content) > 2000:
        raise ValueError("質問文がDiscordのメッセージ上限を超えています。")
    return content


async def _notice(
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


class _AnswerButton(discord.ui.Button):
    def __init__(
        self,
        owner: QuestionView,
        selection: AnswerSelection,
    ) -> None:
        super().__init__(
            label=selection.choice_id.upper(),
            style=discord.ButtonStyle.primary,
            custom_id=(
                f"wanbot:valo:{selection.session_id}:"
                f"{selection.question_id}:{selection.choice_id}"
            ),
            row=0,
        )
        self._owner = owner
        self._selection = selection

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._owner.handle_answer(interaction, self._selection)


class QuestionView(discord.ui.View):
    def __init__(
        self,
        session: DiagnosisSession,
        on_answer: AnswerHandler,
    ) -> None:
        question = session.current_question
        if question is None:
            raise ValueError("進行中の診断だけボタンを作成できます。")

        # View独自のタイマーではなく、保存された回答期限を使用する。
        super().__init__(timeout=None)
        self._guild_id = session.guild_id
        self._user_id = session.user_id
        self._on_answer = on_answer

        for choice in question.choices:
            selection = AnswerSelection(
                guild_id=session.guild_id,
                user_id=session.user_id,
                session_id=session.session_id,
                question_id=question.id,
                choice_id=choice.id,
            )
            self.add_item(_AnswerButton(self, selection))

    async def handle_answer(
        self,
        interaction: discord.Interaction,
        selection: AnswerSelection,
    ) -> None:
        if interaction.user.id != self._user_id:
            await _notice(interaction, "この診断は本人だけが回答できます。")
            return

        # DMはguild_idがNone。サーバー内なら診断元と一致させる。
        if interaction.guild_id not in (None, self._guild_id):
            await _notice(interaction, "このサーバーでは回答できません。")
            return

        # 保存済みの状態・期限・質問順の確認は、診断サービス側で行う。
        await self._on_answer(interaction, selection)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        log_event(
            "valorant_diagnosis",
            "answer_interaction_failed",
            guild_id=self._guild_id,
            level=logging.ERROR,
            error=error,
        )
        try:
            await _notice(
                interaction,
                "診断の処理を完了できませんでした。管理者にお知らせください。",
            )
        except discord.HTTPException as notification_error:
            log_event(
                "valorant_diagnosis",
                "error_notification_failed",
                guild_id=self._guild_id,
                level=logging.ERROR,
                error=notification_error,
            )
