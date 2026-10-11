"""Welcome質問のボタン・入力フォーム・選択メニュー。"""

from dataclasses import dataclass
import logging
from typing import Callable

import discord

from wanbot.logging_config import log_event
from wanbot.welcome.questionnaire import TEXT_LIMITS


LABELS = {
    "name": "名前",
    "gender": "性別",
    "adult": "25歳以上ですか？",
    "age_confirmation": "本当に25歳未満ですか？",
    "main_game": "メインのゲーム",
    "comment": "ひとこと",
    "hobby": "リアルの趣味",
    "plays_valo": "VALORANTをやりますか？",
}

CHOICES = {
    "gender": (
        ("女性", "female"),
        ("男性", "male"),
        ("回答しない", "no_answer"),
    ),
    "adult": (
        ("Yes：25歳以上です", "yes"),
        ("No：25歳未満です", "no"),
    ),
    "age_confirmation": (
        ("25歳未満です", "under_25"),
        ("入力を間違えました。25歳以上です", "adult_correction"),
    ),
    "plays_valo": (
        ("Yes：やります", "yes"),
        ("No：やりません", "no"),
    ),
}


async def notice(interaction, message):
    kwargs = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.followup.send(message, **kwargs)
    else:
        await interaction.response.send_message(message, **kwargs)


@dataclass(frozen=True)
class QuestionnaireContext:
    guild_id: int
    channel_id: int
    user_id: int
    session_id: str
    is_available: Callable[[], bool]

    async def check(self, interaction):
        if (
            interaction.guild_id != self.guild_id
            or interaction.channel_id != self.channel_id
        ):
            await notice(
                interaction,
                "この案内のWelcomeチャンネルから操作してください。",
            )
            return False

        if interaction.user.id != self.user_id:
            await notice(
                interaction,
                "この質問に回答できるのは、案内の対象者本人だけです。",
            )
            return False

        if not self.is_available():
            await notice(
                interaction,
                "現在、この案内は操作できません。",
            )
            return False

        return True


async def report_error(context, interaction, error):
    log_event(
        "welcome",
        "questionnaire_ui_failed",
        guild_id=context.guild_id,
        level=logging.ERROR,
        error=error,
    )
    await notice(
        interaction,
        "操作を完了できませんでした。"
        "Welcomeの「回答を始める・再開する」から確認してください。",
    )


class SafeQuestionView(discord.ui.View):
    def __init__(self, context, *, timeout):
        super().__init__(timeout=timeout)
        self.context = context

    async def on_error(self, interaction, error, item):
        await report_error(self.context, interaction, error)


class QuestionnaireStartView(SafeQuestionView):
    def __init__(self, context, *, on_begin):
        super().__init__(context, timeout=None)
        self._on_begin = on_begin

        button = discord.ui.Button(
            label="回答を始める・再開する",
            style=discord.ButtonStyle.primary,
            custom_id=f"wanbot:welcome:questions:{context.session_id}",
        )
        button.callback = self.begin
        self.add_item(button)

    async def begin(self, interaction):
        if not await self.context.check(interaction):
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._on_begin(interaction)


class TextQuestionModal(discord.ui.Modal):
    def __init__(
        self,
        context,
        *,
        question,
        revision,
        on_answer,
    ):
        if question not in TEXT_LIMITS:
            raise ValueError("Not a text question")

        super().__init__(title=LABELS[question], timeout=300)
        self.context = context
        self.question = question
        self.revision = revision
        self._on_answer = on_answer

        self.answer_input = discord.ui.TextInput(
            label=LABELS[question],
            required=True,
            min_length=1,
            max_length=TEXT_LIMITS[question],
            style=(
                discord.TextStyle.paragraph
                if question in ("comment", "hobby")
                else discord.TextStyle.short
            ),
        )
        self.add_item(self.answer_input)

    async def on_submit(self, interaction):
        # フォームを開いた後に状態が変わる場合もあるため再確認する。
        if not await self.context.check(interaction):
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._on_answer(
            interaction,
            self.question,
            self.answer_input.value,
            self.revision,
        )

    async def on_error(self, interaction, error):
        await report_error(self.context, interaction, error)


class QuestionView(SafeQuestionView):
    def __init__(self, context, snapshot, *, on_answer):
        if (
            snapshot.session_id != context.session_id
            or snapshot.user_id != context.user_id
        ):
            raise ValueError("Questionnaire identity does not match")
        if snapshot.progress.status != "answering":
            raise ValueError("Questionnaire is not accepting answers")

        super().__init__(context, timeout=300)
        self.question = snapshot.progress.next_question
        self.revision = snapshot.revision
        self._on_answer = on_answer

        if self.question in TEXT_LIMITS:
            button = discord.ui.Button(
                label="入力する",
                style=discord.ButtonStyle.primary,
            )
            button.callback = self.open_modal
            self.add_item(button)
        else:
            self.selection = discord.ui.Select(
                placeholder=LABELS[self.question],
                min_values=1,
                max_values=1,
                options=[
                    discord.SelectOption(label=label, value=value)
                    for label, value in CHOICES[self.question]
                ],
            )
            self.selection.callback = self.select_answer
            self.add_item(self.selection)

    async def open_modal(self, interaction):
        if not await self.context.check(interaction):
            return

        await interaction.response.send_modal(
            TextQuestionModal(
                self.context,
                question=self.question,
                revision=self.revision,
                on_answer=self._on_answer,
            )
        )

    async def select_answer(self, interaction):
        if not await self.context.check(interaction):
            return

        values = self.selection.values
        allowed = {value for _, value in CHOICES[self.question]}
        if len(values) != 1 or values[0] not in allowed:
            await notice(interaction, "選択肢を選び直してください。")
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._on_answer(
            interaction,
            self.question,
            values[0],
            self.revision,
        )
        self.stop()
