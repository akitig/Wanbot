"""Welcome内で診断の質問・結果を一つの投稿に表示する。"""

from datetime import datetime, timezone

import discord

from wanbot.welcome.input_activity import finish_input, track_input

from wanbot.storage.async_store import AsyncGuildJsonStore
from wanbot.valorant.presentation import format_result
from wanbot.valorant.question_view import QuestionView, format_question
from wanbot.valorant.session import SessionError, SessionStatus
from wanbot.welcome.valorant_notices import send_diagnosis_notice
from wanbot.welcome.questionnaire_panel_repository import (
    QuestionnairePanelRepository,
)


class WelcomeDiagnosisPanelRepository(QuestionnairePanelRepository):
    def __init__(self, config, guild_id):
        self._store = AsyncGuildJsonStore(
            config, guild_id, "welcome_valorant_panels"
        )


class WelcomeDiagnosisPanel:
    def __init__(
        self,
        *,
        client,
        context,
        welcome_repository,
        questionnaire_repository,
        panel_repository,
        service,
        lock,
        timeout_seconds,
        clock=None,
    ):
        self._client = client
        self._context = context
        self._welcome = welcome_repository
        self._questions = questionnaire_repository
        self._panels = panel_repository
        self._service = service
        self._lock = lock
        self._timeout_seconds = timeout_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._view = None
        self._closed = False
        self._on_completed = None

    async def _check_active(self):
        if self._closed or not self._context.is_available():
            raise SessionError("Welcome diagnosis is unavailable")

        session = await self._welcome.get_active(self._context.user_id)
        if (
            session is None
            or session.state != "active"
            or session.session_id != self._context.session_id
            or session.user_id != self._context.user_id
            or session.channel_id != self._context.channel_id
        ):
            raise SessionError("Welcome session does not match")

        snapshot = await self._questions.get(self._context.session_id)
        if (
            snapshot is None
            or snapshot.session_id != self._context.session_id
            or snapshot.user_id != self._context.user_id
            or snapshot.progress.status != "completed"
            or snapshot.progress.plays_valo is not True
        ):
            raise SessionError("Welcome questionnaire is not eligible")

    @property
    def _marker(self):
        return f"wanbot:welcome:valorant:{self._context.session_id}"

    async def _channel(self):
        channel = await self._client.fetch_channel(self._context.channel_id)
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.id != self._context.channel_id
            or channel.guild.id != self._context.guild_id
            or channel.topic != (
                f"wanbot:welcome:{self._context.guild_id}:"
                f"{self._context.session_id}"
            )
            or self._client.user is None
        ):
            raise SessionError("Invalid Welcome diagnosis channel")
        return channel

    def _matches(self, message):
        return (
            message.author.id == self._client.user.id
            and message.channel.id == self._context.channel_id
            and message.webhook_id is None
            and any(
                embed.footer.text == self._marker
                for embed in message.embeds
            )
        )

    async def _publish(self, progress):
        channel = await self._channel()
        message_id = await self._panels.get(self._context.session_id)
        message = None

        if message_id is not None:
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound as error:
                if error.code != 10008:
                    raise
            if message is not None and not self._matches(message):
                raise SessionError("Saved diagnosis message does not match")

        if message is None:
            # 投稿成功後、ID保存前に停止した場合も同じ投稿を見つける。
            async for candidate in channel.history(limit=None):
                if self._matches(candidate):
                    message = candidate
                    break

        await self._check_active()

        view = None
        if progress.result is not None:
            finish_input(self._context, "diagnosis")
            content = format_result(progress.result)
        else:
            content = format_question(progress.session)
            view = QuestionView(progress.session, self.on_answer)
            track_input(self._context, view, "diagnosis", 600)

        embed = discord.Embed(title="WelcomeのVALORANT診断")
        embed.set_footer(text=self._marker)

        try:
            if message is None:
                message = await channel.send(
                    content=content,
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await message.edit(
                    content=content,
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            await self._panels.save(self._context.session_id, message.id)
        except BaseException:
            if view is not None:
                view.stop()
            raise

        if self._view is not None:
            self._view.stop()
        self._view = view
        if progress.result is not None and self._on_completed is not None:
            await self._on_completed()
        return message.id

    def set_completion_handler(self, handler):
        self._on_completed = handler

    async def load_progress(self):
        """今回のWelcome専用の診断状態を読み出す。"""
        if self._closed:
            raise SessionError("Welcome diagnosis is unavailable")
        return await self._service.resume(
            self._context.user_id, now=self._clock()
        )

    async def begin(self):
        """開始・再開・結果の再表示。呼び出し元で本人確認を行う。"""
        async with self._lock:
            await self._check_active()
            progress = await self._service.resume(
                self._context.user_id, now=self._clock()
            )
            if progress is None:
                progress = await self._service.start(
                    user_id=self._context.user_id,
                    invoker_id=self._context.user_id,
                    timeout_seconds=self._timeout_seconds,
                    now=self._clock(),
                )

            if progress.session.status is SessionStatus.EXPIRED:
                # 回答済みの内容を捨てて、勝手にやり直しにはしない。
                raise SessionError("Welcome diagnosis has expired")

            return await self._publish(progress)

    async def on_answer(self, interaction, selection):
        try:
            await self._answer_checked(interaction, selection)
        except SessionError as error:
            await send_diagnosis_notice(interaction, error)

    async def _answer_checked(self, interaction, selection):
        if not await self._context.check(interaction):
            return

        await interaction.response.defer(thinking=False)

        async with self._lock:
            await self._check_active()
            message_id = await self._panels.get(self._context.session_id)
            if (
                selection.guild_id != self._context.guild_id
                or selection.user_id != self._context.user_id
                or interaction.message is None
                or interaction.message.id != message_id
            ):
                raise SessionError("Diagnosis answer source does not match")

            progress = await self._service.answer(
                user_id=self._context.user_id,
                session_id=selection.session_id,
                actor_id=interaction.user.id,
                question_id=selection.question_id,
                choice_id=selection.choice_id,
                now=self._clock(),
            )
            await self._publish(progress)

    def close(self):
        self._closed = True
        if self._view is not None:
            self._view.stop()
            self._view = None
