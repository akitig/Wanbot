"""自己紹介を複数項目ずつ入力するフォーム。"""

import discord

from wanbot.welcome.input_activity import track_input
from wanbot.welcome.questionnaire import TEXT_LIMITS
from wanbot.welcome.questionnaire_forms import (
    LABELS,
    SafeQuestionView,
    report_error,
)


BASIC = ("name", "gender", "adult")
PROFILE = ("main_game", "comment", "hobby", "plays_valo")


def batch_questions(progress):
    current = progress.next_question
    for group in (BASIC, PROFILE):
        if current in group:
            return group[group.index(current):]
    return ()


class QuestionnaireBatchModal(discord.ui.Modal):
    def __init__(self, context, snapshot, *, on_submit):
        questions = batch_questions(snapshot.progress)
        if not questions:
            raise ValueError("No questions available for this form")

        super().__init__(
            title=(
                "自己紹介：基本情報"
                if questions[0] in BASIC
                else "自己紹介：ゲーム・趣味"
            ),
            timeout=600,
        )
        self._context = context
        self._answer_revision = snapshot.revision
        self._submit_answers = on_submit
        self._answer_fields = []

        for question in questions:
            if question in TEXT_LIMITS:
                component = discord.ui.TextInput(
                    required=True,
                    max_length=TEXT_LIMITS[question],
                    style=(
                        discord.TextStyle.paragraph
                        if question in ("comment", "hobby")
                        else discord.TextStyle.short
                    ),
                )
            else:
                choices = (
                    (
                        ("女性", "female"),
                        ("男性", "male"),
                        ("回答しない", "no_answer"),
                    )
                    if question == "gender"
                    else (("Yes", "yes"), ("No", "no"))
                )
                component = discord.ui.Select(
                    placeholder="選んでください",
                    min_values=1,
                    max_values=1,
                    options=[
                        discord.SelectOption(label=label, value=value)
                        for label, value in choices
                    ],
                )

            self._answer_fields.append((question, component))
            self.add_item(
                discord.ui.Label(
                    text=LABELS[question],
                    component=component,
                )
            )

        track_input(
            context, self, kind="introduction", seconds=600
        )

    async def on_submit(self, interaction):
        if not await self._context.check(interaction):
            return
        await interaction.response.defer(
            ephemeral=True, thinking=True
        )
        answers = []
        for question, component in self._answer_fields:
            if isinstance(component, discord.ui.TextInput):
                value = component.value
            else:
                if len(component.values) != 1:
                    await interaction.edit_original_response(
                        content="未選択の項目があります。フォームを開き直してください。",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
                value = component.values[0]
            answers.append((question, value))

        await self._submit_answers(
            interaction, tuple(answers), self._answer_revision
        )

    async def on_error(self, interaction, error):
        await report_error(self._context, interaction, error)


class QuestionnaireBatchView(SafeQuestionView):
    def __init__(self, context, snapshot, *, on_submit, on_edit):
        super().__init__(context, timeout=600)
        self._answer_snapshot = snapshot
        self._submit_answers = on_submit
        self._edit_answers = on_edit

        button = discord.ui.Button(
            label=(
                "基本情報をまとめて入力する"
                if snapshot.progress.next_question in BASIC
                else "ゲーム・趣味をまとめて入力する"
            ),
            style=discord.ButtonStyle.primary,
        )
        button.callback = self.open_form
        self.add_item(button)

        if snapshot.revision > 0:
            edit = discord.ui.Button(
                label="回答済みの自由入力を修正する",
                style=discord.ButtonStyle.secondary,
            )
            edit.callback = self.edit_answers
            self.add_item(edit)

        track_input(
            context, self, kind="introduction", seconds=600
        )

    async def open_form(self, interaction):
        if not await self.context.check(interaction):
            return
        await interaction.response.send_modal(
            QuestionnaireBatchModal(
                self.context,
                self._answer_snapshot,
                on_submit=self._submit_answers,
            )
        )

    async def edit_answers(self, interaction):
        if not await self.context.check(interaction):
            return
        await interaction.response.defer(
            ephemeral=True, thinking=True
        )
        await self._edit_answers(interaction)
