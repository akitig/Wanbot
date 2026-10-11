"""質問画面と回答保存を接続する。"""

import logging

import discord

from wanbot.logging_config import log_event
from wanbot.welcome.questionnaire import QuestionnaireError, TEXT_LIMITS
from wanbot.welcome.questionnaire_forms import (
    LABELS,
    EditAnswersView,
    QuestionView,
    QuestionnaireStartView,
)
from wanbot.welcome.questionnaire_repository import QuestionnaireConflict


class QuestionnaireUnavailable(ValueError):
    """この案内では回答を受け付けられない。"""


class QuestionnaireController:
    def __init__(
        self,
        *,
        context,
        welcome_repository,
        questionnaire_repository,
        lock,
        on_completed=None,
    ):
        self.context = context
        self._welcome = welcome_repository
        self._questions = questionnaire_repository
        self._on_completed = on_completed
        # Welcomeの作成・退出処理と、同じロックを渡す。
        self._lock = lock
        self._closed = False
        self._question_view = None
        self.start_view = QuestionnaireStartView(
            context,
            on_begin=self.begin,
        )

    async def _active_session(self):
        if self._closed or not self.context.is_available():
            raise QuestionnaireUnavailable("Questionnaire is unavailable")

        session = await self._welcome.get_active(self.context.user_id)
        if (
            session is None
            or session.session_id != self.context.session_id
            or session.user_id != self.context.user_id
            or session.channel_id != self.context.channel_id
            or session.state != "active"
        ):
            raise QuestionnaireUnavailable("Welcome is not active")

        return session

    async def begin(self, interaction):
        # 開始ボタン側で応答を保留してから呼ばれる。
        if not await self.context.check(interaction):
            return

        try:
            async with self._lock:
                session = await self._active_session()
                snapshot = await self._questions.ensure(session)
                await self._share_completed(snapshot)
            await self._render(interaction, snapshot)
        except Exception as error:
            await self._handle_error(interaction, error)

    async def answer(self, interaction, question, value, revision):
        # フォーム・選択メニュー側で応答を保留してから呼ばれる。
        if not await self.context.check(interaction):
            return

        try:
            async with self._lock:
                await self._active_session()
                snapshot = await self._questions.submit(
                    self.context.session_id,
                    actor_id=interaction.user.id,
                    expected_revision=revision,
                    question=question,
                    value=value,
                )
                await self._share_completed(snapshot)
            await self._render(interaction, snapshot)
        except Exception as error:
            await self._handle_error(interaction, error)

    async def show_edit_menu(self, interaction):
        if not await self.context.check(interaction):
            return

        try:
            async with self._lock:
                session = await self._active_session()
                snapshot = await self._questions.ensure(session)

            if not any(
                getattr(snapshot.progress, field) is not None
                for field in TEXT_LIMITS
            ):
                await self._render(interaction, snapshot)
                return

            self._stop_question_view()
            await interaction.edit_original_response(
                content="修正する自由入力の項目を選んでください。",
                embed=None,
                view=self._make_edit_view(snapshot),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as error:
            await self._handle_error(interaction, error)

    async def edit_text(self, interaction, question, value, revision):
        if not await self.context.check(interaction):
            return

        try:
            async with self._lock:
                await self._active_session()
                snapshot = await self._questions.edit(
                    self.context.session_id,
                    actor_id=interaction.user.id,
                    expected_revision=revision,
                    question=question,
                    value=value,
                )
                await self._share_completed(snapshot)
            await self._render(interaction, snapshot)
        except Exception as error:
            await self._handle_error(interaction, error)

    def _make_edit_view(self, snapshot):
        view = EditAnswersView(
            self.context,
            snapshot,
            on_edit=self.edit_text,
            on_back=self.begin,
        )
        self._question_view = view
        return view

    async def _share_completed(self, snapshot):
        if (
            snapshot.progress.status == "completed"
            and self._on_completed is not None
        ):
            await self._on_completed(snapshot)

    def _stop_question_view(self):
        if self._question_view is not None:
            self._question_view.stop()
            self._question_view = None

    async def _render(self, interaction, snapshot):
        if self._closed:
            return
        self._stop_question_view()
        progress = snapshot.progress

        if progress.status == "age_review_required":
            await interaction.edit_original_response(
                content=(
                    "年齢についての回答を受け付けました。"
                    "運営からの案内をお待ちください。"
                ),
                embed=None,
                view=self._make_edit_view(snapshot),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if progress.status == "completed":
            embed = discord.Embed(
                title="参加者への質問：回答内容",
                colour=discord.Colour.green(),
            )
            genders = {
                "female": "女性",
                "male": "男性",
                "no_answer": "回答しない",
            }
            fields = (
                ("名前", progress.name),
                ("性別", genders[progress.gender]),
                ("25歳以上ですか？", "Yes"),
                ("メインのゲーム", progress.main_game),
                ("ひとこと", progress.comment),
                ("リアルの趣味", progress.hobby),
                ("VALORANTをやりますか？", "Yes" if progress.plays_valo else "No"),
            )
            for label, value in fields:
                embed.add_field(name=label, value=value, inline=False)

            await interaction.edit_original_response(
                content=(
                    "回答を保存しました。続く案内をお待ちください。"
                    "\n自由入力は下のメニューから修正できます。"
                ),
                embed=embed,
                view=self._make_edit_view(snapshot),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        question = progress.next_question
        view = QuestionView(
            self.context,
            snapshot,
            on_answer=self.answer,
            on_edit=self.show_edit_menu,
        )
        self._question_view = view

        message = f"**{LABELS[question]}**"
        if question == "age_confirmation":
            message += (
                "\nこのサーバーは25歳以上の方が対象です。"
                "\n入力を間違えた場合は、訂正する選択肢を選んでください。"
            )
        if question in ("name", "main_game", "comment", "hobby"):
            message += "\n「入力する」を押して回答してください。"
        else:
            message += "\nメニューから選んでください。"

        await interaction.edit_original_response(
            content=message,
            embed=None,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _handle_error(self, interaction, error):
        self._stop_question_view()

        if isinstance(error, QuestionnaireUnavailable):
            message = "この案内では現在、回答を受け付けていません。"
        elif isinstance(error, QuestionnaireConflict):
            message = (
                "回答の状態が変わりました。"
                "Welcomeの「回答を始める・再開する」から再開してください。"
            )
        elif isinstance(error, QuestionnaireError):
            message = (
                "入力内容を確認してください。"
                "Welcomeの「回答を始める・再開する」から入力し直せます。"
            )
        else:
            log_event(
                "welcome",
                "questionnaire_operation_failed",
                guild_id=self.context.guild_id,
                level=logging.ERROR,
                error=error,
            )
            message = (
                "処理を完了できませんでした。"
                "Welcomeの「回答を始める・再開する」から"
                "保存済みの回答を確認してください。"
            )

        await interaction.edit_original_response(
            content=message,
            embed=None,
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    def close(self):
        self._closed = True
        self._stop_question_view()
        self.start_view.stop()
